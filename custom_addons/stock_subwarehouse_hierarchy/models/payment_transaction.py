from odoo import _, models
from odoo.exceptions import UserError


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

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
