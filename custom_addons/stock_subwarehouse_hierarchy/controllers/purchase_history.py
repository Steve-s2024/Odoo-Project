from odoo.http import request, route
from odoo.addons.portal.controllers.portal import CustomerPortal


class WebsitePurchaseHistory(CustomerPortal):
    """Customer-facing website order history, separate from the generic portal."""

    def _redirect_home_if_public(self):
        if request.env.user._is_public():
            return request.redirect("/")
        return None

    def _purchase_domain(self):
        partner = request.env.user.partner_id.commercial_partner_id
        return [
            ("website_id", "!=", False),
            ("partner_id", "child_of", partner.ids),
            "|",
            ("transaction_ids", "!=", False),
            ("state", "in", ("sent", "sale", "cancel")),
        ]

    @staticmethod
    def _purchase_status(order, is_english):
        if order.state == "cancel":
            return "Cancelled" if is_english else "已取消"
        if order.state in ("draft", "sent"):
            return "Awaiting payment" if is_english else "待支付"
        active_pickings = order.picking_ids.filtered(
            lambda picking: picking.state not in ("done", "cancel")
        )
        if active_pickings:
            return "Processing" if is_english else "处理中"
        return "Completed" if is_english else "已完成"

    def _get_customer_order(self, order_id):
        orders = request.env["sale.order"].sudo().search(
            [("id", "=", order_id), *self._purchase_domain()], limit=1
        )
        return orders

    @route("/purchase-history", type="http", auth="public", website=True, sitemap=False)
    def purchase_history(self, **kwargs):
        redirect = self._redirect_home_if_public()
        if redirect:
            return redirect

        is_english = request.lang and request.lang.code == "en_US"
        orders = request.env["sale.order"].sudo().search(
            self._purchase_domain(), order="date_order desc, id desc"
        )
        return request.render("stock_subwarehouse_hierarchy.purchase_history_page", {
            "orders": orders,
            "is_english": is_english,
            "purchase_status": self._purchase_status,
            "additional_title": "Purchase History" if is_english else "购买记录",
        })

    @route("/purchase-detail", type="http", auth="public", website=True, sitemap=False)
    def purchase_detail_without_id(self, **kwargs):
        return request.redirect("/")

    @route(
        "/purchase-detail/<int:order_id>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
    )
    def purchase_detail(self, order_id, **kwargs):
        redirect = self._redirect_home_if_public()
        if redirect:
            return redirect

        order = self._get_customer_order(order_id)
        if not order:
            return request.redirect("/")

        is_english = request.lang and request.lang.code == "en_US"
        return request.render("stock_subwarehouse_hierarchy.purchase_detail_page", {
            "order": order,
            "is_english": is_english,
            "purchase_status": self._purchase_status(order, is_english),
            "additional_title": "Purchase Details" if is_english else "购买详情",
        })
