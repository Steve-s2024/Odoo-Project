from odoo import _, models
from odoo.exceptions import UserError, ValidationError


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _supports_website_original_refund(self):
        """Return whether this completed payment supports an API refund.

        Odoo uses the string ``none`` for an unsupported provider, so a plain
        truth-value check is unsafe (``bool("none")`` is true).  Keeping this
        provider-neutral also lets newly installed providers participate in
        the established refund workflow without another hard-coded whitelist.
        """
        self.ensure_one()
        return (
            self.state == "done"
            and self.operation != "refund"
            and self.provider_id.support_refund in ("full_only", "partial")
        )

    def _website_refund_available_amount(self):
        self.ensure_one()
        refunded = sum(
            -transaction.amount
            for transaction in self.child_transaction_ids
            if transaction.operation == "refund"
            and transaction.state in ("draft", "pending", "authorized", "done")
        )
        return max(self.amount - refunded, 0.0)

    def _validate_website_original_refund(self, amount):
        self.ensure_one()
        if not self._supports_website_original_refund():
            raise ValidationError(_("该支付方式不支持原路退款。"))
        available = self._website_refund_available_amount()
        if amount <= 0 or self.currency_id.compare_amounts(amount, available) > 0:
            raise ValidationError(_("退款金额必须大于零且不能超过当前可退款金额。"))
        if (
            self.provider_id.support_refund == "full_only"
            and self.currency_id.compare_amounts(amount, available)
        ):
            raise ValidationError(_("该支付方式只支持全额退款。"))
        return available

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
