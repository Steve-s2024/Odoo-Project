import base64
import secrets

from odoo import _, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_alipay import const


_logger = get_payment_logger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    alipay_qr_code = fields.Char(string="Alipay QR code URL", readonly=True)
    alipay_out_trade_no = fields.Char(string="Alipay merchant order number", readonly=True, copy=False)
    alipay_out_refund_no = fields.Char(string="Alipay merchant refund number", readonly=True, copy=False)
    alipay_simulation_token = fields.Char(readonly=True, copy=False, groups="base.group_system")

    def _get_specific_rendering_values(self, processing_values):
        if self.provider_code != "alipay":
            return super()._get_specific_rendering_values(processing_values)
        self.ensure_one()
        self._alipay_ensure_trade()
        return {"api_url": const.PROCESS_ROUTE, "reference": self.reference}

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        if provider_code != "alipay":
            return super()._get_tx_from_notification_data(provider_code, notification_data)
        reference = notification_data.get("reference")
        out_trade_no = notification_data.get("out_trade_no")
        if not reference and not out_trade_no:
            raise ValidationError(_("The Alipay notification has no transaction reference."))
        tx = self.search([
            ("provider_code", "=", "alipay"),
            "|", ("reference", "=", reference), ("alipay_out_trade_no", "=", out_trade_no),
        ], limit=1)
        if not tx:
            raise ValidationError(_("No Alipay transaction matches the notification."))
        return tx

    def _alipay_ensure_trade(self):
        self.ensure_one()
        if self.alipay_qr_code:
            return
        if self.currency_id.name != "CNY":
            self._set_error(_("Alipay only supports CNY in this store."))
            return

        out_trade_no = self.alipay_out_trade_no or f"ODOO{self.id}"
        provider = self.provider_id.sudo()
        if provider.alipay_simulation_mode:
            self.write({
                "alipay_qr_code": f"alipay://simulated/{out_trade_no}",
                "alipay_out_trade_no": out_trade_no,
                "alipay_simulation_token": secrets.token_urlsafe(32),
            })
            self._set_pending()
            return

        response = provider._alipay_api_request(
            "alipay.trade.precreate",
            {
                "out_trade_no": out_trade_no,
                "total_amount": f"{self.amount:.2f}",
                "subject": (self.reference or "Odoo order")[:256],
                "product_code": "FACE_TO_FACE_PAYMENT",
                "timeout_express": "30m",
            },
            notify_url=f"{provider.get_base_url().rstrip('/')}{const.NOTIFY_ROUTE}",
            reference=self.reference,
        )
        if response.get("code") != "10000" or not response.get("qr_code"):
            self._set_error(response.get("sub_msg") or response.get("msg") or _("Alipay did not return a QR code."))
            return
        self.write({
            "alipay_qr_code": response["qr_code"],
            "alipay_out_trade_no": out_trade_no,
        })
        self._set_pending()

    def _get_alipay_qr_data_uri(self):
        self.ensure_one()
        if not self.alipay_qr_code or self.provider_id.sudo().alipay_simulation_mode:
            return None
        try:
            barcode = self.env["ir.actions.report"].barcode(
                barcode_type="QR", value=self.alipay_qr_code,
                width=256, height=256, quiet=False,
            )
        except Exception:
            _logger.exception("Unable to render Alipay QR code for %s.", self.reference)
            return None
        return f"data:image/png;base64,{base64.b64encode(barcode).decode()}"

    def _extract_amount_data(self, payment_data):
        if self.provider_code != "alipay":
            return super()._extract_amount_data(payment_data)
        total_amount = (
            payment_data.get("refund_fee")
            if self.operation == "refund"
            else payment_data.get("total_amount")
        )
        if total_amount in (None, ""):
            return None
        return {
            "amount": float(total_amount),
            "currency_code": "CNY",
            "precision_digits": 2,
        }

    def _apply_updates(self, payment_data):
        if self.provider_code != "alipay":
            return super()._apply_updates(payment_data)
        if self.operation == "refund":
            self.provider_reference = (
                payment_data.get("trade_no")
                or payment_data.get("out_request_no")
                or self.alipay_out_refund_no
            )
            if payment_data.get("code") == "10000":
                self._set_done()
            else:
                self._set_error(
                    payment_data.get("sub_msg")
                    or payment_data.get("msg")
                    or _("Alipay refund failed.")
                )
            return

        self.provider_reference = payment_data.get("trade_no") or payment_data.get("out_trade_no")
        trade_status = payment_data.get("trade_status")
        if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            self._set_done()
        elif trade_status == "TRADE_CLOSED":
            self._set_canceled()
        else:
            self._set_pending()

    def _send_refund_request(self):
        if self.provider_code != "alipay":
            return super()._send_refund_request()

        self.ensure_one()
        source_tx = self.source_transaction_id
        if not source_tx or not (source_tx.provider_reference or source_tx.alipay_out_trade_no):
            raise ValidationError(_("The original Alipay transaction cannot be found."))
        out_refund_no = self.alipay_out_refund_no or f"ODOOREF{self.id}"
        self.alipay_out_refund_no = out_refund_no
        provider = self.provider_id.sudo()
        if provider.alipay_simulation_mode:
            self._process("alipay", {
                "code": "10000",
                "trade_no": f"SIM-{out_refund_no}",
                "out_request_no": out_refund_no,
                "refund_fee": f"{-self.amount:.2f}",
            })
            self._post_process()
            return

        values = {
            "refund_amount": f"{-self.amount:.2f}",
            "refund_reason": f"Odoo refund {source_tx.reference}"[:256],
            "out_request_no": out_refund_no,
        }
        if source_tx.provider_reference:
            values["trade_no"] = source_tx.provider_reference
        else:
            values["out_trade_no"] = source_tx.alipay_out_trade_no
        response = provider._alipay_api_request("alipay.trade.refund", values)
        self._process("alipay", {**response, "out_request_no": out_refund_no})
        if self.state == "done":
            self._post_process()
