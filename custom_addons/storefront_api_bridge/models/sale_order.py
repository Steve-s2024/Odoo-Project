import hashlib
import json

from odoo import _, fields, models
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_storefront_reservation_id = fields.Char(copy=False, readonly=True)
    x_storefront_reservation_expires_at = fields.Datetime(copy=False, readonly=True)
    x_storefront_quote_fingerprint = fields.Char(copy=False, readonly=True)
    x_storefront_remote_customer_id = fields.Char(copy=False, readonly=True)
    x_storefront_remote_order_id = fields.Char(copy=False, readonly=True)
    x_storefront_remote_payment_id = fields.Char(copy=False, readonly=True)
    x_storefront_remote_state = fields.Char(copy=False, readonly=True)
    x_storefront_shortage_product_uuids = fields.Json(copy=False, default=list)

    def _storefront_api_items(self):
        self.ensure_one()
        items = []
        for line in self.order_line.filtered(lambda item: not item.is_delivery and item.product_uom_qty > 0):
            product_uuid = line.product_id.shop_api_uuid
            if not product_uuid:
                raise ValidationError(_("A cart product has no ERP API identifier."))
            items.append({"product_id": product_uuid, "quantity": line.product_uom_qty})
        return items

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

    def _storefront_release_reservation(self):
        self.ensure_one()
        if self.x_storefront_reservation_id:
            try:
                self.env["storefront.erp.client"].post(
                    f"/api/v1/reservations/{self.x_storefront_reservation_id}/release",
                    {}, idempotency_key=f"release-{self.access_token}",
                )
            finally:
                self.write({
                    "x_storefront_reservation_id": False,
                    "x_storefront_reservation_expires_at": False,
                    "x_storefront_quote_fingerprint": False,
                })

    def _storefront_ensure_quote(self):
        self.ensure_one()
        fingerprint = self._storefront_fingerprint()
        now = fields.Datetime.now()
        if (
            self.x_storefront_reservation_id
            and self.x_storefront_quote_fingerprint == fingerprint
            and self.x_storefront_reservation_expires_at
            and self.x_storefront_reservation_expires_at > now
        ):
            return self.x_storefront_reservation_id
        if self.x_storefront_reservation_id:
            self._storefront_release_reservation()
        quote = self.env["storefront.erp.client"].post(
            "/api/v1/checkout/quote",
            {
                "external_id": f"quote-{self.access_token}-{fingerprint[:12]}",
                "language": self.x_website_checkout_language or "zh_CN",
                "items": self._storefront_api_items(),
            },
            idempotency_key=f"quote-{self.access_token}-{fingerprint}",
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
        client = self.env["storefront.erp.client"]
        partner = self.partner_invoice_id
        account = partner.commercial_partner_id.user_ids.filtered(
            lambda user: user.active and user.x_storefront_remote_customer_id
        )[:1]
        desired_customer_id = account.x_storefront_remote_customer_id if account else False
        if (
            self.x_storefront_remote_order_id
            and desired_customer_id
            and self.x_storefront_remote_customer_id != desired_customer_id
        ):
            client.post(
                f"/api/v1/orders/{self.x_storefront_remote_order_id}/cancel",
                {},
                idempotency_key=f"reassign-cancel-{self.access_token}-{self.x_storefront_remote_order_id}",
            )
            self.write({
                "x_storefront_reservation_id": False,
                "x_storefront_reservation_expires_at": False,
                "x_storefront_quote_fingerprint": False,
                "x_storefront_remote_customer_id": False,
                "x_storefront_remote_order_id": False,
                "x_storefront_remote_payment_id": False,
                "x_storefront_remote_state": False,
            })
        elif self.x_storefront_remote_order_id:
            return self.x_storefront_remote_order_id

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
                idempotency_key=f"customer-{self.access_token}",
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
                idempotency_key=f"address-{self.access_token}",
            )
            remote_address_id = remote_address["id"]
            if account and not shipping.shop_api_uuid:
                shipping.with_context(shop_api_skip_event=True).shop_api_uuid = remote_address_id
        shipping_method_id = False
        if self.carrier_id:
            shipping_method_id = self.carrier_id.shop_api_uuid
            if not shipping_method_id:
                raise ValidationError(_("The selected delivery method has no ERP API identifier."))
        remote = client.post(
            "/api/v1/orders",
            {
                "reservation_id": self.x_storefront_reservation_id,
                "customer_id": customer["id"],
                "shipping_address_id": remote_address_id,
                "shipping_method_id": shipping_method_id,
                "external_id": f"storefront-{self.access_token}",
                "language": self.x_website_checkout_language or "zh_CN",
            },
            idempotency_key=f"order-{self.access_token}",
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
        response = self.env["storefront.erp.client"].post(
            f"/api/v1/orders/{remote_order_id}/payments",
            {
                "provider": provider_code,
                "return_url": self._storefront_payment_return_url(),
            },
            idempotency_key=f"payment-{self.access_token}-{provider_code}",
        )
        payment = response.get("payment") or {}
        self.write({
            "x_storefront_remote_payment_id": payment.get("id"),
            "x_storefront_remote_state": payment.get("state"),
        })
        return response

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
