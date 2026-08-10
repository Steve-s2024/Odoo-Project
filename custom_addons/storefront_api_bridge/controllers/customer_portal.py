import hashlib
import json

from odoo.http import request, route
from odoo.addons.stock_subwarehouse_hierarchy.controllers.purchase_history import (
    WebsitePurchaseHistory,
)

from ..models.api_client import StorefrontApiError


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

    def _login_redirect(self):
        target = request.httprequest.full_path.rstrip("?")
        return request.redirect(f"/web/login?redirect={target}")

    def _remote_order(self, order_id):
        customer_id = self._remote_customer_id()
        if not customer_id:
            return None
        try:
            order = request.env["storefront.erp.client"].get(f"/api/v1/orders/{order_id}")
        except StorefrontApiError:
            return None
        return order if order.get("customer_id") == customer_id else None

    def _render_purchase_history_page(self, orders, error=False):
        is_english = self._is_english()
        return request.render("storefront_api_bridge.remote_purchase_history_page", {
            "orders": orders,
            "is_english": is_english,
            "portal_error": error,
            "status_label": self._status_label,
            "additional_title": "Purchase History" if is_english else "购买记录",
        })

    @route()
    def purchase_history(self, **kwargs):
        customer_id = self._remote_customer_id()
        if not customer_id:
            # Internal accounts are ERP-authorized website editors, not shop
            # customers. Render an empty editable page instead of redirecting
            # them back and forth between /purchase-history and /web/login.
            if request.env.user._is_internal():
                return self._render_purchase_history_page([])
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
        if not self._remote_customer_id():
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
        if not self._remote_customer_id():
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
        return request.render("storefront_api_bridge.remote_refund_item_page", {
            "order": order,
            "refunds": refunds,
            "is_english": is_english,
            "refund_label": self._refund_label,
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
        if not self._remote_customer_id():
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
        if not items:
            return request.redirect(f"/refund-item/{order_id}?error=items")
        fingerprint = hashlib.sha256(
            json.dumps(items, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        try:
            request.env["storefront.erp.client"].post(
                f"/api/v1/orders/{order_id}/refund-requests",
                {"items": items},
                idempotency_key=f"portal-refund-{order_id}-{fingerprint}",
            )
        except StorefrontApiError as exc:
            request.session["x_storefront_refund_error"] = str(exc)
            return request.redirect(f"/refund-item/{order_id}?error=api")
        return request.redirect(f"/purchase-detail/{order_id}?refund_requested=1")
