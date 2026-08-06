from odoo import _, models
from odoo.exceptions import UserError


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _set_done(self, *, state_message=None, extra_allowed_states=()):
        done_transactions = super()._set_done(
            state_message=state_message,
            extra_allowed_states=extra_allowed_states,
        )
        if done_transactions:
            self.env["stock.subwarehouse.website.refund.request"].sudo().search([
                ("refund_transaction_id", "in", done_transactions.ids),
                ("credit_note_id", "=", False),
            ])._ensure_credit_note()
        return done_transactions

    def _apply_updates(self, payment_data):
        if self.provider_code == "wechatpay" and payment_data.get("trade_state") == "SUCCESS":
            quotations = self.sale_order_ids.filtered(lambda order: order.state in ("draft", "sent"))
            if quotations:
                try:
                    quotations._prepare_website_stock_for_payment()
                except UserError as error:
                    self._set_error(_(
                        "支付前库存复核失败，交易未完成：%(reason)s",
                        reason=str(error),
                    ))
                    return
        return super()._apply_updates(payment_data)
