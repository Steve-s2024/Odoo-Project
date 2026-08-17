import logging
import uuid

from odoo import fields
from odoo.http import content_disposition, request, route
from odoo.addons.stock_subwarehouse_hierarchy.controllers.purchase_history import (
    WebsitePurchaseHistory,
)

from ..models.api_client import StorefrontApiError


_logger = logging.getLogger(__name__)


class StorefrontCustomerPortal(WebsitePurchaseHistory):
    @staticmethod
    def _remote_customer_id():
        if request.env.user._is_public() or request.env.user._is_internal():
            return False
        return request.env.user.sudo().x_storefront_remote_customer_id

    @staticmethod
    def _is_english():
        language = getattr(request, "lang", None)
        return bool((getattr(language, "code", "") or "").lower().startswith("en"))

    @classmethod
    def _language(cls):
        return "en_US" if cls._is_english() else "zh_CN"

    @staticmethod
    def _payment_method_label(provider_code, is_english):
        labels = {
            "wechatpay": ("WeChat Pay", "微信支付"),
            "alipay": ("Alipay", "支付宝"),
            "stripe": ("Stripe", "Stripe"),
            "demo": ("Demo payment", "模拟支付"),
        }
        return labels.get(provider_code, (provider_code or "—", provider_code or "—"))[
            0 if is_english else 1
        ]

    @staticmethod
    def _status_label(order, is_english):
        payment_state = order.get("payment_state")
        state = order.get("state")
        if payment_state == "expired" or order.get("payment_expired"):
            return "Expired" if is_english else "已过期"
        if payment_state == "paid":
            delivery_state = order.get("delivery_state")
            labels = {
                "awaiting_delivery": ("Awaiting dispatch", "待发货"),
                "delivering": ("Delivering", "配送中"),
                "delivered": ("Delivered", "已送达"),
            }
            return labels.get(delivery_state, ("Paid", "已支付"))[0 if is_english else 1]
        if payment_state == "pending":
            return "Awaiting payment" if is_english else "待支付"
        if state == "cancel":
            return "Cancelled" if is_english else "已取消"
        return "Awaiting payment" if is_english else "待支付"

    @staticmethod
    def _payment_label(order, is_english):
        labels = {
            "unpaid": ("Unpaid", "未支付"),
            "pending": ("Awaiting payment", "待支付"),
            "paid": ("Paid", "已支付"),
            "expired": ("Expired", "已过期"),
            "error": ("Payment failed", "支付失败"),
        }
        return labels.get(order.get("payment_state"), ("Unknown", "未知"))[0 if is_english else 1]

    @staticmethod
    def _refund_label(refund, is_english):
        labels = {
            "requested": ("Awaiting review", "待审核"),
            "returning": ("Awaiting product return", "等待退货"),
            "return_received": ("Return received", "退货已收货"),
            "return_cancelled": ("Return arrangement cancelled", "退货安排已取消"),
            "processing": ("Refund processing", "退款处理中"),
            "refunded": ("Refunded", "已退款"),
            "failed": ("Refund failed", "退款失败"),
            "rejected": ("Refund rejected", "已拒绝"),
        }
        return labels.get(refund.get("state"), ("Unknown", "未知"))[0 if is_english else 1]

    @staticmethod
    def _delivery_label(order, is_english):
        labels = {
            "awaiting_delivery": ("Awaiting dispatch", "待发货"),
            "delivering": ("Delivering", "配送中"),
            "delivered": ("Delivered", "已送达"),
        }
        return labels.get(order.get("delivery_state"), ("—", "—"))[0 if is_english else 1]

    @staticmethod
    def _return_delivery_label(refund, is_english):
        labels = {
            "awaiting_delivery": ("Awaiting return dispatch", "等待退货发出"),
            "delivering": ("Return in transit", "退货运输中"),
            "delivered": ("Return delivered", "退货已送达"),
        }
        return labels.get(refund.get("return_delivery_state"), ("—", "—"))[
            0 if is_english else 1
        ]

    @staticmethod
    def _currency_symbol(currency_code):
        return {
            "CNY": "￥",
            "USD": "$",
        }.get((currency_code or "").upper(), currency_code or "")

    def _login_redirect(self):
        target = request.httprequest.full_path.rstrip("?")
        return request.redirect(f"/web/login?redirect={target}")

    def _remote_order(self, order_id):
        session = getattr(request, "session", {})
        session_completed_order = session.get("x_storefront_completed_order_id")
        if request.env.user._is_public() and str(order_id) != str(session_completed_order or ""):
            return None
        internal_user = request.env.user._is_internal()
        customer_id = self._remote_customer_id()
        session_authorized = str(order_id) == str(session_completed_order or "")
        if not internal_user and not customer_id and not session_authorized:
            return None
        try:
            order = request.env["storefront.erp.client"].get(
                f"/api/v1/orders/{order_id}",
                params={"language": self._language()},
            )
        except StorefrontApiError:
            return None
        # ERP-authorized internal website editors may inspect any storefront
        # order. Portal users remain restricted to their own ERP customer ID.
        return order if (
            internal_user
            or session_authorized
            or order.get("customer_id") == customer_id
        ) else None

    @staticmethod
    def _can_view_remote_orders():
        user = request.env.user
        return not user._is_public() and (
            user._is_internal() or bool(user.sudo().x_storefront_remote_customer_id)
        )

    @staticmethod
    def _remote_order_is_payable(order):
        """Accept only an explicitly unpaid, non-cancelled ERP order."""
        if not order:
            return False
        try:
            expires_at = fields.Datetime.to_datetime(order.get("payment_expires_at"))
        except (TypeError, ValueError):
            expires_at = False
        return bool(
            order.get("state") in {"draft", "sent"}
            and order.get("payment_state") in {"unpaid", "pending", "error"}
            and order.get("payment_expired") is not True
            and expires_at
            and expires_at > fields.Datetime.now()
        )

    def _resumable_local_order(self, order):
        """Return the current account's matching Shop presentation order.

        ERP remains authoritative for the order/payment state.  The local
        record is used only to prove that this Shop account owns the checkout
        presentation that can be restored.  This also lets an internal website
        editor resume an order it actually placed without allowing that editor
        to pay an arbitrary customer order visible through the admin history.
        """
        if request.env.user._is_public() or not self._remote_order_is_payable(order):
            return request.env["sale.order"].browse()
        customer_id = order.get("customer_id")
        if not customer_id:
            return request.env["sale.order"].browse()
        return request.env["sale.order"].sudo().search([
            ("x_storefront_remote_order_id", "=", order.get("id")),
            ("x_storefront_remote_customer_id", "=", customer_id),
            (
                "partner_id.commercial_partner_id",
                "=",
                request.env.user.partner_id.commercial_partner_id.id,
            ),
            ("website_id", "=", request.website.id),
            ("state", "=", "draft"),
        ], order="id desc", limit=1)

    def _render_purchase_history_page(self, orders, error=False):
        is_english = self._is_english()
        return request.render("storefront_api_bridge.remote_purchase_history_page", {
            "orders": orders,
            "is_english": is_english,
            "portal_error": error,
            "status_label": self._status_label,
            "currency_symbol": self._currency_symbol,
            "additional_title": "Purchase History" if is_english else "购买记录",
        })

    @staticmethod
    def _all_storefront_orders():
        client = request.env["storefront.erp.client"]
        page = 1
        orders = []
        while True:
            rows, meta = client.call("GET", "/api/v1/orders", params={
                "page": page, "page_size": 100,
                "language": StorefrontCustomerPortal._language(),
            })
            orders.extend(rows or [])
            if len(orders) >= int((meta or {}).get("total") or len(orders)):
                return orders
            page += 1

    @route()
    def purchase_history(self, **kwargs):
        customer_id = self._remote_customer_id()
        if not customer_id:
            # Internal accounts are ERP-authorized website editors, not shop
            # customers. Load the client-wide ERP history instead of redirecting
            # them back and forth between /purchase-history and /web/login.
            if request.env.user._is_internal():
                try:
                    orders = self._all_storefront_orders()
                    error = False
                except StorefrontApiError as exc:
                    orders = []
                    error = str(exc)
                return self._render_purchase_history_page(orders, error=error)
            return self._login_redirect()
        try:
            orders = request.env["storefront.erp.client"].get(
                f"/api/v1/customers/{customer_id}/orders",
                params={"page_size": 100, "language": self._language()},
            ) or []
            error = False
        except StorefrontApiError as exc:
            orders = []
            error = str(exc)
        return self._render_purchase_history_page(orders, error=error)

    @route()
    def purchase_detail(self, order_id, **kwargs):
        return self._render_remote_detail(str(order_id))

    @route(
        ["/purchase-detail/<string:order_uuid>", "/purchase-details/<string:order_uuid>"],
        type="http", auth="public", website=True, sitemap=False,
    )
    def remote_purchase_detail(self, order_uuid, **kwargs):
        return self._render_remote_detail(order_uuid)

    def _render_remote_detail(self, order_id):
        if not self._can_view_remote_orders():
            return self._login_redirect()
        order = self._remote_order(order_id)
        if not order:
            return request.redirect("/purchase-history")
        try:
            refunds = request.env["storefront.erp.client"].get(
                f"/api/v1/orders/{order_id}/refund-requests",
                params={"language": self._language()},
            ) or []
        except StorefrontApiError:
            refunds = []
        try:
            documents = request.env["storefront.erp.client"].get(
                f"/api/v1/orders/{order_id}/documents"
            ) or {}
        except StorefrontApiError:
            documents = {}
        is_english = self._is_english()
        completed_payments = [
            payment for payment in (order.get("payments") or [])
            if payment.get("state") == "done" and payment.get("operation") != "refund"
        ]
        payment = completed_payments[-1] if completed_payments else (
            (order.get("payments") or [])[-1] if order.get("payments") else {}
        )
        can_pay = bool(self._resumable_local_order(order))
        resume_payment_error = getattr(request, "session", {}).pop(
            "x_storefront_resume_payment_error", False
        )
        return request.render("storefront_api_bridge.remote_purchase_detail_page", {
            "order": order,
            "refunds": refunds,
            "is_english": is_english,
            "status": self._status_label(order, is_english),
            "payment_status": self._payment_label(order, is_english),
            "delivery_status": self._delivery_label(order, is_english),
            "payment": payment,
            "payment_method": self._payment_method_label(
                payment.get("provider"), is_english
            ) if payment else "—",
            "documents": documents,
            "can_pay": can_pay,
            "resume_payment_error": resume_payment_error,
            "refund_label": self._refund_label,
            "return_delivery_label": self._return_delivery_label,
            "currency_symbol": self._currency_symbol,
            "additional_title": "Purchase Details" if is_english else "购买详情",
        })

    @route(
        "/purchase-detail/<string:order_uuid>/pay",
        type="http", auth="user", website=True, methods=["POST"], sitemap=False,
    )
    def resume_remote_payment(self, order_uuid, **post):
        """Restore only the owner's ERP-confirmed local presentation cart."""
        order = self._remote_order(order_uuid)
        if not order or not self._remote_order_is_payable(order):
            return request.redirect(f"/purchase-detail/{order_uuid}")

        local_order = self._resumable_local_order(order)
        if not local_order:
            request.session["x_storefront_resume_payment_error"] = True
            return request.redirect(f"/purchase-detail/{order_uuid}")

        payments = [
            item for item in (order.get("payments") or [])
            if item.get("operation") != "refund"
            and item.get("state") in {"draft", "pending", "authorized"}
        ]
        payment = payments[-1] if payments else {}
        if payment:
            provider = payment.get("provider")
            if not provider or not local_order._storefront_payment_is_authoritative(
                payment,
                order_uuid,
                provider,
                payment_id=payment.get("id"),
            ):
                request.session["x_storefront_resume_payment_error"] = True
                return request.redirect(f"/purchase-detail/{order_uuid}")
            local_order.write({
                "x_storefront_remote_payment_id": payment["id"],
                "x_storefront_remote_state": payment.get("state"),
                "x_storefront_payment_provider": payment.get("provider"),
                "x_storefront_payment_currency": payment.get("currency"),
                "x_storefront_payment_amount": payment.get("amount") or 0.0,
            })

        request.session["sale_order_id"] = local_order.id
        request.session["website_sale_cart_quantity"] = int(local_order.cart_quantity)
        request.session["x_storefront_pending_local_order_id"] = local_order.id
        request.session["x_storefront_pending_order_id"] = order_uuid
        request.session["x_storefront_pending_payment_id"] = payment.get("id") or False
        request.session.pop("x_storefront_completed_payment_state", None)
        return request.redirect(
            "/shop/payment/status" if payment else "/shop/payment"
        )

    @route()
    def refund_item(self, order_id, **kwargs):
        return self._render_remote_refund(str(order_id))

    @route(
        "/refund-item/<string:order_uuid>",
        type="http", auth="public", website=True, sitemap=False,
    )
    def remote_refund_item(self, order_uuid, **kwargs):
        return self._render_remote_refund(order_uuid)

    def _render_remote_refund(self, order_id):
        if not self._can_view_remote_orders():
            return self._login_redirect()
        order = self._remote_order(order_id)
        if not order:
            return request.redirect("/purchase-history")
        try:
            refunds = request.env["storefront.erp.client"].get(
                f"/api/v1/orders/{order_id}/refund-requests",
                params={"language": self._language()},
            ) or []
        except StorefrontApiError:
            refunds = []
        is_english = self._is_english()
        session = getattr(request, "session", {})
        return request.render("storefront_api_bridge.remote_refund_item_page", {
            "order": order,
            "refunds": refunds,
            "is_english": is_english,
            "refund_label": self._refund_label,
            "return_delivery_label": self._return_delivery_label,
            "currency_symbol": self._currency_symbol,
            "refund_attempt_id": str(uuid.uuid4()),
            "refund_flash": session.pop("x_storefront_refund_flash", False),
            "additional_title": "Refund Items" if is_english else "申请退款",
        })

    @route()
    def submit_refund_item(self, order_id, **post):
        return self._submit_remote_refund(str(order_id), post)

    @route(
        "/refund-item/<string:order_uuid>/submit",
        type="http", auth="public", website=True, methods=["POST"], sitemap=False,
    )
    def remote_submit_refund_item(self, order_uuid, **post):
        return self._submit_remote_refund(order_uuid, post)

    def _submit_remote_refund(self, order_id, post):
        if not self._can_view_remote_orders():
            return self._login_redirect()
        order = self._remote_order(order_id)
        if not order:
            return request.redirect("/purchase-history")
        items = []
        for item in order.get("items") or []:
            if not item.get("refundable"):
                continue
            raw = post.get(f"quantity_{item.get('product_id')}")
            try:
                quantity = float(raw or 0)
            except (TypeError, ValueError):
                quantity = 0
            if quantity > 0:
                items.append({
                    "product_id": item["product_id"],
                    "quantity": min(quantity, float(item.get("quantity") or 0)),
                })
        session = getattr(request, "session", {})
        if not items:
            session["x_storefront_refund_flash"] = "failure"
            return request.redirect(f"/refund-item/{order_id}")
        attempt_id = post.get("refund_attempt_id")
        try:
            attempt_id = str(uuid.UUID(str(attempt_id)))
        except (TypeError, ValueError, AttributeError):
            session["x_storefront_refund_flash"] = "failure"
            return request.redirect(f"/refund-item/{order_id}")
        expected_quantities = {
            item["product_id"]: float(item["quantity"])
            for item in items
        }
        client = request.env["storefront.erp.client"]
        try:
            created = client.post(
                f"/api/v1/orders/{order_id}/refund-requests",
                {"items": items},
                idempotency_key=f"portal-refund-{order_id}-{attempt_id}",
            )
            refund_id = created.get("id") if isinstance(created, dict) else False
            if (
                not refund_id
                or created.get("authoritative") is not True
                or created.get("order_id") != order_id
            ):
                raise StorefrontApiError(
                    "ERP did not authoritatively confirm the refund request.",
                    code="refund_confirmation_invalid",
                    status=502,
                )
            confirmed = client.get(f"/api/v1/refund-requests/{refund_id}")
            confirmed_quantities = {
                item.get("product_id"): float(item.get("quantity") or 0)
                for item in (confirmed.get("items") or [])
            } if isinstance(confirmed, dict) else {}
            if (
                not isinstance(confirmed, dict)
                or confirmed.get("authoritative") is not True
                or confirmed.get("id") != refund_id
                or confirmed.get("order_id") != order_id
                or confirmed.get("review_state") != "requested"
                or confirmed_quantities != expected_quantities
            ):
                raise StorefrontApiError(
                    "ERP refund confirmation could not be verified.",
                    code="refund_readback_mismatch",
                    status=502,
                )
        except (StorefrontApiError, TypeError, ValueError, AttributeError) as exc:
            _logger.warning(
                "ERP refund request was not confirmed for order %s: %s (%s)",
                order_id,
                getattr(exc, "code", "refund_response_invalid"),
                getattr(exc, "status", 502),
            )
            session["x_storefront_refund_flash"] = "failure"
            return request.redirect(f"/refund-item/{order_id}")
        session["x_storefront_refund_flash"] = "success"
        return request.redirect(f"/refund-item/{order_id}")

    @route(
        "/purchase-document/<string:order_uuid>/receipt.pdf",
        type="http", auth="public", website=True, sitemap=False,
    )
    def remote_payment_receipt(self, order_uuid, **kwargs):
        order = self._remote_order(order_uuid)
        if not order:
            return self._login_redirect() if request.env.user._is_public() else request.not_found()
        try:
            pdf = request.env["storefront.erp.client"].get_binary(
                f"/api/v1/orders/{order_uuid}/receipt.pdf"
            )
        except StorefrontApiError:
            return request.not_found()
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", str(len(pdf))),
            ("Content-Disposition", content_disposition(
                f"payment-receipt-{order.get('number') or order_uuid}.pdf"
            )),
        ])

    @route(
        "/purchase-document/<string:order_uuid>/invoice/<string:invoice_uuid>.pdf",
        type="http", auth="public", website=True, sitemap=False,
    )
    def remote_invoice_download(self, order_uuid, invoice_uuid, **kwargs):
        order = self._remote_order(order_uuid)
        if not order:
            return self._login_redirect() if request.env.user._is_public() else request.not_found()
        try:
            pdf = request.env["storefront.erp.client"].get_binary(
                f"/api/v1/orders/{order_uuid}/invoices/{invoice_uuid}.pdf"
            )
        except StorefrontApiError:
            return request.not_found()
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", str(len(pdf))),
            ("Content-Disposition", content_disposition(f"invoice-{invoice_uuid}.pdf")),
        ])
