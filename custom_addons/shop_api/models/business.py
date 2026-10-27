import re
import uuid
from calendar import timegm

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
        language = "en_US" if str(language).lower().startswith("en") else "zh_CN"
        is_english = language == "en_US"
        chinese_product = self.with_context(lang="zh_CN")
        english_name = self._shop_api_group_english_name()
        variants = []
        for variant in self.product_variant_ids:
            variant._shop_api_ensure_uuid()
            values = self._get_shop_variant_display_values(is_english=is_english)
            variants.append({
                "id": variant.shop_api_uuid,
                "sku": variant.default_code or self.default_code or "",
                "name": english_name if is_english else chinese_product.name,
                "attributes": values,
                "available_quantity": self._get_shop_available_quantity(),
                "available": self._is_shop_available(),
            })
        payload = {
            "id": self.shop_api_uuid,
            "version": self._shop_api_version(),
            "name": english_name if is_english else chinese_product.name,
            "name_zh": chinese_product.name,
            "name_en": english_name,
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
            # The storefront has its own PostgreSQL catalogue copy.  Preserve
            # the ERP-selected representative for a same-name product group
            # instead of letting that copy fall back to an arbitrary SKU.
            "group_cover": bool(self.x_shop_group_cover),
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
                        "name": english_name if is_english else sibling.with_context(lang="zh_CN").name,
                        "attributes": values,
                        "available_quantity": sibling._get_shop_available_quantity(),
                        "available": sibling._is_shop_available(),
                    })
        return payload

    @staticmethod
    def _shop_api_is_english_name(value):
        return bool(re.search(r"[A-Za-z]", str(value or "")))

    def _shop_api_group_english_name(self):
        """Use one reliable English website name for every same-name SKU."""
        self.ensure_one()
        siblings = self._get_all_shop_group_siblings()
        explicit_names = siblings.mapped("x_website_english_name")
        translated_names = [
            sibling.with_context(lang="en_US").name for sibling in siblings
        ]
        for candidate in [*explicit_names, *translated_names]:
            normalized = " ".join(str(candidate or "").split())
            if self._shop_api_is_english_name(normalized):
                return normalized
        return self.x_website_english_name or self.with_context(lang="en_US").name

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
            records._queue_shop_product_sync("product.created")
        return records

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get("shop_api_skip_event"):
            return result
        self._queue_shop_product_sync(
            "product.archived" if vals.get("active") is False else "product.updated"
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

    @api.model_create_multi
    def create(self, vals_list):
        images = super().create(vals_list)
        if not self.env.context.get("shop_api_skip_event"):
            images.mapped("product_tmpl_id")._queue_shop_product_sync("product.image.updated")
        return images

    def write(self, vals):
        affected_products = self.mapped("product_tmpl_id")
        result = super().write(vals)
        if not self.env.context.get("shop_api_skip_event") and {
            "image_1920", "name", "sequence", "product_tmpl_id",
        }.intersection(vals):
            affected_products |= self.mapped("product_tmpl_id")
            affected_products._queue_shop_product_sync("product.image.updated")
        return result

    def unlink(self):
        products = self.mapped("product_tmpl_id")
        result = super().unlink()
        if not self.env.context.get("shop_api_skip_event"):
            products.exists()._queue_shop_product_sync("product.image.updated")
        return result


class ProductAttribute(models.Model):
    _name = "product.attribute"
    _inherit = ["product.attribute", "shop.api.uuid.mixin"]


class ProductAttributeValue(models.Model):
    _name = "product.attribute.value"
    _inherit = ["product.attribute.value", "shop.api.uuid.mixin"]


class DeliveryCarrier(models.Model):
    _name = "delivery.carrier"
    _inherit = ["delivery.carrier", "shop.api.uuid.mixin"]

    def _shop_api_display_name(self, language="zh_CN"):
        """Return the delivery label in the website language requested by the Shop."""
        self.ensure_one()
        language = "en_US" if str(language or "").lower().startswith("en") else "zh_CN"
        localized_name = str(self.with_context(lang=language).name or "").strip()
        known_names = {
            str(self.with_context(lang=lang).name or "").strip().casefold()
            for lang in ("zh_CN", "en_US")
        }
        explicit_english = ""
        if self.product_id:
            template = self.product_id.product_tmpl_id
            known_names.update({
                str(template.with_context(lang=lang).name or "").strip().casefold()
                for lang in ("zh_CN", "en_US")
            })
            if language == "en_US":
                explicit_english = str(
                    getattr(template, "x_website_english_name", "") or ""
                ).strip()
        standard_names = {
            "标准送货", "标准配送", "standard delivery", "standard shipping",
        }
        if known_names.intersection(standard_names):
            return "Standard delivery" if language == "en_US" else "标准送货"
        if re.search(r"[A-Za-z]", explicit_english):
            return explicit_english
        return localized_name or ("Delivery" if language == "en_US" else "配送")


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


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def _shop_api_is_delivery_line(self):
        """Recognize normal delivery rows and legacy carrier-product rows."""
        self.ensure_one()
        carrier = self.order_id.carrier_id
        return bool(
            self.is_delivery
            or (
                carrier
                and carrier.product_id
                and self.product_id == carrier.product_id
            )
        )

    @staticmethod
    def _shop_api_option_semantic_key(label):
        normalized = re.sub(r"[\s_\-/]+", "", str(label or "").casefold())
        aliases = {
            "color": "color", "colour": "color", "颜色": "color", "颜色分类": "color",
            "size": "size", "尺寸": "size", "尺码": "size", "鞋码": "size",
            "flex": "flex", "硬度": "flex", "款型": "flex",
            "type": "type", "类型": "type", "款式": "type",
        }
        return aliases.get(normalized, normalized)

    def _shop_api_selected_options(self, language="zh_CN"):
        """Serialize the customer's selected non-colour product choices."""
        self.ensure_one()
        language = "en_US" if str(language or "").lower().startswith("en") else "zh_CN"
        is_english = language == "en_US"
        if self._shop_api_is_delivery_line() or not self.product_id:
            return []
        product_template = self.product_id.product_tmpl_id
        raw_values = product_template._get_shop_variant_display_values(is_english=False)
        display_values = product_template._get_shop_variant_display_values(is_english=is_english)
        options = []
        seen_keys = set()

        def add_option(key, label, value):
            semantic_key = self._shop_api_option_semantic_key(key or label)
            value = str(value or "").strip()
            if not value or semantic_key == "color" or semantic_key in seen_keys:
                return
            options.append({"key": semantic_key, "label": label, "value": value})
            seen_keys.add(semantic_key)

        type_code = str(raw_values.get("type_code") or "").strip()
        if type_code:
            add_option("type", "Type" if is_english else "类型", type_code)

        raw_size = str(raw_values.get("size") or "").strip()
        if raw_size not in {"", "未识别", "默认"}:
            add_option("size", "Size" if is_english else "尺码", display_values.get("size"))

        raw_flex = product_template._normalize_website_mapping_flex(raw_values.get("flex"))
        if raw_flex not in {"", "000", "无", "无硬度", "未识别", "默认"}:
            add_option("flex", "Flex" if is_english else "硬度", display_values.get("flex"))

        selected_ptavs = (
            self.product_template_attribute_value_ids
            | self.product_no_variant_attribute_value_ids
        ).sorted()
        custom_values = {
            value.custom_product_template_attribute_value_id.id: value.custom_value
            for value in self.product_custom_attribute_value_ids
        }
        grouped_values = {}
        for ptav in selected_ptavs:
            translated = ptav.with_context(lang=language)
            label = translated.attribute_id.name
            semantic_key = self._shop_api_option_semantic_key(label)
            if semantic_key == "color" or semantic_key in seen_keys:
                continue
            value = custom_values.get(ptav.id) or translated.name
            group = grouped_values.setdefault(
                semantic_key or f"attribute_{ptav.attribute_id.id}",
                {"label": label, "values": []},
            )
            if value and value not in group["values"]:
                group["values"].append(value)
        for key, group in grouped_values.items():
            add_option(key, group["label"], " / ".join(group["values"]))
        return options

    def _shop_api_display_name(self, language="zh_CN"):
        """Return the website-facing name in the language requested by the shop."""
        self.ensure_one()
        language = "en_US" if str(language or "").lower().startswith("en") else "zh_CN"
        if self._shop_api_is_delivery_line():
            carrier = self.order_id.carrier_id
            if carrier:
                return carrier._shop_api_display_name(language=language)
        if not self.product_id:
            localized = self.with_context(lang=language)
            return localized.name_short or localized.name
        return self.product_id.product_tmpl_id._get_website_display_name(
            is_english=language == "en_US"
        )


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "shop.api.uuid.mixin"]

    def _shop_api_origin_clients(self):
        """Return only storefront clients that own these separated-shop orders."""
        if not self:
            return self.env["shop.api.client"]
        references = self.env["shop.api.external.reference"].sudo().search([
            ("resource_type", "=", "order"),
            ("resource_model", "=", "sale.order"),
            ("resource_id", "in", self.ids),
        ])
        return references.mapped("client_id").filtered("active")

    def _shop_api_payload(self, language=None):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        self.partner_id._shop_api_ensure_uuid()
        language = language or self.x_website_checkout_language or self.partner_id.lang or "zh_CN"
        language = "en_US" if str(language).lower().startswith("en") else "zh_CN"
        payment_expired = self._website_payment_deadline_is_expired()
        payment_state = "expired" if payment_expired else self.x_website_payment_state
        payment_expires_at = self.x_website_stock_reserved_until
        return {
            "id": self.shop_api_uuid,
            "authoritative": True,
            "version": self._shop_api_version(),
            "number": self.name,
            "state": self.state,
            "customer_id": self.partner_id.shop_api_uuid,
            "currency": self.currency_id.name,
            "amount_untaxed": self.amount_untaxed,
            "amount_tax": self.amount_tax,
            "amount_total": self.amount_total,
            "payment_state": payment_state,
            "payment_expired": payment_expired,
            "payment_expires_at": fields.Datetime.to_string(
                payment_expires_at
            ),
            "payment_expires_at_epoch": (
                timegm(payment_expires_at.timetuple()) * 1000
                if payment_expires_at else 0
            ),
            "payment_reference": self.x_website_payment_reference or "",
            "delivery_state": self.x_website_delivery_state or "",
            "delivery_started_at": fields.Datetime.to_string(
                self.x_website_delivery_started_at
            ),
            "delivered_at": fields.Datetime.to_string(self.x_website_delivered_at),
            "language": language,
            "created_at": fields.Datetime.to_string(self.create_date),
            "items": [
                {
                    "product_id": line.product_id.shop_api_uuid,
                    "name": line._shop_api_display_name(language=language),
                    "quantity": line.product_uom_qty,
                    "unit_price": line.price_unit,
                    "subtotal": line.price_subtotal,
                    "total": line.price_total,
                    "uom": line.product_uom_id.name,
                    "is_delivery": line._shop_api_is_delivery_line(),
                    "refundable": bool(
                        not line._shop_api_is_delivery_line()
                        and line.product_uom_qty > 0
                    ),
                    "selected_options": line._shop_api_selected_options(language=language),
                }
                for line in self.order_line.filtered(lambda item: not item.display_type)
            ],
            "shipments": [picking._shop_api_payload() for picking in self.picking_ids],
            "payments": [transaction._shop_api_payload() for transaction in self.transaction_ids],
            "refund_requests": [
                item._shop_api_payload(language=language)
                for item in self.x_website_refund_request_ids
            ],
        }

    def write(self, vals):
        previous_delivery_states = {
            order.id: order.x_website_delivery_state for order in self
        }
        result = super().write(vals)
        if (
            "x_website_delivery_state" in vals
            and not self.env.context.get("shop_api_skip_event")
        ):
            event_by_state = {
                "awaiting_delivery": "shipment.ready",
                "delivering": "shipment.shipped",
                "delivered": "shipment.delivered",
            }
            for order in self:
                if previous_delivery_states.get(order.id) == order.x_website_delivery_state:
                    continue
                event_type = event_by_state.get(order.x_website_delivery_state)
                if event_type:
                    for client in order._shop_api_origin_clients():
                        self.env["shop.api.event"].enqueue(
                            event_type, order, order._shop_api_payload(), client=client,
                        )
        return result

    def _get_available_qty_for_source_location(
        self,
        product,
        location,
        exclude_order=False,
        exclude_reservation=False,
    ):
        available = super()._get_available_qty_for_source_location(
            product,
            location,
            exclude_order=exclude_order,
            exclude_reservation=exclude_reservation,
        )
        reserved = self.env["shop.api.reservation.line"].sudo()._active_reserved_qty(
            product,
            location,
            exclude_reservation=exclude_reservation,
        )
        return max(available - reserved, 0.0)

    def action_confirm(self):
        result = super().action_confirm()
        if not self.env.context.get("shop_api_skip_event"):
            for order in self:
                for client in order._shop_api_origin_clients():
                    self.env["shop.api.event"].enqueue(
                        "order.confirmed", order, {"order_id": order.shop_api_uuid},
                        client=client,
                    )
        return result

    def action_cancel(self):
        result = super().action_cancel()
        if not self.env.context.get("shop_api_skip_event"):
            for order in self:
                for client in order._shop_api_origin_clients():
                    self.env["shop.api.event"].enqueue(
                        "order.cancelled", order, {"order_id": order.shop_api_uuid},
                        client=client,
                    )
        return result


class PaymentTransaction(models.Model):
    _name = "payment.transaction"
    _inherit = ["payment.transaction", "shop.api.uuid.mixin"]

    def _shop_api_origin_clients(self):
        clients = self.env["shop.api.client"]
        for transaction in self:
            orders = (
                transaction.sale_order_ids
                or transaction.source_transaction_id.sale_order_ids
            )
            clients |= orders._shop_api_origin_clients()
        return clients

    def _shop_api_payload(self):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        orders = self.sale_order_ids or self.source_transaction_id.sale_order_ids
        orders._shop_api_ensure_uuid()
        payload = {
            "id": self.shop_api_uuid,
            "authoritative": True,
            "version": self._shop_api_version(),
            "reference": self.reference,
            "provider": self.provider_code,
            "provider_reference": self.provider_reference or "",
            "operation": self.operation,
            "state": self.state,
            "amount": self.amount,
            "currency": self.currency_id.name,
            "order_ids": orders.mapped("shop_api_uuid"),
        }
        if self.provider_code == "wechatpay":
            payload["simulation_mode"] = bool(
                getattr(self.provider_id, "wechatpay_simulation_mode", False)
            )
            qr_method = getattr(self, "_get_wechatpay_qr_data_uri", None)
            payload["qr_code_data_uri"] = qr_method() if qr_method else None
        elif self.provider_code == "alipay":
            payload["simulation_mode"] = bool(
                getattr(self.provider_id, "alipay_simulation_mode", False)
            )
            qr_method = getattr(self, "_get_alipay_qr_data_uri", None)
            payload["qr_code_data_uri"] = qr_method() if qr_method else None
        elif self.provider_code == "lianlian":
            payload["simulation_mode"] = False
            payload["checkout_url"] = getattr(self, "lianlian_payment_url", False) or None
        payload["post_processed"] = bool(self.is_post_processed)
        return payload

    def _set_done(self, *, state_message=None, extra_allowed_states=()):
        transactions = super()._set_done(
            state_message=state_message, extra_allowed_states=extra_allowed_states,
        )
        for transaction in transactions:
            for client in transaction._shop_api_origin_clients():
                self.env["shop.api.event"].enqueue(
                    "payment.completed", transaction, transaction._shop_api_payload(),
                    client=client,
                )
        transactions.sale_order_ids._queue_paid_website_delivery()
        post_processing_cron = self.env.ref(
            "payment.cron_post_process_payment_tx", raise_if_not_found=False,
        )
        if post_processing_cron:
            # Match Odoo's standard payment lifecycle without keeping the API
            # request open while invoices, PDFs, and email are generated.
            post_processing_cron.sudo()._trigger()
        return transactions

    def _set_pending(self, *, state_message=None, extra_allowed_states=()):
        transactions = super()._set_pending(
            state_message=state_message, extra_allowed_states=extra_allowed_states,
        )
        for transaction in transactions:
            for client in transaction._shop_api_origin_clients():
                self.env["shop.api.event"].enqueue(
                    "payment.pending", transaction, transaction._shop_api_payload(),
                    client=client,
                )
        return transactions

    def _set_error(self, state_message, extra_allowed_states=()):
        transactions = super()._set_error(
            state_message, extra_allowed_states=extra_allowed_states,
        )
        for transaction in transactions:
            for client in transaction._shop_api_origin_clients():
                self.env["shop.api.event"].enqueue(
                    "payment.failed", transaction, transaction._shop_api_payload(),
                    client=client,
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
                if (
                    previous_states[picking.id] != picking.state
                    and picking.picking_type_code == "outgoing"
                    and not picking.sale_id.x_website_delivery_state
                ):
                    event_type = event_by_state.get(picking.state)
                    if event_type:
                        for client in picking.sale_id._shop_api_origin_clients():
                            self.env["shop.api.event"].enqueue(
                                event_type, picking, picking._shop_api_payload(), client=client,
                            )
        return result


class WebsiteRefundRequest(models.Model):
    _name = "stock.subwarehouse.website.refund.request"
    _inherit = ["stock.subwarehouse.website.refund.request", "shop.api.uuid.mixin"]

    shop_api_return_carrier = fields.Char(string="客户退货承运商", copy=False)
    shop_api_return_tracking = fields.Char(string="客户退货单号", copy=False)
    shop_api_return_shipped_at = fields.Datetime(string="客户退货发出时间", copy=False)

    def _shop_api_payload(self, language=None):
        self.ensure_one()
        self._shop_api_ensure_uuid()
        self.order_id._shop_api_ensure_uuid()
        language = (
            language
            or self.order_id.x_website_checkout_language
            or self.order_id.partner_id.lang
            or "zh_CN"
        )
        language = "en_US" if str(language).lower().startswith("en") else "zh_CN"
        return {
            "id": self.shop_api_uuid,
            "order_id": self.order_id.shop_api_uuid,
            "version": self._shop_api_version(),
            "authoritative": True,
            "state": self.state,
            "review_state": self.review_state,
            "amount": self.amount_total,
            "currency": self.currency_id.name,
            "return_required": self.return_required,
            "return_location": self.return_location_id.complete_name if self.return_location_id else "",
            "return_carrier": self.shop_api_return_carrier or "",
            "return_tracking": self.shop_api_return_tracking or "",
            "return_delivery_state": self.x_return_delivery_state or "",
            "return_delivery_started_at": fields.Datetime.to_string(
                self.x_return_delivery_started_at
            ),
            "return_delivered_at": fields.Datetime.to_string(
                self.x_return_delivered_at
            ),
            "items": [
                {
                    "product_id": line.product_id.shop_api_uuid,
                    "name": line.sale_line_id._shop_api_display_name(language=language),
                    "quantity": line.quantity,
                    "amount": line.amount,
                    "selected_options": line.sale_line_id._shop_api_selected_options(
                        language=language
                    ),
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
            for client in record.order_id._shop_api_origin_clients():
                self.env["shop.api.event"].enqueue(
                    "refund.requested", record, record._shop_api_payload(), client=client,
                )
        return records

    def write(self, vals):
        previous = {
            record.id: (record.state, record.x_return_delivery_state)
            for record in self
        }
        result = super().write(vals)
        if not self.env.context.get("shop_api_skip_event"):
            for record in self:
                if previous[record.id] != (record.state, record.x_return_delivery_state):
                    for client in record.order_id._shop_api_origin_clients():
                        self.env["shop.api.event"].enqueue(
                            "refund.updated", record, record._shop_api_payload(), client=client,
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
