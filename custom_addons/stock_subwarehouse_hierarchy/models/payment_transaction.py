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
            done_transactions.sale_order_ids.sudo()._queue_paid_website_delivery()
        return done_transactions

    def _apply_updates(self, payment_data):
        payment_is_confirmed = (
            self.provider_code == "wechatpay" and payment_data.get("trade_state") == "SUCCESS"
        ) or (
            self.provider_code == "alipay"
            and payment_data.get("trade_status") in ("TRADE_SUCCESS", "TRADE_FINISHED")
        )
        if payment_is_confirmed:
            quotations = self.sale_order_ids.filtered(lambda order: order.state in ("draft", "sent"))
            if quotations:
                expired_orders = quotations.filtered(
                    lambda order: order._website_payment_deadline_is_expired()
                )
                if expired_orders:
                    self._set_error(_(
                        "支付确认已超过15分钟库存预留期限，交易不能完成。"
                    ))
                    expired_orders._expire_website_payment()
                    return
                try:
                    quotations._prepare_website_stock_for_payment()
                except UserError as error:
                    self._set_error(_(
                        "支付前库存复核失败，交易未完成：%(reason)s",
                        reason=str(error),
                    ))
                    return
        return super()._apply_updates(payment_data)
