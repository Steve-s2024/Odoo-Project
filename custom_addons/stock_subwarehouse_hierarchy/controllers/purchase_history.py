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
        refunds = order.transaction_ids.mapped("child_transaction_ids").filtered(
            lambda transaction: transaction.operation == "refund"
        )
        if refunds.filtered(lambda transaction: transaction.state == "done"):
            refunded = sum(-transaction.amount for transaction in refunds.filtered(
                lambda transaction: transaction.state == "done"
            ))
            if refunded >= order.amount_total:
                return "Refunded" if is_english else "已退款"
            return "Partially refunded" if is_english else "部分退款"
        if refunds.filtered(lambda transaction: transaction.state in ("draft", "pending", "authorized")):
            return "Refund processing" if is_english else "退款处理中"
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

    def _get_order_refund_requests(self, order):
        return request.env["stock.subwarehouse.website.refund.request"].sudo().search([
            ("order_id", "=", order.id),
        ])

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

    @route(["/purchase-detail", "/purchase-details"], type="http", auth="public", website=True, sitemap=False)
    def purchase_detail_without_id(self, **kwargs):
        return request.redirect("/")

    @route(
        ["/purchase-detail/<int:order_id>", "/purchase-details/<int:order_id>"],
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
            "refund_requests": self._get_order_refund_requests(order),
            "refund_transactions": order.transaction_ids.mapped("child_transaction_ids").filtered(
                lambda transaction: transaction.operation == "refund"
            ),
            "is_english": is_english,
            "purchase_status": self._purchase_status(order, is_english),
            "additional_title": "Purchase Details" if is_english else "购买详情",
        })

    @route("/refund-item", type="http", auth="public", website=True, sitemap=False)
    def refund_item_without_id(self, **kwargs):
        return request.redirect("/")

    @route(
        "/refund-item/<int:order_id>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
    )
    def refund_item(self, order_id, **kwargs):
        redirect = self._redirect_home_if_public()
        if redirect:
            return redirect
        order = self._get_customer_order(order_id)
        if not order:
            return request.redirect("/")
        is_english = request.lang and request.lang.code == "en_US"
        return request.render("stock_subwarehouse_hierarchy.refund_item_page", {
            "order": order,
            "is_english": is_english,
            "additional_title": "Refund Items" if is_english else "申请退款",
        })

    @route(
        "/refund-item/<int:order_id>/submit",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
        sitemap=False,
    )
    def submit_refund_item(self, order_id, **post):
        redirect = self._redirect_home_if_public()
        if redirect:
            return redirect
        order = self._get_customer_order(order_id)
        if not order:
            return request.redirect("/")
        transaction = order.transaction_ids.filtered(
            lambda tx: tx.state == "done" and tx.provider_code == "wechatpay"
        ).sorted("id")[-1:]
        if not transaction:
            return request.redirect(f"/purchase-detail/{order.id}?refund_error=payment")

        request_lines = []
        for sale_line in order.order_line.filtered(lambda line: not line.display_type):
            raw_quantity = post.get(f"quantity_{sale_line.id}")
            if not raw_quantity:
                continue
            try:
                quantity = float(raw_quantity)
            except (TypeError, ValueError):
                continue
            if quantity > 0:
                request_lines.append((0, 0, {
                    "sale_line_id": sale_line.id,
                    "quantity": min(quantity, sale_line.product_uom_qty),
                }))
        if not request_lines:
            return request.redirect(f"/refund-item/{order.id}?error=items")

        request.env["stock.subwarehouse.website.refund.request"].sudo().create({
            "order_id": order.id,
            "source_transaction_id": transaction.id,
            "line_ids": request_lines,
        })
        return request.redirect(f"/purchase-detail/{order.id}?refund_requested=1")
