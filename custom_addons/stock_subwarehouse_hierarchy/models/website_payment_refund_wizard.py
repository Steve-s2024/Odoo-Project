from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class WebsitePaymentRefundWizard(models.TransientModel):
    _name = "stock.subwarehouse.website.payment.refund.wizard"
    _description = "Website WeChat Payment Refund"

    order_id = fields.Many2one("sale.order", string="订单", readonly=True)
    transaction_id = fields.Many2one(
        "payment.transaction", string="原支付交易", readonly=True, required=True
    )
    currency_id = fields.Many2one(related="transaction_id.currency_id")
    amount_available = fields.Monetary(
        string="可退款金额", compute="_compute_amount_available", readonly=True
    )
    amount_to_refund = fields.Monetary(string="退款金额", required=True)

    @api.depends("transaction_id", "transaction_id.child_transaction_ids.state")
    def _compute_amount_available(self):
        for wizard in self:
            transaction = wizard.transaction_id
            refunded = sum(
                -refund.amount
                for refund in transaction.child_transaction_ids
                if refund.operation == "refund"
                and refund.state in ("pending", "authorized", "done")
            )
            wizard.amount_available = max(transaction.amount - refunded, 0.0)

    @api.onchange("transaction_id")
    def _onchange_transaction_id(self):
        self.amount_to_refund = self.amount_available

    def action_submit_refund(self):
        self.ensure_one()
        if self.transaction_id.provider_code != "wechatpay":
            raise ValidationError(_("仅支持微信支付交易退款。"))
        if not 0 < self.amount_to_refund <= self.amount_available:
            raise ValidationError(_("退款金额必须大于零且不超过可退款金额。"))
        refund_transaction = self.transaction_id._refund(self.amount_to_refund)
        message = (
            _("模拟退款已完成。")
            if refund_transaction.state == "done"
            else _("退款申请已提交，正在等待微信支付确认。")
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "message": message, "sticky": False},
        }
