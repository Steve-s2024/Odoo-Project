from datetime import datetime
from urllib.parse import urljoin

from odoo import fields
from odoo.addons.stock_subwarehouse_hierarchy.controllers.website_sale import (
    WebsiteCartStockSource,
    WebsiteSaleStockSource,
)
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.http import request, route

from ..models.api_client import StorefrontApiError


class StorefrontCart(WebsiteCartStockSource):
    @route()
    def cart(self, id=None, access_token=None, revive_method="", **post):
        order = request.cart
        if order and order.x_storefront_remote_payment_id:
            try:
                if order.sudo()._storefront_refresh_payment_completion():
                    payment = {
                        "id": order.x_storefront_remote_payment_id,
                        "provider": order.x_storefront_payment_provider,
                        "state": "done",
                        "authoritative": True,
                    }
                    StorefrontWebsiteSale._store_completed_payment_session(
                        order, payment,
                    )
                    # Start a new request so its lazy cart lookup cannot retain
                    # the now-cancelled local presentation order.
                    return request.redirect("/shop/payment/status")
            except StorefrontApiError:
                # Cart display remains available, but completion is never
                # inferred locally when ERP cannot confirm it.
                pass
        return super().cart(
            id=id, access_token=access_token, revive_method=revive_method, **post
        )

    def _prepare_order_history(self):
        """Feed Odoo's quick-reorder drawer from completed ERP checkouts.

        The separated Shop deliberately cancels its local presentation order
        after ERP confirms payment, so Odoo's native ``state = sale`` lookup is
        always empty.  The retained local lines are a suitable presentation
        cache for quick reorder; stock and price are still revalidated by ERP
        later at the mandatory payment gate.
        """
        if request.env.user._is_public():
            return {"order_history": []}

        def is_same_combo(first, second):
            return first.linked_line_ids.product_id.ids == second.linked_line_ids.product_id.ids

        partner = request.env.user.partner_id.commercial_partner_id
        previous_orders = request.env["sale.order"].sudo().search([
            ("partner_id.commercial_partner_id", "=", partner.id),
            ("website_id", "=", request.website.id),
            ("x_storefront_completed_at", "!=", False),
            ("x_storefront_remote_state", "=", "done"),
        ], order="x_storefront_completed_at desc, id desc", limit=10)

        SaleOrderLine = request.env["sale.order.line"].sudo()
        cart_lines = request.cart.order_line if request.cart else SaleOrderLine
        seen_lines = SaleOrderLine
        lines_per_date = {}
        for line in previous_orders.order_line:
            product_id = line.product_id.id
            if (
                line.is_delivery
                or line.linked_line_id.product_type == "combo"
                or not line._is_sellable()
                or (
                    request.website.prevent_zero_price_sale
                    and line.product_id._get_combination_info_variant()["price"] == 0
                )
            ):
                continue

            is_combo = line.product_type == "combo"
            if any(
                existing.product_id.id == product_id
                and (not is_combo or is_same_combo(line, existing))
                for existing in cart_lines + seen_lines
            ):
                continue
            seen_lines |= line

            completed_at = line.order_id.x_storefront_completed_at or line.order_id.date_order
            days_ago = (fields.Date.today() - completed_at.date()).days
            if days_ago == 0:
                label = request.env._("Today")
            elif days_ago == 1:
                label = request.env._("Yesterday")
            else:
                label = request.env._("%s days ago", days_ago)
            lines_per_date.setdefault(label, SaleOrderLine)
            lines_per_date[label] |= line

        return {
            "order_history": [
                {"label": label, "lines": lines}
                for label, lines in lines_per_date.items()
            ],
        }

    @route()
    def update_cart(self, line_id, quantity, product_id=None, **kwargs):
        order = request.cart
        line = request.env["sale.order.line"].sudo().browse(int(line_id)).exists()
        product = line.product_id if line and line.order_id == order else False
        result = super().update_cart(line_id, quantity, product_id=product_id, **kwargs)
        if order and product:
            order.sudo()._storefront_clear_shortage_for_product(product)
            result["website_sale.cart_lines"] = request.env["ir.ui.view"]._render_template(
                "website_sale.cart_lines", {
                    "website_sale_order": request.cart,
                    "date": fields.Date.today(),
                    "suggested_products": request.cart._cart_accessories(),
                },
            )
        return result

    @route()
    def clear_cart(self):
        order = request.cart
        if order and order.x_storefront_reservation_id:
            try:
                order.sudo()._storefront_release_reservation()
            except StorefrontApiError:
                pass
        return super().clear_cart()


class StorefrontWebsiteSale(WebsiteSaleStockSource):
    _PAYMENT_PRESENTATION = {
        "wechatpay": {"zh_CN": "微信支付", "en_US": "WeChat Pay", "icon": "fa-weixin"},
        "alipay": {"zh_CN": "支付宝", "en_US": "Alipay", "icon": "fa-credit-card"},
        "bank_card": {"zh_CN": "银行卡支付（即将开放）", "en_US": "Bank card (coming soon)", "icon": "fa-credit-card"},
    }

    @classmethod
    def _localized_payment_methods(cls, methods):
        language = request.lang.code if request.lang.code in ("zh_CN", "en_US") else "zh_CN"
        localized = []
        for method in methods:
            values = dict(method)
            presentation = cls._PAYMENT_PRESENTATION.get(method.get("code"), {})
            values["display_name"] = presentation.get(language) or method.get("name")
            values["icon_class"] = presentation.get("icon", "fa-credit-card")
            localized.append(values)
        return localized

    @staticmethod
    def _store_completed_payment_session(order, payment):
        remote_order_id = order.x_storefront_remote_order_id
        payment_id = payment.get("id")
        request.session["x_storefront_completed_order_id"] = remote_order_id
        request.session["x_storefront_completed_payment_id"] = payment_id
        request.session["x_storefront_completed_provider"] = payment.get("provider")
        request.session["x_storefront_completed_payment_state"] = "done"
        request.session["sale_last_order_id"] = order.id
        request.website.sale_reset()
        # `sale_reset()` removes Odoo's cart cache keys. Keep the intent
        # explicit so future website changes cannot revive the completed cart.
        request.session.pop("sale_order_id", None)
        request.session.pop("website_sale_cart_quantity", None)
        request.session.pop("x_storefront_pending_local_order_id", None)
        request.session.pop("x_storefront_pending_order_id", None)
        request.session.pop("x_storefront_pending_payment_id", None)
        return remote_order_id

    @staticmethod
    def _remember_pending_payment(order, payment):
        request.session["x_storefront_pending_local_order_id"] = order.id
        request.session["x_storefront_pending_order_id"] = order.x_storefront_remote_order_id
        request.session["x_storefront_pending_payment_id"] = payment.get("id")

    @staticmethod
    def _forget_pending_payment_session():
        """Forget only the expired payment pointers; keep the active cart."""
        request.session.pop("x_storefront_pending_local_order_id", None)
        request.session.pop("x_storefront_pending_order_id", None)
        request.session.pop("x_storefront_pending_payment_id", None)

    @staticmethod
    def _payment_order_from_session(payment_id=None):
        order_id = (
            request.session.get("sale_order_id")
            or request.session.get("x_storefront_pending_local_order_id")
        )
        if not order_id:
            return request.env["sale.order"]
        try:
            order_id = int(order_id)
        except (TypeError, ValueError):
            return request.env["sale.order"]
        order = request.env["sale.order"].sudo().browse(order_id).exists()
        if (
            not order
            or order.website_id != request.website
            or not order.x_storefront_remote_order_id
            or not order.x_storefront_remote_payment_id
            or (
                payment_id
                and order.x_storefront_remote_payment_id != str(payment_id)
            )
        ):
            return request.env["sale.order"]
        if not request.env.user._is_public() and not request.env.user._is_internal():
            if (
                order.partner_id.commercial_partner_id
                != request.env.user.partner_id.commercial_partner_id
            ):
                return request.env["sale.order"]
        return order

    @staticmethod
    def _completed_payment_context():
        if request.session.get("x_storefront_completed_payment_state") != "done":
            return {}
        return {
            "id": request.session.get("x_storefront_completed_payment_id"),
            "provider": request.session.get("x_storefront_completed_provider"),
            "state": "done",
            "authoritative": True,
        }

    @route()
    def shop_checkout(self, try_skip_step=None, **query_params):
        self._sync_website_checkout_language()
        order = request.cart
        if order and order.sudo()._get_source_inventory_shortage_lines():
            request.session["x_stock_quantity_warning"] = True
            return request.redirect("/shop/cart")
        # Quantity is intentionally not checked here. ERP performs the current
        # stock check and creates the 15-minute reservation at /shop/payment.
        return WebsiteSale.shop_checkout(self, try_skip_step=try_skip_step, **query_params)

    def _get_shop_payment_errors(self, order):
        return WebsiteSale._get_shop_payment_errors(self, order)

    @route()
    def shop_payment(self, **post):
        self._sync_website_checkout_language()
        order = request.cart
        remote_methods = []
        remote_error = None
        if order:
            try:
                if order.sudo()._get_source_inventory_shortage_lines():
                    request.session["x_stock_quantity_warning"] = True
                    return request.redirect("/shop/cart")
                reservation_expires_at = order.x_storefront_reservation_expires_at
                if (
                    order.x_storefront_remote_order_id
                    and isinstance(reservation_expires_at, datetime)
                    and reservation_expires_at <= fields.Datetime.now()
                ):
                    remote_order = request.env["storefront.erp.client"].get(
                        f"/api/v1/orders/{order.x_storefront_remote_order_id}"
                    ) or {}
                    if (
                        remote_order.get("payment_expired") is True
                        or remote_order.get("payment_state") == "expired"
                    ):
                        order.sudo()._storefront_retire_expired_attempt(remote_order)
                        self._forget_pending_payment_session()
                if (
                    order.x_storefront_remote_payment_id
                    and order.sudo()._storefront_refresh_payment_completion()
                ):
                    payment = request.env["storefront.erp.client"].get(
                        f"/api/v1/payments/{order.x_storefront_remote_payment_id}"
                    )
                    self._store_completed_payment_session(order, payment)
                    return request.redirect("/shop/payment/status")
                checked = order.sudo()._storefront_check_inventory()
                if any(not item.get("available") for item in checked):
                    request.session["x_stock_quantity_warning"] = True
                    return request.redirect("/shop/cart")
                previous_attempt_id = order.x_storefront_attempt_id
                order.sudo()._storefront_sync_remote_order()
                if order.x_storefront_attempt_id != previous_attempt_id:
                    self._forget_pending_payment_session()
                remote_methods = self._localized_payment_methods(
                    order.sudo()._storefront_payment_methods()
                )
            except StorefrontApiError as exc:
                # A reservation can lose a race after the initial check. Only a
                # second explicit ERP inventory rejection is allowed to create
                # red cart rows; timeouts and malformed responses do not.
                try:
                    checked = order.sudo()._storefront_check_inventory()
                except StorefrontApiError:
                    checked = []
                if any(not item.get("available") for item in checked):
                    request.session["x_stock_quantity_warning"] = True
                    return request.redirect("/shop/cart")
                remote_error = str(exc)
        response = WebsiteSale.shop_payment(self, **post)
        if getattr(response, "qcontext", None) is not None:
            response.qcontext["x_storefront_payment_methods"] = remote_methods
            response.qcontext["x_storefront_payment_error"] = remote_error
        return response

    @route(
        "/shop/erp-payment/start/<string:provider_code>",
        type="http", auth="public", methods=["POST"], website=True, sitemap=False,
    )
    def storefront_payment_start(self, provider_code, **post):
        order = request.cart
        if not order:
            return request.redirect("/shop/cart")
        if provider_code == "bank_card":
            return request.render("storefront_api_bridge.payment_error", {
                "message": "Bank card payment is coming soon."
                if request.lang.code == "en_US" else "银行卡支付即将开放。",
            })
        try:
            result = order.sudo()._storefront_create_payment(provider_code)
        except StorefrontApiError as exc:
            return request.render("storefront_api_bridge.payment_error", {"message": str(exc)})
        payment = result.get("payment") or {}
        self._remember_pending_payment(order, payment)
        if payment.get("provider") in {"wechatpay", "alipay"}:
            # Never render a reloadable POST response.  A refresh on that page
            # repeats payment initiation and, after completion, can create a
            # second checkout attempt.  The stable GET page only reads status.
            return request.redirect("/shop/payment/status")
        processing = result.get("processing") or {}
        action = processing.get("api_url") or processing.get("action_url") or processing.get("url")
        values = processing.get("form_values") or processing.get("data") or {
            key: value for key, value in processing.items()
            if key not in ("api_url", "action_url", "url")
        }
        if not action:
            return request.redirect("/shop/payment/status")
        if action.startswith("/"):
            base_url, _key, _timeout = request.env["storefront.erp.client"]._settings()
            action = urljoin(f"{base_url}/", action.lstrip("/"))
        return request.render("storefront_api_bridge.payment_handoff", {
            "action": action,
            "form_values": values,
        })

    @route("/shop/payment/status", type="http", auth="public", website=True, sitemap=False)
    def storefront_payment_status(self, **post):
        # A provider webhook can complete and retire the local presentation
        # order before the browser returns. Capture the signed-session cart id
        # before `request.cart` clears it, then recover only that exact order.
        session_cart_id = request.session.get("sale_order_id")
        order = request.cart
        if not order and session_cart_id:
            candidate = request.env["sale.order"].sudo().browse(
                int(session_cart_id)
            ).exists()
            if (
                candidate
                and candidate.website_id == request.website
                and candidate.x_storefront_remote_order_id
                and candidate.x_storefront_remote_payment_id
            ):
                order = candidate
        payment = self._completed_payment_context()
        completed_order_id = request.session.get("x_storefront_completed_order_id")
        remote_order = {}
        if order and order.x_storefront_remote_payment_id:
            try:
                remote_order = request.env["storefront.erp.client"].get(
                    f"/api/v1/orders/{order.x_storefront_remote_order_id}"
                ) or {}
                payment = request.env["storefront.erp.client"].get(
                    f"/api/v1/payments/{order.x_storefront_remote_payment_id}"
                )
                if payment.get("state") == "done":
                    order.sudo()._storefront_finalize_completed_attempt(payment)
                    completed_order_id = self._store_completed_payment_session(
                        order, payment,
                    )
                elif order.sudo()._storefront_payment_is_authoritative(
                    payment,
                    order.x_storefront_remote_order_id,
                    payment.get("provider"),
                    payment_id=order.x_storefront_remote_payment_id,
                ):
                    order.sudo().x_storefront_remote_state = payment.get("state")
                else:
                    payment = None
            except StorefrontApiError:
                payment = None
        receipt_available = False
        if payment and payment.get("state") == "done" and completed_order_id:
            try:
                documents = request.env["storefront.erp.client"].get(
                    f"/api/v1/orders/{completed_order_id}/documents"
                )
                receipt_available = bool(documents.get("receipt"))
            except StorefrontApiError:
                # Payment success remains visible. The bounded browser poll
                # retries until ERP finishes generating the receipt.
                receipt_available = False
        return request.render("storefront_api_bridge.payment_status", {
            "order": order,
            "remote_order": remote_order,
            "payment": payment,
            "completed_order_id": completed_order_id,
            "receipt_available": receipt_available,
        })

    @route(
        "/shop/payment/status/poll",
        type="http", auth="public", methods=["GET"], website=True, sitemap=False,
    )
    def storefront_payment_status_poll(self, payment_id=None, **post):
        """Return a bounded, session-owned ERP status without reloading the page."""
        order = self._payment_order_from_session(payment_id=payment_id)
        if not order:
            return request.make_json_response({"state": "unknown"}, status=404)
        try:
            payment = request.env["storefront.erp.client"].get(
                f"/api/v1/payments/{order.x_storefront_remote_payment_id}"
            ) or {}
            provider = payment.get("provider")
            if not provider or not order._storefront_payment_is_authoritative(
                payment,
                order.x_storefront_remote_order_id,
                provider,
                payment_id=order.x_storefront_remote_payment_id,
            ):
                raise StorefrontApiError(
                    "ERP returned an invalid payment status.",
                    code="invalid_payment_status",
                    status=502,
                )
            if payment.get("state") == "done":
                order._storefront_finalize_completed_attempt(payment)
                self._store_completed_payment_session(order, payment)
                return request.make_json_response({
                    "state": "done",
                    "redirect": "/shop/payment/status",
                })
            remote_order = request.env["storefront.erp.client"].get(
                f"/api/v1/orders/{order.x_storefront_remote_order_id}"
            ) or {}
            if (
                remote_order.get("authoritative") is not True
                or remote_order.get("id") != order.x_storefront_remote_order_id
            ):
                raise StorefrontApiError(
                    "ERP returned an invalid order status.",
                    code="invalid_order_status",
                    status=502,
                )
            if (
                remote_order.get("payment_expired") is True
                or remote_order.get("payment_state") == "expired"
            ):
                return request.make_json_response({
                    "state": "expired",
                    "redirect": f"/purchase-detail/{order.x_storefront_remote_order_id}",
                })
            order.x_storefront_remote_state = payment.get("state")
            return request.make_json_response({"state": payment.get("state")})
        except StorefrontApiError:
            # Fail closed: the browser keeps the order pending and can retry;
            # it never infers success from cached Shop data.
            return request.make_json_response({"state": "unavailable"}, status=503)

    @route(
        "/shop/payment/simulate-success",
        type="http", auth="public", methods=["POST"], website=True, sitemap=False,
    )
    def storefront_payment_simulate_success(self, **post):
        order = request.cart
        if not order:
            return request.redirect("/shop/cart")
        try:
            order.sudo()._storefront_simulate_payment_success()
        except StorefrontApiError as exc:
            return request.render("storefront_api_bridge.payment_error", {"message": str(exc)})
        return request.redirect("/shop/payment/status")
