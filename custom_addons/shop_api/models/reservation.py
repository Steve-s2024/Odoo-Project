import uuid
from datetime import timedelta

from odoo import _, Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


class ShopApiReservation(models.Model):
    _name = "shop.api.reservation"
    _description = "Shop API Inventory Reservation"
    _order = "create_date desc"

    name = fields.Char(string="预留编号", required=True, default=lambda self: str(uuid.uuid4()), index=True)
    client_id = fields.Many2one("shop.api.client", required=True, ondelete="restrict", index=True)
    external_id = fields.Char(string="商城预留编号", index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    website_id = fields.Many2one("website", ondelete="set null", index=True)
    partner_id = fields.Many2one("res.partner", ondelete="set null")
    state = fields.Selection(
        [("active", "有效"), ("confirmed", "已转订单"), ("released", "已释放"),
         ("expired", "已过期"), ("cancelled", "已取消")],
        default="active", required=True, index=True,
    )
    expires_at = fields.Datetime(required=True, index=True)
    confirmed_order_id = fields.Many2one("sale.order", ondelete="set null", readonly=True)
    line_ids = fields.One2many("shop.api.reservation.line", "reservation_id", string="预留商品")
    notes = fields.Text()

    _unique_name = models.Constraint("UNIQUE(name)", "库存预留编号必须唯一。")
    _unique_client_external = models.Constraint(
        "UNIQUE(client_id, external_id)",
        "同一客户端的商城预留编号必须唯一。",
    )

    @api.constrains("line_ids")
    def _check_lines(self):
        for reservation in self:
            if reservation.state == "active" and not reservation.line_ids:
                raise ValidationError(_("有效库存预留必须至少包含一个商品。"))

    @api.model
    def _configuration_for_client(self, client):
        company = client.company_ids[:1] or self.env.company
        website = client.website_ids[:1] or self.env["website"].sudo().search([
            ("company_id", "=", company.id),
        ], limit=1)
        configuration = self.env["shop.api.configuration"].sudo().search([
            ("company_id", "=", company.id),
            ("website_id", "in", [website.id, False]),
            ("active", "=", True),
        ], order="website_id desc", limit=1)
        return configuration or self.env["shop.api.configuration"].sudo()._ensure_default_configuration()

    @api.model
    def _find_product(self, item):
        product = self.env["product.product"].sudo().search([
            ("shop_api_uuid", "=", item.get("product_id")),
            ("active", "=", True),
        ], limit=1)
        if not product and item.get("sku"):
            product = self.env["product.product"].sudo().search([
                ("default_code", "=", item["sku"]), ("active", "=", True),
            ], limit=1)
        if not product:
            raise UserError(_("找不到所选商品。"))
        return product

    @api.model
    def _candidate_locations(self, company):
        roots = self.env["stock.warehouse"].sudo().search([
            ("company_id", "=", company.id),
        ]).mapped("view_location_id")
        domain = [
            ("usage", "=", "internal"),
            "|", ("company_id", "=", False), ("company_id", "=", company.id),
        ]
        if roots:
            domain.append(("id", "child_of", roots.ids))
        return self.env["stock.location"].sudo().search(domain, order="complete_name, id")

    @api.model
    def _lock_products(self, products):
        for product_id in sorted(products.ids):
            self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", [7421, product_id])

    @api.model
    def check_inventory(self, items, client, exclude_reservation=None):
        company = client.company_ids[:1] or self.env.company
        products = self.env["product.product"]
        normalized = []
        for item in items or []:
            product = self._find_product(item)
            quantity = float(item.get("quantity") or 0)
            if quantity <= 0:
                raise UserError(_("商品数量必须大于零。"))
            products |= product
            normalized.append((product, quantity))
        if not normalized:
            raise UserError(_("库存校验至少需要一个商品。"))
        self._lock_products(products)
        locations = self._candidate_locations(company)
        result = []
        for product, quantity in normalized:
            options = []
            for location in locations:
                available = self.env["stock.quant"].sudo()._get_available_quantity(
                    product, location, strict=True,
                )
                available -= self.env["shop.api.reservation.line"].sudo()._active_reserved_qty(
                    product, location, exclude_reservation=exclude_reservation,
                )
                if available > 0:
                    options.append((location, max(available, 0.0)))
            satisfying = [option for option in options if option[1] >= quantity]
            selected = sorted(satisfying, key=lambda option: (option[1], option[0].id))[:1]
            product._shop_api_ensure_uuid()
            result.append({
                "product_id": product.shop_api_uuid,
                "name": product.product_tmpl_id._get_website_display_name(False),
                "requested_quantity": quantity,
                "available": bool(selected),
                "available_quantity": max([qty for _location, qty in options] or [0.0]),
                "source_location_id": selected[0][0].id if selected else None,
            })
        return result

    @api.model
    def create_reservation(self, client, payload):
        configuration = self._configuration_for_client(client)
        company = client.company_ids[:1] or configuration.company_id
        website = client.website_ids[:1] or configuration.website_id
        items = payload.get("items") or []
        inventory = self.check_inventory(items, client)
        if any(not item["available"] for item in inventory):
            raise UserError(_("订单无法满足，请更改数量。"))
        lines = []
        for source, checked in zip(items, inventory):
            product = self._find_product(source)
            lines.append(Command.create({
                "product_id": product.id,
                "product_uom_id": product.uom_id.id,
                "quantity": float(source["quantity"]),
                "source_location_id": checked["source_location_id"],
                "available_quantity_snapshot": checked["available_quantity"],
            }))
        reservation = self.sudo().create({
            "client_id": client.id,
            "external_id": payload.get("external_id") or False,
            "company_id": company.id,
            "website_id": website.id if website else False,
            "expires_at": fields.Datetime.now() + timedelta(
                minutes=configuration.reservation_ttl_minutes
            ),
            "line_ids": lines,
        })
        return reservation

    def _shop_api_payload(self):
        self.ensure_one()
        return {
            "id": self.name,
            "authoritative": True,
            "external_id": self.external_id,
            "state": self.state,
            "expires_at": fields.Datetime.to_string(self.expires_at),
            "order_id": self.confirmed_order_id.shop_api_uuid if self.confirmed_order_id else None,
            "items": [line._shop_api_payload() for line in self.line_ids],
        }

    def action_extend(self, minutes=None):
        self.ensure_one()
        if self.state != "active" or self.expires_at <= fields.Datetime.now():
            raise UserError(_("只有尚未过期的有效库存预留可以延长。"))
        configuration = self._configuration_for_client(self.client_id)
        minutes = int(minutes or configuration.reservation_max_extension_minutes)
        if minutes <= 0 or minutes > configuration.reservation_max_extension_minutes:
            raise UserError(_("延长时间超过配置允许的范围。"))
        self.expires_at += timedelta(minutes=minutes)
        return self

    def action_release(self):
        for reservation in self:
            if reservation.state == "active":
                reservation.state = "released"
        return self

    def create_order(
        self, partner, external_id, language="zh_CN", shipping_address=None,
        shipping_method=None,
    ):
        self.ensure_one()
        if self.state != "active" or self.expires_at <= fields.Datetime.now():
            raise UserError(_("库存预留已失效，不能创建订单。"))
        self._lock_products(self.line_ids.mapped("product_id"))
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        for line in self.line_ids:
            available = self.env["stock.quant"].sudo()._get_available_quantity(
                line.product_id, line.source_location_id, strict=True,
            ) - self.env["shop.api.reservation.line"].sudo()._active_reserved_qty(
                line.product_id, line.source_location_id, exclude_reservation=self,
            )
            required = line.product_uom_id._compute_quantity(
                line.quantity, line.product_id.uom_id,
            )
            if float_compare(required, available, precision_digits=precision) > 0:
                raise UserError(_("订单无法满足，请更改数量。"))
        language = language if language in ("zh_CN", "en_US") else "zh_CN"
        configuration = self._configuration_for_client(self.client_id)
        pricelist = (
            configuration.english_pricelist_id if language == "en_US"
            else configuration.chinese_pricelist_id
        )
        currency_name = "USD" if language == "en_US" else self.company_id.currency_id.name
        if not pricelist:
            pricelist = self.env["product.pricelist"].sudo().search([
                ("currency_id.name", "=", currency_name),
                "|", ("website_id", "=", self.website_id.id), ("website_id", "=", False),
            ], limit=1)
        if not pricelist:
            raise UserError(_("找不到适用于当前商城语言的价目表。"))
        def line_price(reservation_line):
            template = reservation_line.product_id.product_tmpl_id
            if language == "en_US" and template.x_website_usd_price:
                return template.x_website_usd_price
            if language == "zh_CN":
                return template.list_price
            return pricelist._get_product_price(
                reservation_line.product_id, reservation_line.quantity,
            )

        order = self.env["sale.order"].sudo().create({
            "partner_id": partner.id,
            "partner_invoice_id": partner.id,
            "partner_shipping_id": (shipping_address or partner).id,
            "website_id": self.website_id.id or False,
            "company_id": self.company_id.id,
            "pricelist_id": pricelist.id,
            "x_platform": "separated_shop",
            "x_channel": self.client_id.code,
            "x_website_checkout_language": language,
            "order_line": [Command.create({
                "product_id": line.product_id.id,
                "product_uom_qty": line.quantity,
                "product_uom_id": line.product_uom_id.id,
                "price_unit": line_price(line),
                "x_source_location_id": line.source_location_id.id,
                "x_website_stock_reserved_until": self.expires_at,
            }) for line in self.line_ids],
        })
        if shipping_method:
            rate = shipping_method.sudo().rate_shipment(order)
            if not rate.get("success"):
                raise UserError(rate.get("error_message") or _("配送方式不可用。"))
            order.set_delivery_line(shipping_method.sudo(), rate["price"])
        order._shop_api_ensure_uuid()
        self.write({"state": "confirmed", "confirmed_order_id": order.id, "partner_id": partner.id})
        if external_id:
            self.env["shop.api.external.reference"].set_reference(
                self.client_id, "order", external_id, order,
            )
        self.env["shop.api.event"].enqueue(
            "order.created", order, order._shop_api_payload(), client=self.client_id,
        )
        return order

    @api.model
    def _cron_expire_reservations(self):
        reservations = self.sudo().search([
            ("state", "=", "active"), ("expires_at", "<=", fields.Datetime.now()),
        ])
        for reservation in reservations:
            reservation.state = "expired"
            self.env["shop.api.event"].enqueue(
                "reservation.expired", payload=reservation._shop_api_payload(),
                client=reservation.client_id,
            )


class ShopApiReservationLine(models.Model):
    _name = "shop.api.reservation.line"
    _description = "Shop API Inventory Reservation Line"
    _order = "id"

    reservation_id = fields.Many2one(
        "shop.api.reservation", required=True, ondelete="cascade", index=True,
    )
    product_id = fields.Many2one("product.product", required=True, ondelete="restrict", index=True)
    product_uom_id = fields.Many2one("uom.uom", required=True, ondelete="restrict")
    quantity = fields.Float(required=True)
    source_location_id = fields.Many2one(
        "stock.location", required=True, ondelete="restrict", index=True,
    )
    available_quantity_snapshot = fields.Float(readonly=True)

    @api.constrains("quantity")
    def _check_quantity(self):
        if any(line.quantity <= 0 for line in self):
            raise ValidationError(_("库存预留数量必须大于零。"))

    @api.model
    def _active_reserved_qty(self, product, location, exclude_reservation=None):
        domain = [
            ("product_id", "=", product.id),
            ("source_location_id", "=", location.id),
            ("reservation_id.state", "=", "active"),
            ("reservation_id.expires_at", ">", fields.Datetime.now()),
        ]
        if exclude_reservation:
            domain.append(("reservation_id", "!=", exclude_reservation.id))
        lines = self.sudo().search(domain)
        return sum(
            line.product_uom_id._compute_quantity(line.quantity, product.uom_id)
            for line in lines
        )

    def _shop_api_payload(self):
        self.ensure_one()
        self.product_id._shop_api_ensure_uuid()
        return {
            "product_id": self.product_id.shop_api_uuid,
            "name": self.product_id.product_tmpl_id._get_website_display_name(False),
            "quantity": self.quantity,
            "uom": self.product_uom_id.name,
            "source_location": self.source_location_id.complete_name,
        }
