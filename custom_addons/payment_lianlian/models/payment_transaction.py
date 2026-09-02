import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_lianlian import const


_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    _lianlian_merchant_transaction_id_unique = models.Constraint(
        "UNIQUE(lianlian_merchant_transaction_id)",
        "连连商户支付交易 ID 必须唯一。",
    )

    lianlian_merchant_transaction_id = fields.Char(
        string="连连商户支付交易 ID", readonly=True, copy=False, index=True,
    )
    lianlian_transaction_id = fields.Char(
        string="连连支付订单号", readonly=True, copy=False,
    )
    lianlian_payment_url = fields.Char(
        string="连连收银台地址", readonly=True, copy=False,
    )
    lianlian_last_query_at = fields.Datetime(
        string="最近连连查询时间", readonly=True, copy=False,
    )

    def _get_specific_rendering_values(self, processing_values):
        if self.provider_code != "lianlian":
            return super()._get_specific_rendering_values(processing_values)
        self.ensure_one()
        self._lianlian_ensure_payment()
        if self.state == "done":
            return {"api_url": self.landing_route or "/payment/status", "http_method": "get"}
        if not self.lianlian_payment_url:
            raise ValidationError(_("连连没有返回可用的收银台地址。"))
        return {
            "api_url": self.lianlian_payment_url,
            "http_method": "get",
            "form_values": {},
        }

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        if provider_code != "lianlian":
            return super()._get_tx_from_notification_data(provider_code, notification_data)
        merchant_transaction_id = notification_data.get("merchant_transaction_id")
        if not merchant_transaction_id:
            raise ValidationError(_("连连通知缺少商户支付交易 ID。"))
        tx = self.search([
            ("provider_code", "=", "lianlian"),
            ("lianlian_merchant_transaction_id", "=", merchant_transaction_id),
        ], limit=1)
        if not tx:
            raise ValidationError(_("没有找到与连连通知匹配的支付交易。"))
        return tx

    @staticmethod
    def _lianlian_timestamp():
        return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    @staticmethod
    def _lianlian_names(partner):
        name = (partner.name or "Customer").strip()
        pieces = name.split(None, 1)
        first_name = pieces[0][:64]
        last_name = (pieces[1] if len(pieces) > 1 else pieces[0])[:64]
        return first_name, last_name, name[:128]

    @staticmethod
    def _lianlian_product_quantity(quantity):
        """Return the positive integer required by LianLian's PRODUCT schema."""
        try:
            normalized = Decimal(str(quantity))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValidationError(_("连连商品数量必须是正整数。")) from error
        if (
            not normalized.is_finite()
            or normalized <= 0
            or normalized != normalized.to_integral_value()
        ):
            raise ValidationError(_("连连商品数量必须是正整数。"))
        return int(normalized)

    @staticmethod
    def _lianlian_checkout_language(sale_order, partner):
        """Return the language explicitly selected by the storefront.

        Shop-created ERP orders persist this value at the mandatory order
        confirmation boundary.  A partner-language fallback is retained only
        for legacy or manually-created orders; delivery country must not alter
        the customer's chosen checkout language.
        """
        language = (
            getattr(sale_order, "x_website_checkout_language", False)
            if sale_order else False
        ) or partner.lang or "zh_CN"
        return "en_US" if str(language).lower().startswith("en") else "zh_CN"

    @staticmethod
    def _lianlian_without_product_code(value, product):
        """Remove any legacy SKU accidentally embedded in display text."""
        text = str(value or "").strip()
        if not product:
            return text
        codes = {
            str(product.default_code or "").strip(),
            str(product.product_tmpl_id.default_code or "").strip(),
        }
        for code in sorted(codes - {""}, key=len, reverse=True):
            text = text.replace(f"[{code}]", "").replace(code, "")
        return " ".join(text.split()).strip(" -:;：；")

    def _lianlian_product_presentation(self, line, language):
        """Build the localized cart-style name and selected-option subtitle."""
        self.ensure_one()
        is_english = language == "en_US"
        product = line.product_id
        is_delivery_method = getattr(line, "_shop_api_is_delivery_line", None)
        is_delivery = (
            is_delivery_method() if callable(is_delivery_method)
            else bool(getattr(line, "is_delivery", False))
        )

        display_method = getattr(line, "_shop_api_display_name", None)
        if callable(display_method):
            name = display_method(language=language)
        elif is_delivery:
            name = "Standard delivery" if is_english else "标准送货"
        elif product:
            template = product.product_tmpl_id
            website_name_method = getattr(template, "_get_website_display_name", None)
            if callable(website_name_method):
                name = website_name_method(is_english=is_english)
            else:
                name = template.with_context(lang=language).name
        else:
            name = line.with_context(lang=language).name
        name = self._lianlian_without_product_code(name, product)
        if not name:
            name = "Product" if is_english else "商品"

        options = []
        option_method = getattr(line, "_shop_api_selected_options", None)
        if callable(option_method) and not is_delivery:
            options = [dict(option) for option in option_method(language=language)]

        # Cart/purchase helpers intentionally omit colour when the product
        # image already communicates it.  LianLian has no product picture in
        # its order summary, so include the selected colour explicitly there.
        template = product.product_tmpl_id if product else False
        variant_method = getattr(template, "_get_shop_variant_display_values", None)
        if callable(variant_method) and not is_delivery:
            raw_values = variant_method(is_english=False)
            display_values = variant_method(is_english=is_english)
            raw_color = str(raw_values.get("color") or "").strip()
            if raw_color not in {"", "未识别", "默认"} and not any(
                option.get("key") == "color" for option in options
            ):
                color_option = {
                    "key": "color",
                    "label": "Color" if is_english else "颜色",
                    "value": display_values.get("color"),
                }
                insert_at = 1 if options and options[0].get("key") == "type" else 0
                options.insert(insert_at, color_option)

        separator = "; " if is_english else "；"
        label_separator = ": " if is_english else "："
        option_text = separator.join(
            f"{option.get('label')}{label_separator}{option.get('value')}"
            for option in options
            if option.get("label") and option.get("value")
        )
        description = self._lianlian_without_product_code(option_text, product) or name

        # LianLian requires product_id/SKU-like identifiers.  Use a stable,
        # opaque per-line token so internal ERP product codes never leave ERP.
        opaque_id = hashlib.sha256(
            f"{self.company_id.id}:{line.order_id.id}:{line.id}".encode("utf-8")
        ).hexdigest()[:24]
        return name[:128], description[:256], f"ITEM-{opaque_id}"

    def _lianlian_callback_url(self):
        self.ensure_one()
        base = self.provider_id.sudo().lianlian_callback_base_url.rstrip("/")
        return f"{base}{const.NOTIFY_ROUTE}"

    def _lianlian_redirect_url(self):
        self.ensure_one()
        return self.landing_route or f"{self.provider_id.get_base_url().rstrip('/')}{const.RETURN_ROUTE}"

    def _lianlian_cancel_url(self):
        self.ensure_one()
        redirect_url = self._lianlian_redirect_url()
        return redirect_url if "?" not in redirect_url else redirect_url.split("?", 1)[0]

    def _lianlian_order_payload(self):
        self.ensure_one()
        provider = self.provider_id.sudo()
        merchant_transaction_id = (
            self.lianlian_merchant_transaction_id or f"ODOO-{self.company_id.id}-{self.id}"
        )[:64]
        if not self.lianlian_merchant_transaction_id:
            self.lianlian_merchant_transaction_id = merchant_transaction_id

        sale_order = self.sale_order_ids[:1]
        partner = (sale_order.partner_id if sale_order else self.partner_id).sudo()
        shipping_partner = (
            sale_order.partner_shipping_id if sale_order else partner
        ).sudo()
        first_name, last_name, full_name = self._lianlian_names(partner)
        shipping_first, shipping_last, shipping_name = self._lianlian_names(shipping_partner)
        email = (partner.email or "").strip()
        if provider.lianlian_environment == "sandbox" and provider.lianlian_sandbox_customer_email:
            email = provider.lianlian_sandbox_customer_email.strip()
        phone = (
            partner.phone or getattr(partner, "mobile", False) or ""
        ).strip()
        if not email and not phone:
            raise ValidationError(_("连连收银台要求客户至少填写邮箱或手机号。"))

        language = self._lianlian_checkout_language(sale_order, partner)
        is_english = language == "en_US"
        products = []
        if sale_order:
            for line in sale_order.order_line.filtered(lambda item: not item.display_type):
                product = line.product_id
                name, description, opaque_item_id = self._lianlian_product_presentation(
                    line, language,
                )
                products.append({
                    "product_id": opaque_item_id,
                    "name": name,
                    "description": description,
                    "price": f"{line.price_unit:.2f}",
                    "quantity": self._lianlian_product_quantity(line.product_uom_qty),
                    "sku": opaque_item_id,
                    "url": urljoin(
                        f"{self.provider_id.get_base_url().rstrip('/')}/",
                        ("en/shop" if is_english else "shop"),
                    ),
                    "shipping_provider": "other",
                })
        if not products:
            opaque_payment_id = hashlib.sha256(
                f"{self.company_id.id}:{self.id}".encode("utf-8")
            ).hexdigest()[:24]
            products = [{
                "product_id": f"ITEM-{opaque_payment_id}",
                "name": "Order payment" if is_english else "订单支付",
                "description": "Order payment" if is_english else "订单支付",
                "price": f"{self.amount:.2f}",
                "quantity": 1,
                "sku": f"ITEM-{opaque_payment_id}",
                "url": urljoin(
                    f"{self.provider_id.get_base_url().rstrip('/')}/",
                    ("en/shop" if is_english else "shop"),
                ),
                "shipping_provider": "other",
            }]

        country_code = (
            shipping_partner.country_id.code or provider.lianlian_merchant_country or "CN"
        ).upper()
        address = {
            "line1": (shipping_partner.street or "N/A")[:256],
            "line2": (shipping_partner.street2 or "")[:256],
            "country": country_code,
            "city": (shipping_partner.city or "N/A")[:128],
            "state": (shipping_partner.state_id.code or shipping_partner.state_id.name or "")[:64],
            "postal_code": (shipping_partner.zip or "000000")[:32],
        }
        merchant_order_id = (sale_order.name if sale_order else self.reference)[:64]
        return {
            "merchant_id": provider.lianlian_merchant_id,
            "sub_merchant_id": provider.lianlian_sub_merchant_id,
            "merchant_transaction_id": merchant_transaction_id,
            # Cashier mode keeps raw cardholder data entirely on LianLian's
            # hosted page.  Selecting the international-card method here
            # avoids an empty payment-method chooser and guarantees that the
            # customer is asked for card details by the PCI-scoped provider.
            "payment_method": "inter_credit_card",
            "notification_url": self._lianlian_callback_url(),
            "redirect_url": self._lianlian_redirect_url(),
            "cancel_url": self._lianlian_cancel_url(),
            "country": (provider.lianlian_merchant_country or "CN").upper(),
            "merchant_order": {
                "merchant_order_id": merchant_order_id,
                "merchant_user_no": str(partner.id),
                "merchant_order_time": self._lianlian_timestamp(),
                "order_description": (
                    (f"Order {sale_order.name}" if is_english else f"订单 {sale_order.name}")
                    if sale_order else ("Order payment" if is_english else "订单支付")
                )[:256],
                "order_amount": f"{self.amount:.2f}",
                "order_currency_code": self.currency_id.name,
                "products": products,
                "shipping": {
                    "first_name": shipping_first,
                    "last_name": shipping_last,
                    "name": shipping_name,
                    "phone": phone or "00000000000",
                    "cycle": "48h",
                    "address": address,
                },
            },
            "customer": {
                "customer_type": "I",
                "first_name": first_name,
                "last_name": last_name,
                "full_name": full_name,
                "email": email,
                "phone": phone,
            },
        }

    def _lianlian_ensure_payment(self):
        self.ensure_one()
        if self.lianlian_payment_url or self.state == "done":
            return
        response = self.provider_id.sudo()._lianlian_payment_request(
            self._lianlian_order_payload()
        )
        order = response.get("order") or {}
        if not order:
            raise ValidationError(_("连连创单成功响应中没有订单数据。"))
        self._lianlian_apply_provider_order(order)

    def _lianlian_apply_provider_order(self, order):
        self.ensure_one()
        merchant_transaction_id = order.get("merchant_transaction_id")
        if merchant_transaction_id != self.lianlian_merchant_transaction_id:
            raise ValidationError(_("连连返回的商户支付交易 ID 不匹配。"))
        payment_data = order.get("payment_data") or {}
        payment_currency = payment_data.get("payment_currency_code")
        if payment_currency and payment_currency != self.currency_id.name:
            raise ValidationError(_("连连返回的支付币种不匹配。"))
        payment_amount = payment_data.get("payment_amount")
        if payment_amount not in (None, "") and self.currency_id.compare_amounts(
            float(payment_amount), self.amount,
        ):
            raise ValidationError(_("连连返回的支付金额不匹配。"))
        payment_url = order.get("payment_url") or self.lianlian_payment_url
        if payment_url and not self.provider_id.sudo()._lianlian_checkout_url_is_allowed(payment_url):
            raise ValidationError(_("连连返回了不受信任的收银台地址。"))
        self.write({
            "lianlian_transaction_id": order.get("ll_transaction_id") or self.lianlian_transaction_id,
            "lianlian_payment_url": payment_url,
            "provider_reference": order.get("ll_transaction_id") or self.provider_reference,
        })
        self._process("lianlian", order)

    def _lianlian_refresh_status(self, force=False):
        self.ensure_one()
        if self.provider_code != "lianlian" or self.state in ("done", "cancel"):
            return self.state
        if not self.lianlian_merchant_transaction_id:
            return self.state
        now = fields.Datetime.now()
        if (
            not force and self.lianlian_last_query_at
            and (now - self.lianlian_last_query_at).total_seconds() < 5
        ):
            return self.state
        self.lianlian_last_query_at = now
        if self.operation == "refund":
            response = self.provider_id.sudo()._lianlian_refund_query(
                self.lianlian_merchant_transaction_id
            )
            order = response.get("order") or {}
            if order:
                self._lianlian_apply_refund_order(order)
            return self.state
        response = self.provider_id.sudo()._lianlian_payment_query(
            self.lianlian_merchant_transaction_id
        )
        order = response.get("order") or {}
        if order:
            self._lianlian_apply_provider_order(order)
        return self.state

    def _lianlian_apply_refund_order(self, order):
        """Validate and process a signed refund response or query result."""
        self.ensure_one()
        merchant_transaction_id = order.get("merchant_transaction_id")
        if merchant_transaction_id != self.lianlian_merchant_transaction_id:
            raise ValidationError(_("连连返回的退款交易 ID 不匹配。"))
        source = self.source_transaction_id
        original_transaction_id = order.get("original_transaction_id")
        if (
            original_transaction_id
            and source
            and original_transaction_id != source.lianlian_merchant_transaction_id
        ):
            raise ValidationError(_("连连返回的原支付交易 ID 不匹配。"))
        refund_data = order.get("refund_data") or {}
        refund_currency = (
            refund_data.get("actual_refund_currency_code")
            or refund_data.get("refund_currency_code")
        )
        if refund_currency and refund_currency != self.currency_id.name:
            raise ValidationError(_("连连返回的退款币种不匹配。"))
        refund_amount = (
            refund_data.get("actual_refund_amount")
            or refund_data.get("refund_amount")
        )
        if refund_amount not in (None, "") and self.currency_id.compare_amounts(
            float(refund_amount), -self.amount,
        ):
            raise ValidationError(_("连连返回的退款金额不匹配。"))
        self.write({
            "lianlian_transaction_id": (
                order.get("ll_transaction_id") or self.lianlian_transaction_id
            ),
            "provider_reference": order.get("ll_transaction_id") or self.provider_reference,
        })
        self._process("lianlian", order)

    @api.model
    def _cron_lianlian_reconcile_pending_refunds(self):
        """Query signed provider state when a refund notification is delayed."""
        transactions = self.sudo().search([
            ("provider_code", "=", "lianlian"),
            ("operation", "=", "refund"),
            ("state", "=", "pending"),
            ("lianlian_merchant_transaction_id", "!=", False),
        ], order="id", limit=100)
        for transaction in transactions:
            try:
                with self.env.cr.savepoint():
                    transaction._lianlian_refresh_status(force=True)
            except Exception:
                _logger.exception(
                    "Unable to reconcile pending LianLian refund transaction %s.",
                    transaction.id,
                )

    def _extract_amount_data(self, payment_data):
        if self.provider_code != "lianlian":
            return super()._extract_amount_data(payment_data)
        values = payment_data.get("refund_data") or payment_data.get("payment_data") or {}
        amount = values.get("actual_refund_amount") or values.get("refund_amount")
        currency = values.get("actual_refund_currency_code") or values.get("refund_currency_code")
        if amount is None:
            amount = values.get("payment_amount")
            currency = values.get("payment_currency_code")
        if amount in (None, ""):
            return None
        return {
            "amount": float(amount),
            "currency_code": currency or self.currency_id.name,
            "precision_digits": 2,
        }

    def _apply_updates(self, payment_data):
        if self.provider_code != "lianlian":
            return super()._apply_updates(payment_data)
        values = payment_data.get("refund_data") or payment_data.get("payment_data") or {}
        self.provider_reference = (
            payment_data.get("ll_transaction_id")
            or self.provider_reference
            or self.lianlian_transaction_id
        )
        status = values.get("refund_status") if self.operation == "refund" else values.get("payment_status")
        if status == ("RS" if self.operation == "refund" else "PS"):
            self._set_done()
        elif self.operation != "refund" and status == "PF":
            self._set_error(_("连连支付失败。"))
        elif status in (
            ("RP", "PP", None, "")
            if self.operation == "refund"
            else ("IN", "PP", "WP", None, "")
        ):
            self._set_pending(
                extra_allowed_states=("error",) if self.operation == "refund" else (),
            )
        else:
            self._set_error(_("连连返回了未支持的支付状态：%s", status))

    def _send_refund_request(self):
        if self.provider_code != "lianlian":
            return super()._send_refund_request()
        self.ensure_one()
        source = self.source_transaction_id
        if not source or not source.lianlian_merchant_transaction_id:
            raise ValidationError(_("找不到连连原支付交易。"))
        merchant_refund_id = (
            self.lianlian_merchant_transaction_id
            or f"ODOO-REF-{self.company_id.id}-{self.id}"
        )[:64]
        if not self.lianlian_merchant_transaction_id:
            self.lianlian_merchant_transaction_id = merchant_refund_id
        response = self.provider_id.sudo()._lianlian_refund_request({
            "merchant_id": self.provider_id.lianlian_merchant_id,
            "sub_merchant_id": self.provider_id.lianlian_sub_merchant_id,
            "merchant_transaction_id": merchant_refund_id,
            "merchant_refund_time": self._lianlian_timestamp(),
            "original_transaction_id": source.lianlian_merchant_transaction_id,
            "notification_url": self._lianlian_callback_url(),
            "refund_data": {
                "refund_amount": f"{-self.amount:.2f}",
                "refund_currency_code": self.currency_id.name,
                "reason": f"Odoo refund {source.reference}"[:256],
            },
        })
        order = response.get("order") or {}
        if not order:
            raise ValidationError(_("连连退款响应中没有订单数据。"))
        self._lianlian_apply_refund_order(order)
        if self.state == "done":
            self._post_process()
