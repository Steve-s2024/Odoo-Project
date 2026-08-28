from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class WebsitePaymentRefundWizard(models.TransientModel):
    _name = "stock.subwarehouse.website.payment.refund.wizard"
    _description = "Website Payment Refund"

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
            wizard.amount_available = (
                transaction._website_refund_available_amount() if transaction else 0.0
            )

    @api.onchange("transaction_id")
    def _onchange_transaction_id(self):
        self.amount_to_refund = self.amount_available

    def action_submit_refund(self):
        self.ensure_one()
        self.transaction_id._validate_website_original_refund(self.amount_to_refund)
        refund_transaction = self.transaction_id._refund(self.amount_to_refund)
        message = (
            _("模拟退款已完成。")
            if refund_transaction.state == "done"
            else _("退款申请已提交，正在等待支付平台确认。")
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "message": message, "sticky": False},
        }
