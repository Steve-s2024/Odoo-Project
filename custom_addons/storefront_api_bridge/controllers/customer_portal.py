import logging
import uuid

from odoo.http import request, route
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
        return bool((request.lang and request.lang.code or "").lower().startswith("en"))

    @staticmethod
    def _status_label(order, is_english):
        payment_state = order.get("payment_state")
        state = order.get("state")
        if payment_state == "paid":
            return "Completed" if is_english else "已完成"
        if payment_state == "pending":
            return "Payment processing" if is_english else "支付处理中"
        if state == "cancel":
            return "Cancelled" if is_english else "已取消"
        return "Awaiting payment" if is_english else "待支付"

    @staticmethod
    def _payment_label(order, is_english):
        labels = {
            "unpaid": ("Unpaid", "未支付"),
            "pending": ("Payment processing", "支付处理中"),
            "paid": ("Paid", "已支付"),
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
    def _currency_symbol(currency_code):
        return {
            "CNY": "￥",
            "USD": "$",
        }.get((currency_code or "").upper(), currency_code or "")

    def _login_redirect(self):
        target = request.httprequest.full_path.rstrip("?")
        return request.redirect(f"/web/login?redirect={target}")

    def _remote_order(self, order_id):
        if request.env.user._is_public():
            return None
        internal_user = request.env.user._is_internal()
        customer_id = self._remote_customer_id()
        if not internal_user and not customer_id:
            return None
        try:
            order = request.env["storefront.erp.client"].get(f"/api/v1/orders/{order_id}")
        except StorefrontApiError:
            return None
        # ERP-authorized internal website editors may inspect any storefront
        # order. Portal users remain restricted to their own ERP customer ID.
        return order if internal_user or order.get("customer_id") == customer_id else None

    @staticmethod
    def _can_view_remote_orders():
        user = request.env.user
        return not user._is_public() and (
            user._is_internal() or bool(user.sudo().x_storefront_remote_customer_id)
        )

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
                params={"page_size": 100},
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
                f"/api/v1/orders/{order_id}/refund-requests"
            ) or []
        except StorefrontApiError:
            refunds = []
        is_english = self._is_english()
        return request.render("storefront_api_bridge.remote_purchase_detail_page", {
            "order": order,
            "refunds": refunds,
            "is_english": is_english,
            "status": self._status_label(order, is_english),
            "payment_status": self._payment_label(order, is_english),
            "refund_label": self._refund_label,
            "currency_symbol": self._currency_symbol,
            "additional_title": "Purchase Details" if is_english else "购买详情",
        })

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
                f"/api/v1/orders/{order_id}/refund-requests"
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
