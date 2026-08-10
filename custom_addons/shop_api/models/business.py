import uuid

from odoo import api, fields, models


class ShopApiUuidMixin(models.AbstractModel):
    _name = "shop.api.uuid.mixin"
    _description = "Shop API Stable UUID Mixin"

    shop_api_uuid = fields.Char(
        string="Shop API UUID",
        copy=False,
        readonly=True,
        index=True,
    )

    _unique_shop_api_uuid = models.Constraint(
        "UNIQUE(shop_api_uuid)",
        "Shop API UUID must be unique.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        # Odoo evaluates a field default once for a multi-create batch. Assigning
        # here guarantees a different durable public identifier for every row.
        values_list = []
        for values in vals_list:
            values = dict(values)
            values.setdefault("shop_api_uuid", str(uuid.uuid4()))
            values_list.append(values)
        return super().create(values_list)

    @api.model
    def _shop_api_uuid_models(self):
        return [
            "product.template", "product.product", "product.category", "product.pricelist",
            "product.image", "product.attribute", "product.attribute.value", "delivery.carrier",
            "res.partner", "sale.order", "payment.transaction",
            "stock.picking", "stock.subwarehouse.website.refund.request", "account.move",
        ]

    def _shop_api_ensure_uuid(self):
        for record in self.filtered(lambda item: not item.shop_api_uuid):
            record.with_context(shop_api_skip_event=True).sudo().write({
                "shop_api_uuid": str(uuid.uuid4()),
            })
        return self

    def _shop_api_version(self):
        self.ensure_one()
        return fields.Datetime.to_string(self.write_date or self.create_date or fields.Datetime.now())


class ProductTemplate(models.Model):
    _name = "product.template"
    _inherit = ["product.template", "shop.api.uuid.mixin"]

    @api.model
    def _shop_api_inventory_snapshot(self):
        """Return compact availability without serializing the full catalogue."""
        templates = self.sudo().search([
            ("sale_ok", "=", True),
            ("website_published", "=", True),
            ("active", "=", True),
        ], order="id")
        variants = templates.mapped("product_variant_ids")
        templates._shop_api_ensure_uuid()
        variants._shop_api_ensure_uuid()

        quantities = {
            variant.id: 1.0 if not variant.is_storable else 0.0
            for variant in variants
        }
        storable_variants = variants.filtered("is_storable")
        if storable_variants:
            quants = self.env["stock.quant"].sudo().search([
                ("product_id", "in", storable_variants.ids),
                ("location_id.usage", "=", "internal"),
            ])
            for quant in quants:
                quantities[quant.product_id.id] += quant.available_quantity

            reservation_lines = self.env["shop.api.reservation.line"].sudo().search([
                ("product_id", "in", storable_variants.ids),
                ("reservation_id.state", "=", "active"),
                ("reservation_id.expires_at", ">", fields.Datetime.now()),
            ])
            for line in reservation_lines:
                quantities[line.product_id.id] -= line.product_uom_id._compute_quantity(
                    line.quantity, line.product_id.uom_id,
                )

        products = []
        for template in templates:
            variant_rows = []
            for variant in template.product_variant_ids:
                quantity = max(float(quantities.get(variant.id, 0.0)), 0.0)
                variant_rows.append({
                    "id": variant.shop_api_uuid,
                    "available": quantity > 0,
                    "available_quantity": quantity,
                })
            template_quantity = max(
                [row["available_quantity"] for row in variant_rows] or [0.0]
            )
            products.append({
                "id": template.shop_api_uuid,
                "available": template_quantity > 0,
                "available_quantity": template_quantity,
                "variants": variant_rows,
            })
        return {
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
            "products": products,
        }

    def _shop_api_payload(self, language="zh_CN", detail=True):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        is_english = language.lower().startswith("en")
        variants = []
        for variant in self.product_variant_ids:
            variant._shop_api_ensure_uuid()
            values = self._get_shop_variant_display_values(is_english=is_english)
            variants.append({
                "id": variant.shop_api_uuid,
                "sku": variant.default_code or self.default_code or "",
                "name": self._get_website_display_name(is_english),
                "attributes": values,
                "available_quantity": self._get_shop_available_quantity(),
                "available": self._is_shop_available(),
            })
        payload = {
            "id": self.shop_api_uuid,
            "version": self._shop_api_version(),
            "name": self._get_website_display_name(is_english),
            "name_zh": self.name,
            "name_en": self.x_website_english_name or self.name,
            "description": self.x_website_description_en if is_english else self.x_website_description_zh,
            "description_zh": self.x_website_description_zh or "",
            "description_en": self.x_website_description_en or "",
            "published": bool(self.website_published),
            "sale_ok": bool(self.sale_ok),
            "material_type": self.x_material_type,
            "category": self.categ_id._shop_api_summary() if self.categ_id else None,
            "currency": "USD" if is_english else self.currency_id.name,
            "price": self.x_website_usd_price if is_english else self.list_price,
            "price_cny": self.list_price,
            "price_usd": self.x_website_usd_price,
            "family": self._get_shop_product_family(),
            "group_summary": self._get_shop_group_summary(),
            "available": self._is_shop_available(),
            "available_quantity": self._get_shop_available_quantity(),
            "variants": variants,
            "images": self._shop_api_image_payloads(),
        }
        if detail:
            option_groups = self._get_shop_group_variant_option_groups(is_english=is_english)
            payload["variant_options"] = []
            for group in option_groups:
                image_products = group.get("image_products") or {}
                option_images = {}
                for value, image_product in image_products.items():
                    image_product._shop_api_ensure_uuid()
                    option_images[value] = image_product.shop_api_uuid
                payload["variant_options"].append({
                    "key": group["key"],
                    "label": group["label"],
                    "values": group["values"],
                    "image_product_ids": option_images,
                })
            payload["custom_attributes"] = [
                {"name": value.attribute_id.name, "value": value.value_text or ""}
                for value in self.x_custom_attribute_value_ids
            ]
            payload["group_variants"] = []
            for sibling in self._get_shop_group_siblings():
                sibling._shop_api_ensure_uuid()
                values = sibling._get_shop_variant_display_values(is_english=is_english)
                for variant in sibling.product_variant_ids:
                    variant._shop_api_ensure_uuid()
                    payload["group_variants"].append({
                        "id": variant.shop_api_uuid,
                        "product_id": sibling.shop_api_uuid,
                        "sku": variant.default_code or sibling.default_code or "",
                        "name": sibling._get_website_display_name(is_english),
                        "attributes": values,
                        "available_quantity": sibling._get_shop_available_quantity(),
                        "available": sibling._is_shop_available(),
                    })
        return payload

    def _shop_api_image_payloads(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        images = []
        if self.image_1920:
            images.append({
                "id": self.shop_api_uuid,
                "kind": "cover",
                "sequence": 0,
                "url": f"/api/v1/media/{self.shop_api_uuid}",
                "version": self._shop_api_version(),
            })
        for sequence, image in enumerate(self.product_template_image_ids, start=1):
            image._shop_api_ensure_uuid()
            images.append({
                "id": image.shop_api_uuid,
                "kind": "gallery",
                "sequence": sequence,
                "name": image.name,
                "url": f"/api/v1/media/{image.shop_api_uuid}",
                "version": image._shop_api_version(),
            })
        return images

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get("shop_api_skip_event"):
            for record in records:
                self.env["shop.api.event"].enqueue(
                    "product.created", record, {"product_id": record.shop_api_uuid},
                )
        return records

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get("shop_api_skip_event"):
            return result
        tracked = {
            "name", "x_website_english_name", "x_website_description_zh",
            "x_website_description_en", "list_price", "x_website_usd_price",
            "website_published", "sale_ok", "active", "categ_id",
        }
        image_fields = {"image_1920", "product_template_image_ids", "x_shop_group_cover"}
        if tracked.intersection(vals):
            event_type = "product.archived" if vals.get("active") is False else "product.updated"
            for record in self:
                self.env["shop.api.event"].enqueue(
                    event_type, record, {"product_id": record.shop_api_uuid},
                )
        if image_fields.intersection(vals):
            for record in self:
                self.env["shop.api.event"].enqueue(
                    "product.image.updated", record, {"product_id": record.shop_api_uuid},
                )
        return result


class ProductProduct(models.Model):
    _name = "product.product"
    _inherit = ["product.product", "shop.api.uuid.mixin"]


class ProductCategory(models.Model):
    _name = "product.category"
    _inherit = ["product.category", "shop.api.uuid.mixin"]

    def _shop_api_summary(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        return {"id": self.shop_api_uuid, "name": self.display_name}


class ProductPricelist(models.Model):
    _name = "product.pricelist"
    _inherit = ["product.pricelist", "shop.api.uuid.mixin"]


class ProductImage(models.Model):
    _name = "product.image"
    _inherit = ["product.image", "shop.api.uuid.mixin"]


class ProductAttribute(models.Model):
    _name = "product.attribute"
    _inherit = ["product.attribute", "shop.api.uuid.mixin"]


class ProductAttributeValue(models.Model):
    _name = "product.attribute.value"
    _inherit = ["product.attribute.value", "shop.api.uuid.mixin"]


class DeliveryCarrier(models.Model):
    _name = "delivery.carrier"
    _inherit = ["delivery.carrier", "shop.api.uuid.mixin"]


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "shop.api.uuid.mixin"]

    def _shop_api_payload(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        addresses = self.child_ids.filtered(lambda item: item.type in ("delivery", "invoice", "contact"))
        for address in addresses:
            address._shop_api_ensure_uuid()
        return {
            "id": self.shop_api_uuid,
            "version": self._shop_api_version(),
            "name": self.name,
            "email": self.email or "",
            "phone": self.phone or (self.mobile if "mobile" in self._fields else False) or "",
            "language": self.lang or "zh_CN",
            "company": bool(self.is_company),
            "addresses": [address._shop_api_address_payload() for address in addresses],
        }

    def _shop_api_address_payload(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        return {
            "id": self.shop_api_uuid,
            "type": self.type,
            "name": self.name,
            "street": self.street or "",
            "street2": self.street2 or "",
            "city": self.city or "",
            "state": self.state_id.name if self.state_id else "",
            "state_code": self.state_id.code if self.state_id else "",
            "zip": self.zip or "",
            "country": self.country_id.code if self.country_id else "",
            "phone": self.phone or (self.mobile if "mobile" in self._fields else False) or "",
            "active": bool(self.active),
        }


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "shop.api.uuid.mixin"]

    def _shop_api_payload(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        self.partner_id._shop_api_ensure_uuid()
        return {
            "id": self.shop_api_uuid,
            "version": self._shop_api_version(),
            "number": self.name,
            "state": self.state,
            "customer_id": self.partner_id.shop_api_uuid,
            "currency": self.currency_id.name,
            "amount_untaxed": self.amount_untaxed,
            "amount_tax": self.amount_tax,
            "amount_total": self.amount_total,
            "payment_state": self.x_website_payment_state,
            "payment_reference": self.x_website_payment_reference or "",
            "language": self.x_website_checkout_language or self.partner_id.lang or "zh_CN",
            "created_at": fields.Datetime.to_string(self.create_date),
            "items": [
                {
                    "product_id": line.product_id.shop_api_uuid,
                    "name": line.name_short or line.name,
                    "quantity": line.product_uom_qty,
                    "unit_price": line.price_unit,
                    "subtotal": line.price_subtotal,
                    "total": line.price_total,
                    "uom": line.product_uom_id.name,
                    "is_delivery": bool(line.is_delivery),
                    "refundable": bool(not line.is_delivery and line.product_uom_qty > 0),
                }
                for line in self.order_line.filtered(lambda item: not item.display_type)
            ],
            "shipments": [picking._shop_api_payload() for picking in self.picking_ids],
            "payments": [transaction._shop_api_payload() for transaction in self.transaction_ids],
            "refund_requests": [item._shop_api_payload() for item in self.x_website_refund_request_ids],
        }

    def _get_available_qty_for_source_location(self, product, location, exclude_order=False):
        available = super()._get_available_qty_for_source_location(
            product, location, exclude_order=exclude_order,
        )
        reserved = self.env["shop.api.reservation.line"].sudo()._active_reserved_qty(product, location)
        return max(available - reserved, 0.0)

    def action_confirm(self):
        result = super().action_confirm()
        if not self.env.context.get("shop_api_skip_event"):
            for order in self:
                self.env["shop.api.event"].enqueue(
                    "order.confirmed", order, {"order_id": order.shop_api_uuid},
                )
        return result

    def action_cancel(self):
        result = super().action_cancel()
        if not self.env.context.get("shop_api_skip_event"):
            for order in self:
                self.env["shop.api.event"].enqueue(
                    "order.cancelled", order, {"order_id": order.shop_api_uuid},
                )
        return result


class PaymentTransaction(models.Model):
    _name = "payment.transaction"
    _inherit = ["payment.transaction", "shop.api.uuid.mixin"]

    def _shop_api_payload(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        return {
            "id": self.shop_api_uuid,
            "version": self._shop_api_version(),
            "reference": self.reference,
            "provider": self.provider_code,
            "provider_reference": self.provider_reference or "",
            "operation": self.operation,
            "state": self.state,
            "amount": self.amount,
            "currency": self.currency_id.name,
        }

    def _set_done(self, *, state_message=None, extra_allowed_states=()):
        transactions = super()._set_done(
            state_message=state_message, extra_allowed_states=extra_allowed_states,
        )
        for transaction in transactions:
            self.env["shop.api.event"].enqueue(
                "payment.completed", transaction, transaction._shop_api_payload(),
            )
        return transactions

    def _set_pending(self, *, state_message=None, extra_allowed_states=()):
        transactions = super()._set_pending(
            state_message=state_message, extra_allowed_states=extra_allowed_states,
        )
        for transaction in transactions:
            self.env["shop.api.event"].enqueue(
                "payment.pending", transaction, transaction._shop_api_payload(),
            )
        return transactions

    def _set_error(self, state_message, extra_allowed_states=()):
        transactions = super()._set_error(
            state_message, extra_allowed_states=extra_allowed_states,
        )
        for transaction in transactions:
            self.env["shop.api.event"].enqueue(
                "payment.failed", transaction, transaction._shop_api_payload(),
            )
        return transactions


class StockPicking(models.Model):
    _name = "stock.picking"
    _inherit = ["stock.picking", "shop.api.uuid.mixin"]

    def _shop_api_payload(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        return {
            "id": self.shop_api_uuid,
            "version": self._shop_api_version(),
            "number": self.name,
            "state": self.state,
            "type": self.picking_type_code,
            "origin": self.origin or "",
            "scheduled_at": fields.Datetime.to_string(self.scheduled_date),
            "completed_at": fields.Datetime.to_string(self.date_done),
            "source": self.location_id.complete_name,
            "destination": self.location_dest_id.complete_name,
        }

    def write(self, vals):
        previous_states = {record.id: record.state for record in self}
        result = super().write(vals)
        if "state" in vals and not self.env.context.get("shop_api_skip_event"):
            event_by_state = {"assigned": "shipment.ready", "done": "shipment.delivered"}
            for picking in self:
                if previous_states[picking.id] != picking.state and picking.picking_type_code == "outgoing":
                    event_type = event_by_state.get(picking.state)
                    if event_type:
                        self.env["shop.api.event"].enqueue(
                            event_type, picking, picking._shop_api_payload(),
                        )
        return result


class WebsiteRefundRequest(models.Model):
    _name = "stock.subwarehouse.website.refund.request"
    _inherit = ["stock.subwarehouse.website.refund.request", "shop.api.uuid.mixin"]

    shop_api_return_carrier = fields.Char(string="客户退货承运商", copy=False)
    shop_api_return_tracking = fields.Char(string="客户退货单号", copy=False)
    shop_api_return_shipped_at = fields.Datetime(string="客户退货发出时间", copy=False)

    def _shop_api_payload(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        return {
            "id": self.shop_api_uuid,
            "version": self._shop_api_version(),
            "state": self.state,
            "review_state": self.review_state,
            "amount": self.amount_total,
            "currency": self.currency_id.name,
            "return_required": self.return_required,
            "return_location": self.return_location_id.complete_name if self.return_location_id else "",
            "return_carrier": self.shop_api_return_carrier or "",
            "return_tracking": self.shop_api_return_tracking or "",
            "items": [
                {
                    "product_id": line.product_id.shop_api_uuid,
                    "name": line.sale_line_id.name_short or line.sale_line_id.name,
                    "quantity": line.quantity,
                    "amount": line.amount,
                }
                for line in self.line_ids
            ],
            "credit_note_id": self.credit_note_id.shop_api_uuid if self.credit_note_id else None,
            "refund_id": self.refund_transaction_id.shop_api_uuid if self.refund_transaction_id else None,
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            self.env["shop.api.event"].enqueue(
                "refund.requested", record, record._shop_api_payload(),
            )
        return records

    def write(self, vals):
        previous = {record.id: record.state for record in self}
        result = super().write(vals)
        if not self.env.context.get("shop_api_skip_event"):
            for record in self:
                if previous[record.id] != record.state:
                    self.env["shop.api.event"].enqueue(
                        "refund.updated", record, record._shop_api_payload(),
                    )
        return result


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "shop.api.uuid.mixin"]

    def _shop_api_payload(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        return {
            "id": self.shop_api_uuid,
            "version": self._shop_api_version(),
            "number": self.name,
            "type": self.move_type,
            "state": self.state,
            "payment_state": self.payment_state,
            "amount_total": self.amount_total,
            "amount_residual": self.amount_residual,
            "currency": self.currency_id.name,
            "date": fields.Date.to_string(self.invoice_date or self.date),
        }


class StockQuant(models.Model):
    _inherit = "stock.quant"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._shop_api_emit_inventory_events()
        return records

    def write(self, vals):
        result = super().write(vals)
        if {"quantity", "reserved_quantity", "inventory_quantity"}.intersection(vals):
            self._shop_api_emit_inventory_events()
        return result

    def _shop_api_emit_inventory_events(self):
        if self.env.context.get("shop_api_skip_event"):
            return
        for quant in self.filtered(lambda item: item.location_id.usage == "internal"):
            quant.product_id._shop_api_ensure_uuid()
            self.env["shop.api.event"].enqueue("inventory.updated", quant.product_id, {
                "product_id": quant.product_id.shop_api_uuid,
                "location": quant.location_id.complete_name,
                "available_quantity": quant.available_quantity,
            })
