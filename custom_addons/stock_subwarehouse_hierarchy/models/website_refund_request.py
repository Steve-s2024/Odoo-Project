from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class WebsiteRefundRequest(models.Model):
    _name = "stock.subwarehouse.website.refund.request"
    _description = "Website Refund Request"
    _order = "create_date desc, id desc"

    name = fields.Char(related="order_id.name", store=True, readonly=True)
    order_id = fields.Many2one("sale.order", required=True, ondelete="cascade")
    partner_id = fields.Many2one(related="order_id.partner_id", store=True)
    source_transaction_id = fields.Many2one(
        "payment.transaction", string="原支付交易", required=True, ondelete="restrict"
    )
    refund_transaction_id = fields.Many2one(
        "payment.transaction", string="微信退款交易", readonly=True, ondelete="restrict"
    )
    currency_id = fields.Many2one(related="source_transaction_id.currency_id")
    line_ids = fields.One2many(
        "stock.subwarehouse.website.refund.request.line", "request_id", string="退款商品"
    )
    amount_total = fields.Monetary(compute="_compute_amount_total", store=True)
    state = fields.Selection(
        [
            ("requested", "待审核"),
            ("processing", "退款处理中"),
            ("refunded", "已退款"),
            ("failed", "退款失败"),
            ("rejected", "已拒绝"),
        ],
        compute="_compute_state",
        store=True,
    )
    review_state = fields.Selection(
        [("requested", "待审核"), ("rejected", "已拒绝")],
        default="requested",
        required=True,
    )

    @api.depends("line_ids.amount")
    def _compute_amount_total(self):
        for refund_request in self:
            refund_request.amount_total = sum(refund_request.line_ids.mapped("amount"))

    @api.depends("review_state", "refund_transaction_id.state")
    def _compute_state(self):
        for refund_request in self:
            transaction = refund_request.refund_transaction_id
            if refund_request.review_state == "rejected":
                refund_request.state = "rejected"
            elif not transaction:
                refund_request.state = "requested"
            elif transaction.state == "done":
                refund_request.state = "refunded"
            elif transaction.state == "error":
                refund_request.state = "failed"
            else:
                refund_request.state = "processing"

    def action_submit_wechat_refund(self):
        for refund_request in self:
            if refund_request.state != "requested":
                raise ValidationError(_("该退款申请已处理，不能重复提交。"))
            if refund_request.source_transaction_id.provider_code != "wechatpay":
                raise ValidationError(_("仅支持从微信支付交易发起退款。"))
            if refund_request.amount_total <= 0:
                raise ValidationError(_("退款金额必须大于零。"))
            available = refund_request.source_transaction_id.amount - sum(
                -transaction.amount
                for transaction in refund_request.source_transaction_id.child_transaction_ids
                if transaction.operation == "refund"
                and transaction.state in ("draft", "pending", "authorized", "done")
            )
            if refund_request.amount_total > available:
                raise ValidationError(_("退款金额超过当前可退款金额。"))
            transaction = refund_request.source_transaction_id._refund(refund_request.amount_total)
            refund_request.refund_transaction_id = transaction
        return True

    def action_reject(self):
        self.filtered(lambda refund_request: refund_request.state == "requested").write({
            "review_state": "rejected",
        })


class WebsiteRefundRequestLine(models.Model):
    _name = "stock.subwarehouse.website.refund.request.line"
    _description = "Website Refund Request Line"

    request_id = fields.Many2one(
        "stock.subwarehouse.website.refund.request", required=True, ondelete="cascade"
    )
    sale_line_id = fields.Many2one("sale.order.line", required=True, ondelete="restrict")
    product_id = fields.Many2one(related="sale_line_id.product_id", store=True)
    quantity = fields.Float(string="退款数量", required=True)
    currency_id = fields.Many2one(related="request_id.currency_id")
    amount = fields.Monetary(compute="_compute_amount", store=True)

    @api.depends("sale_line_id.price_total", "sale_line_id.product_uom_qty", "quantity")
    def _compute_amount(self):
        for line in self:
            ordered_qty = line.sale_line_id.product_uom_qty
            line.amount = (
                line.sale_line_id.price_total * line.quantity / ordered_qty
                if ordered_qty else 0.0
            )

    @api.constrains("quantity", "sale_line_id")
    def _check_quantity(self):
        for line in self:
            if not 0 < line.quantity <= line.sale_line_id.product_uom_qty:
                raise ValidationError(_("退款数量必须大于零且不超过订单数量。"))
