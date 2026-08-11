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
                    request.session["sale_last_order_id"] = order.id
                    request.website.sale_reset()
                    # Start a new request so its lazy cart lookup cannot retain
                    # the now-cancelled local presentation order.
                    return request.redirect("/shop/cart")
            except StorefrontApiError:
                # Cart display remains available, but completion is never
                # inferred locally when ERP cannot confirm it.
                pass
        return super().cart(
            id=id, access_token=access_token, revive_method=revive_method, **post
        )

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
        "transfer": {"zh_CN": "银行转账", "en_US": "Bank transfer", "icon": "fa-bank"},
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
                if (
                    order.x_storefront_remote_payment_id
                    and order.sudo()._storefront_refresh_payment_completion()
                ):
                    request.session["sale_last_order_id"] = order.id
                    request.website.sale_reset()
                    return request.redirect("/shop/cart")
                checked = order.sudo()._storefront_check_inventory()
                if any(not item.get("available") for item in checked):
                    request.session["x_stock_quantity_warning"] = True
                    return request.redirect("/shop/cart")
                order.sudo()._storefront_sync_remote_order()
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
        try:
            result = order.sudo()._storefront_create_payment(provider_code)
        except StorefrontApiError as exc:
            return request.render("storefront_api_bridge.payment_error", {"message": str(exc)})
        payment = result.get("payment") or {}
        if payment.get("provider") in {"wechatpay", "alipay"}:
            return request.render("storefront_api_bridge.payment_status", {
                "order": order,
                "payment": payment,
            })
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
        order = request.cart
        payment = None
        if order and order.x_storefront_remote_payment_id:
            try:
                payment = request.env["storefront.erp.client"].get(
                    f"/api/v1/payments/{order.x_storefront_remote_payment_id}"
                )
                if payment.get("state") == "done":
                    order.sudo()._storefront_finalize_completed_attempt(payment)
                    request.session["sale_last_order_id"] = order.id
                    request.website.sale_reset()
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
        return request.render("storefront_api_bridge.payment_status", {
            "order": order,
            "payment": payment,
        })

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
