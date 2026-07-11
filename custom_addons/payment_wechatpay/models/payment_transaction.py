import base64
import json

from odoo import _, fields, models

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_wechatpay import const


_logger = get_payment_logger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    wechatpay_code_url = fields.Char(string="微信支付二维码链接", readonly=True)
    wechatpay_out_trade_no = fields.Char(string="微信支付商户订单号", readonly=True, copy=False)

    def _get_specific_rendering_values(self, processing_values):
        if self.provider_code != "wechatpay":
            return super()._get_specific_rendering_values(processing_values)

        self.ensure_one()
        self._wechatpay_ensure_native_order()
        return {
            "api_url": const.PROCESS_ROUTE,
            "reference": self.reference,
        }

    def _wechatpay_ensure_native_order(self):
        self.ensure_one()
        if self.wechatpay_code_url:
            return

        provider = self.provider_id.sudo()
        if self.currency_id.name != "CNY":
            self._set_error(_("微信支付仅支持人民币 CNY。"))
            return

        out_trade_no = self.wechatpay_out_trade_no or f"ODOO{self.id}"
        if provider.wechatpay_simulation_mode:
            self.write({
                "wechatpay_code_url": f"weixin://wxpay/simulated/{out_trade_no}",
                "wechatpay_out_trade_no": out_trade_no,
            })
            self._set_pending()
            return

        base_url = self.provider_id.get_base_url().rstrip("/")
        payload = {
            "appid": provider.wechatpay_appid,
            "mchid": provider.wechatpay_mchid,
            "description": (self.reference or "Odoo Order")[:127],
            "out_trade_no": out_trade_no,
            "notify_url": f"{base_url}{const.NOTIFY_ROUTE}",
            "amount": {
                "total": int(round(self.amount * 100)),
                "currency": "CNY",
            },
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        response = provider._send_api_request(
            "POST",
            const.NATIVE_TRANSACTION_ENDPOINT,
            data=body,
            reference=self.reference,
        )
        code_url = response.get("code_url")
        if not code_url:
            self._set_error(_("微信支付没有返回二维码链接。"))
            return
        self.write({
            "wechatpay_code_url": code_url,
            "wechatpay_out_trade_no": out_trade_no,
        })

    def _get_wechatpay_qr_data_uri(self):
        self.ensure_one()
        if not self.wechatpay_code_url:
            return None
        barcode = self.env["ir.actions.report"].barcode(
            barcode_type="QR",
            value=self.wechatpay_code_url,
            width=256,
            height=256,
            quiet=False,
        )
        return f"data:image/png;base64,{base64.b64encode(barcode).decode()}"

    def _extract_amount_data(self, payment_data):
        if self.provider_code != "wechatpay":
            return super()._extract_amount_data(payment_data)
        return None

    def _apply_updates(self, payment_data):
        if self.provider_code != "wechatpay":
            return super()._apply_updates(payment_data)

        self.provider_reference = payment_data.get("transaction_id") or payment_data.get("out_trade_no")
        trade_state = payment_data.get("trade_state")
        if trade_state == "SUCCESS":
            _logger.info("微信支付交易 %s 已支付。", self.reference)
            self._set_done()
        elif trade_state in ("CLOSED", "REVOKED", "PAYERROR"):
            self._set_canceled()
        else:
            self._set_pending()
