import hashlib
import json
import re
import time
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .api_client import StorefrontApiError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_storefront_reservation_id = fields.Char(copy=False, readonly=True)
    x_storefront_reservation_expires_at = fields.Datetime(copy=False, readonly=True)
    x_storefront_quote_fingerprint = fields.Char(copy=False, readonly=True)
    x_storefront_remote_customer_id = fields.Char(copy=False, readonly=True)
    x_storefront_remote_order_id = fields.Char(copy=False, readonly=True)
    x_storefront_remote_payment_id = fields.Char(copy=False, readonly=True)
    x_storefront_remote_state = fields.Char(copy=False, readonly=True)
    x_storefront_payment_provider = fields.Char(copy=False, readonly=True)
    x_storefront_payment_currency = fields.Char(copy=False, readonly=True)
    x_storefront_payment_amount = fields.Float(copy=False, readonly=True)
    x_storefront_shortage_product_uuids = fields.Json(copy=False, default=list)
    x_storefront_attempt_id = fields.Char(copy=False, readonly=True, index=True)
    x_storefront_completed_attempt_id = fields.Char(copy=False, readonly=True)
    x_storefront_completed_at = fields.Datetime(copy=False, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            values.setdefault("x_storefront_attempt_id", str(uuid.uuid4()))
        return super().create(vals_list)

    def _storefront_attempt_key(self):
        """Return the stable key for this checkout attempt, creating it lazily for old carts."""
        self.ensure_one()
        if not self.x_storefront_attempt_id:
            self.x_storefront_attempt_id = str(uuid.uuid4())
        return self.x_storefront_attempt_id

    def _storefront_api_items(self):
        self.ensure_one()
        items = []
        for line in self.order_line.filtered(lambda item: not item.is_delivery and item.product_uom_qty > 0):
            product_uuid = line.product_id.shop_api_uuid
            if not product_uuid:
                raise ValidationError(_("A cart product has no ERP API identifier."))
            items.append({"product_id": product_uuid, "quantity": line.product_uom_qty})
        return items

    @staticmethod
    def _storefront_aggregate_items(items):
        """Compare carts by product totals, independent of split sale lines."""
        aggregated = {}
        for item in items or []:
            if item.get("is_delivery"):
                continue
            product_id = str(item.get("product_id") or "")
            if not product_id:
                continue
            aggregated[product_id] = aggregated.get(product_id, 0.0) + float(
                item.get("quantity") or 0.0
            )
        return aggregated

    def _storefront_remote_order_matches(self, remote, desired_customer_id=False):
        self.ensure_one()
        if not remote or remote.get("id") != self.x_storefront_remote_order_id:
            return False
        payment_states = {payment.get("state") for payment in remote.get("payments") or []}
        if remote.get("state") not in {"draft", "sent"} or payment_states & {"authorized", "done"}:
            return False
        if desired_customer_id and remote.get("customer_id") != desired_customer_id:
            return False
        if remote.get("currency") != self.currency_id.name:
            return False
        if self.currency_id.compare_amounts(remote.get("amount_total") or 0.0, self.amount_total):
            return False
        return self._storefront_aggregate_items(remote.get("items")) == (
            self._storefront_aggregate_items(self._storefront_api_items())
        )

    def _storefront_clear_remote_checkout(self):
        self.ensure_one()
        self.write({
            "x_storefront_reservation_id": False,
            "x_storefront_reservation_expires_at": False,
            "x_storefront_quote_fingerprint": False,
            "x_storefront_remote_customer_id": False,
            "x_storefront_remote_order_id": False,
            "x_storefront_remote_payment_id": False,
            "x_storefront_remote_state": False,
            "x_storefront_payment_provider": False,
            "x_storefront_payment_currency": False,
            "x_storefront_payment_amount": 0.0,
        })

    def _storefront_fingerprint(self):
        payload = json.dumps({
            "language": self.x_website_checkout_language or "zh_CN",
            "items": self._storefront_api_items(),
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _storefront_check_inventory(self):
        self.ensure_one()
        items = self._storefront_api_items()
        if not items:
            self.x_storefront_shortage_product_uuids = []
            return []
        checked = self.env["storefront.erp.client"].post("/api/v1/inventory/check", {"items": items})
        shortages = [item["product_id"] for item in checked or [] if not item.get("available")]
        self.x_storefront_shortage_product_uuids = shortages
        return checked or []

    def _get_source_inventory_shortage_lines(self):
        self.ensure_one()
        shortages = set(self.x_storefront_shortage_product_uuids or [])
        return self.order_line.filtered(lambda line: line.product_id.shop_api_uuid in shortages)

    def _storefront_clear_shortage_for_product(self, product):
        """Invalidate only the ERP shortage marker whose quantity was edited."""
        self.ensure_one()
        product_uuid = product.shop_api_uuid
        if not product_uuid:
            return
        shortages = list(self.x_storefront_shortage_product_uuids or [])
        if product_uuid in shortages:
            self.x_storefront_shortage_product_uuids = [
                item for item in shortages if item != product_uuid
            ]

    def _storefront_release_reservation(self):
        self.ensure_one()
        if self.x_storefront_reservation_id:
            reservation_id = self.x_storefront_reservation_id
            try:
                self.env["storefront.erp.client"].post(
                    f"/api/v1/reservations/{reservation_id}/release",
                    {}, idempotency_key=(
                        f"release-{self._storefront_attempt_key()}-{reservation_id}"
                    ),
                )
            finally:
                self.write({
                    "x_storefront_reservation_id": False,
                    "x_storefront_reservation_expires_at": False,
                    "x_storefront_quote_fingerprint": False,
                })

    def _storefront_confirm_reservation(self):
        """Require a current ERP read-after-write confirmation of the hold."""
        self.ensure_one()
        if not self.x_storefront_reservation_id:
            raise StorefrontApiError(
                _("ERP has not confirmed an inventory reservation."),
                code="reservation_required", status=409,
            )
        reservation = self.env["storefront.erp.client"].get(
            f"/api/v1/reservations/{self.x_storefront_reservation_id}"
        ) or {}
        expires_at = fields.Datetime.to_datetime(reservation.get("expires_at"))
        remote_items = self._storefront_aggregate_items(reservation.get("items"))
        local_items = self._storefront_aggregate_items(self._storefront_api_items())
        if (
            reservation.get("authoritative") is not True
            or reservation.get("id") != self.x_storefront_reservation_id
            or reservation.get("state") != "active"
            or not expires_at
            or expires_at <= fields.Datetime.now()
            or remote_items != local_items
        ):
            raise StorefrontApiError(
                _("ERP rejected or expired the inventory reservation."),
                code="reservation_not_confirmed", status=409,
                details={"reservation_id": self.x_storefront_reservation_id},
            )
        self.x_storefront_reservation_expires_at = expires_at
        return reservation

    def _storefront_ensure_quote(self):
        self.ensure_one()
        attempt_id = self._storefront_attempt_key()
        fingerprint = self._storefront_fingerprint()
        now = fields.Datetime.now()
        if (
            self.x_storefront_reservation_id
            and self.x_storefront_quote_fingerprint == fingerprint
            and self.x_storefront_reservation_expires_at
            and self.x_storefront_reservation_expires_at > now
        ):
            self._storefront_confirm_reservation()
            return self.x_storefront_reservation_id
        if self.x_storefront_reservation_id:
            self._storefront_release_reservation()
        quote = self.env["storefront.erp.client"].post(
            "/api/v1/checkout/quote",
            {
                "external_id": f"quote-{attempt_id}-{fingerprint[:12]}",
                "language": self.x_website_checkout_language or "zh_CN",
                "items": self._storefront_api_items(),
            },
            idempotency_key=f"quote-{attempt_id}-{fingerprint}",
        )
        if (
            not quote
            or quote.get("authoritative") is not True
            or not quote.get("reservation_id")
            or not quote.get("expires_at")
        ):
            raise StorefrontApiError(
                _("ERP did not authoritatively confirm the inventory reservation."),
                code="invalid_reservation_confirmation", status=502,
            )
        self.write({
            "x_storefront_reservation_id": quote["reservation_id"],
            "x_storefront_reservation_expires_at": fields.Datetime.to_datetime(quote["expires_at"]),
            "x_storefront_quote_fingerprint": fingerprint,
            "x_storefront_shortage_product_uuids": [],
        })
        return quote["reservation_id"]

    def _storefront_sync_remote_order(self):
        self.ensure_one()
        attempt_id = self._storefront_attempt_key()
        client = self.env["storefront.erp.client"]
        partner = self.partner_invoice_id
        account = partner.commercial_partner_id.user_ids.filtered(
            lambda user: user.active and user.x_storefront_remote_customer_id
        )[:1]
        desired_customer_id = account.x_storefront_remote_customer_id if account else False
        if self.x_storefront_remote_order_id:
            old_remote_order_id = self.x_storefront_remote_order_id
            remote = client.get(f"/api/v1/orders/{old_remote_order_id}") or {}
            if self._storefront_remote_order_matches(remote, desired_customer_id):
                return old_remote_order_id

            payment_states = {
                payment.get("state") for payment in remote.get("payments") or []
            }
            completed = remote.get("state") in {"sale", "done"} or bool(
                payment_states & {"authorized", "done"}
            )
            if not completed and remote.get("state") in {"draft", "sent"}:
                client.post(
                    f"/api/v1/orders/{old_remote_order_id}/cancel",
                    {},
                    idempotency_key=(
                        f"stale-cancel-{attempt_id}-{old_remote_order_id}"
                    ),
                )
            # Completed ERP orders are immutable history. Detach the changed local
            # cart instead of cancelling or reusing the completed transaction.
            self._storefront_clear_remote_checkout()
            attempt_id = str(uuid.uuid4())
            self.x_storefront_attempt_id = attempt_id

        self._storefront_ensure_quote()
        if account:
            customer = {"id": account.x_storefront_remote_customer_id}
        else:
            customer_external = f"odoo-shop-{self.access_token}"
            customer = client.post(
                "/api/v1/customers/upsert",
                {
                    "external_id": customer_external,
                    "name": partner.name,
                    "email": partner.email,
                    "phone": partner.phone,
                    "language": self.x_website_checkout_language or "zh_CN",
                },
                idempotency_key=f"customer-{attempt_id}",
            )
        shipping = self.partner_shipping_id
        remote_address_id = False
        if account and shipping.commercial_partner_id == partner.commercial_partner_id:
            if shipping == shipping.commercial_partner_id:
                remote_address_id = customer["id"]
            elif shipping.shop_api_uuid:
                remote_address_id = shipping.shop_api_uuid
        if not remote_address_id:
            remote_address = client.post(
                f"/api/v1/customers/{customer['id']}/addresses",
                {
                    "name": shipping.name,
                    "street": shipping.street,
                    "street2": shipping.street2,
                    "city": shipping.city,
                    "zip": shipping.zip,
                    "phone": shipping.phone,
                    "country": shipping.country_id.code if shipping.country_id else None,
                    "type": "delivery",
                },
                idempotency_key=f"address-{attempt_id}",
            )
            remote_address_id = remote_address["id"]
            if account and not shipping.shop_api_uuid:
                shipping.with_context(shop_api_skip_event=True).shop_api_uuid = remote_address_id
        shipping_method_id = False
        if self.carrier_id:
            shipping_method_id = self.carrier_id.shop_api_uuid
            if not shipping_method_id:
                raise ValidationError(_("The selected delivery method has no ERP API identifier."))
        order_fingerprint = hashlib.sha256(json.dumps({
            "customer_id": customer["id"],
            "shipping_address_id": remote_address_id,
            "shipping_method_id": shipping_method_id,
            "currency": self.currency_id.name,
            "amount_total": float(self.amount_total),
            "items": self._storefront_aggregate_items(self._storefront_api_items()),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        remote = client.post(
            "/api/v1/orders",
            {
                "reservation_id": self.x_storefront_reservation_id,
                "customer_id": customer["id"],
                "shipping_address_id": remote_address_id,
                "shipping_method_id": shipping_method_id,
                "external_id": f"storefront-{attempt_id}-{order_fingerprint[:24]}",
                "language": self.x_website_checkout_language or "zh_CN",
            },
            idempotency_key=f"order-{attempt_id}-{order_fingerprint[:24]}",
        )
        if remote.get("currency") != self.currency_id.name:
            raise ValidationError(_(
                "The ERP order currency does not match the storefront checkout currency."
            ))
        if self.currency_id.compare_amounts(remote.get("amount_total", 0.0), self.amount_total):
            raise ValidationError(_(
                "The ERP order total does not match the storefront checkout total."
            ))
        self.write({
            "x_storefront_remote_customer_id": customer["id"],
            "x_storefront_remote_order_id": remote["id"],
            "x_storefront_remote_state": remote.get("state"),
        })
        return remote["id"]

    def _storefront_payment_methods(self):
        self.ensure_one()
        language = self.x_website_checkout_language or "zh_CN"
        methods = self.env["storefront.erp.client"].get(
            "/api/v1/payment-methods", params={"lang": language}
        )
        return [item for item in methods or [] if self.currency_id.name in (item.get("currencies") or [])]

    def _storefront_payment_return_url(self):
        self.ensure_one()
        client = self.env["storefront.erp.client"]
        configuration = client.get("/api/v1/shop/configuration") or {}
        shop_base_url = (configuration.get("shop_base_url") or client.public_url()).rstrip("/")
        return f"{shop_base_url}/shop/payment/status"

    def _storefront_create_payment(self, provider_code):
        self.ensure_one()
        remote_order_id = self._storefront_sync_remote_order()
        client = self.env["storefront.erp.client"]
        try:
            response = client.post(
                f"/api/v1/orders/{remote_order_id}/payments",
                {
                    "provider": provider_code,
                    "return_url": self._storefront_payment_return_url(),
                },
                idempotency_key=f"payment-{remote_order_id}-{provider_code}",
                timeout_seconds=client.payment_timeout_seconds(),
            )
        except StorefrontApiError as error:
            if error.code != "erp_unavailable":
                raise
            # A network timeout is ambiguous: ERP may have committed the
            # idempotent command even though its HTTP response did not reach
            # the shop. Recover only from an authoritative ERP read and never
            # create a second charge locally.
            response = {}
            confirmed = self._storefront_recover_payment(
                client, remote_order_id, provider_code,
            )
            if not confirmed:
                raise
            response = {"authoritative": True, "payment": confirmed}
        payment = response.get("payment") or {}
        payment_id = payment.get("id")
        confirmed = client.get(f"/api/v1/payments/{payment_id}") if payment_id else {}
        if not self._storefront_payment_is_authoritative(
            confirmed, remote_order_id, provider_code, payment_id=payment_id,
        ):
            raise StorefrontApiError(
                _("ERP did not authoritatively confirm payment initiation."),
                code="invalid_payment_confirmation", status=502,
            )
        response = {**response, "authoritative": True, "payment": confirmed}
        self.write({
            "x_storefront_remote_payment_id": confirmed["id"],
            "x_storefront_remote_state": confirmed["state"],
            "x_storefront_payment_provider": confirmed["provider"],
            "x_storefront_payment_currency": confirmed["currency"],
            "x_storefront_payment_amount": confirmed["amount"],
        })
        return response

    def _storefront_payment_is_authoritative(
        self, payment, remote_order_id, provider_code, payment_id=None,
    ):
        self.ensure_one()
        is_recorded_payment = bool(
            payment_id and payment_id == self.x_storefront_remote_payment_id
        )
        expected_provider = (
            self.x_storefront_payment_provider if is_recorded_payment
            and self.x_storefront_payment_provider else provider_code
        )
        expected_currency = (
            self.x_storefront_payment_currency if is_recorded_payment
            and self.x_storefront_payment_currency else self.currency_id.name
        )
        expected_amount = (
            self.x_storefront_payment_amount if is_recorded_payment
            and self.x_storefront_payment_currency else self.amount_total
        )
        return bool(
            payment.get("authoritative") is True
            and (not payment_id or payment.get("id") == payment_id)
            and remote_order_id in (payment.get("order_ids") or [])
            and payment.get("provider") == expected_provider
            and payment.get("currency") == expected_currency
            and not self.currency_id.compare_amounts(
                payment.get("amount") or 0.0, expected_amount,
            )
            and payment.get("state") in {"draft", "pending", "authorized", "done"}
        )

    def _storefront_finalize_completed_attempt(self, payment=None):
        """Close the local cart only after a current authoritative ERP payment read."""
        self.ensure_one()
        payment_id = self.x_storefront_remote_payment_id
        remote_order_id = self.x_storefront_remote_order_id
        if not payment_id or not remote_order_id:
            raise StorefrontApiError(
                _("The storefront order has no ERP payment to confirm."),
                code="payment_required", status=409,
            )
        if payment is None:
            payment = self.env["storefront.erp.client"].get(
                f"/api/v1/payments/{payment_id}"
            ) or {}
        provider_code = payment.get("provider")
        if (
            not provider_code
            or payment.get("state") != "done"
            or not self._storefront_payment_is_authoritative(
                payment, remote_order_id, provider_code, payment_id=payment_id,
            )
        ):
            raise StorefrontApiError(
                _("ERP did not authoritatively confirm order completion."),
                code="payment_completion_not_confirmed", status=409,
            )
        if self.x_storefront_completed_at and self.state == "cancel":
            return True

        completed_attempt_id = self._storefront_attempt_key()
        self.with_context(
            shop_api_skip_event=True, tracking_disable=True,
        ).write({
            "x_storefront_completed_attempt_id": completed_attempt_id,
            "x_storefront_attempt_id": str(uuid.uuid4()),
            "x_storefront_completed_at": fields.Datetime.now(),
            "x_storefront_remote_state": "done",
            "x_storefront_reservation_id": False,
            "x_storefront_reservation_expires_at": False,
            "x_storefront_quote_fingerprint": False,
            "x_storefront_shortage_product_uuids": [],
            # ERP owns confirmation, delivery, accounting, and stock. Cancelling
            # only the local presentation cart prevents Odoo from reviving it as
            # an abandoned draft without duplicating those ERP operations.
            "state": "cancel",
        })
        return True

    def _storefront_refresh_payment_completion(self):
        """Read the current ERP payment and finalize this attempt when it is done."""
        self.ensure_one()
        if not self.x_storefront_remote_payment_id:
            return False
        payment = self.env["storefront.erp.client"].get(
            f"/api/v1/payments/{self.x_storefront_remote_payment_id}"
        ) or {}
        provider_code = payment.get("provider")
        if not provider_code or not self._storefront_payment_is_authoritative(
            payment,
            self.x_storefront_remote_order_id,
            provider_code,
            payment_id=self.x_storefront_remote_payment_id,
        ):
            raise StorefrontApiError(
                _("ERP returned an invalid payment status."),
                code="invalid_payment_status", status=502,
            )
        if payment.get("state") == "done":
            self._storefront_finalize_completed_attempt(payment)
            return True
        self.x_storefront_remote_state = payment.get("state")
        return False

    def _storefront_recover_payment(self, client, remote_order_id, provider_code):
        self.ensure_one()
        for delay in (0, 0.25, 0.75, 1.5):
            if delay:
                time.sleep(delay)
            try:
                payments = client.get(
                    f"/api/v1/orders/{remote_order_id}/payments"
                ) or []
            except StorefrontApiError:
                continue
            for payment in reversed(payments):
                if self._storefront_payment_is_authoritative(
                    payment, remote_order_id, provider_code,
                ):
                    return payment
        return {}

    def _storefront_simulate_payment_success(self):
        self.ensure_one()
        if not self.x_storefront_remote_payment_id:
            return {}
        payment = self.env["storefront.erp.client"].post(
            f"/api/v1/payments/{self.x_storefront_remote_payment_id}/simulate-success",
            {},
            idempotency_key=f"payment-simulate-{self.access_token}-{self.x_storefront_remote_payment_id}",
        )
        self.x_storefront_remote_state = payment.get("state")
        return payment


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    @staticmethod
    def _storefront_option_semantic_key(label):
        normalized = re.sub(r"[\s_\-/]+", "", str(label or "").casefold())
        aliases = {
            "color": "color", "colour": "color", "颜色": "color", "颜色分类": "color",
            "size": "size", "尺寸": "size", "尺码": "size", "鞋码": "size",
            "flex": "flex", "硬度": "flex", "款型": "flex",
            "type": "type", "类型": "type", "款式": "type",
        }
        return aliases.get(normalized, normalized)

    def _storefront_selected_options(self, language=None):
        """Return the localized non-colour choices shown beside cart product names."""
        self.ensure_one()
        if self.is_delivery or not self.product_id:
            return []
        language = language or self.env.context.get("lang") or "zh_CN"
        language = "en_US" if str(language).lower().startswith("en") else "zh_CN"
        is_english = language == "en_US"
        product_template = self.product_id.product_tmpl_id
        raw_values = product_template._get_shop_variant_display_values(is_english=False)
        display_values = product_template._get_shop_variant_display_values(
            is_english=is_english
        )
        options = []
        seen_keys = set()

        def add_option(key, label, value):
            semantic_key = self._storefront_option_semantic_key(key or label)
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
            semantic_key = self._storefront_option_semantic_key(label)
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
