import json
import logging

from odoo.http import Controller, request, route

from odoo.addons.payment_lianlian import const


_logger = logging.getLogger(__name__)


class LianLianController(Controller):

    @staticmethod
    def _notice_headers():
        headers = request.httprequest.headers
        return {
            "signature": headers.get("signature") or headers.get("Signature") or "",
            "sign-type": headers.get("sign-type") or headers.get("Sign-Type") or "",
            "timezone": headers.get("timezone") or headers.get("Timezone") or "",
            "timestamp": headers.get("timestamp") or headers.get("Timestamp") or "",
        }

    @route(const.NOTIFY_ROUTE, type="http", auth="public", methods=["POST"], csrf=False)
    def lianlian_notify(self, **_post):
        raw_body = request.httprequest.get_data(cache=True, as_text=True)
        try:
            untrusted = json.loads(raw_body)
            merchant_transaction_id = untrusted.get("merchant_transaction_id")
            if not merchant_transaction_id:
                raise ValueError("Missing merchant transaction ID.")
            tx = request.env["payment.transaction"].sudo().search([
                ("provider_code", "=", "lianlian"),
                ("lianlian_merchant_transaction_id", "=", merchant_transaction_id),
            ], limit=1)
            if not tx:
                raise ValueError("Unknown merchant transaction ID.")
            verified = tx.provider_id.sudo()._lianlian_verify_notice(
                raw_body, self._notice_headers(),
            )
            if verified.get("merchant_transaction_id") != merchant_transaction_id:
                raise ValueError("Verified merchant transaction ID mismatch.")
            if tx.state != "done":
                tx._process("lianlian", verified)
        except Exception:
            _logger.exception("LianLian notification processing failed.")
            return request.make_json_response(
                {"code": 500, "message": "failed"}, status=400,
            )
        return request.make_json_response({"code": 200, "message": "success"})

    @route(const.RETURN_ROUTE, type="http", auth="public", methods=["GET"], csrf=False)
    def lianlian_return(self, **_query):
        return request.redirect("/payment/status")

    @route(const.CANCEL_ROUTE, type="http", auth="public", methods=["GET"], csrf=False)
    def lianlian_cancel(self, **_query):
        return request.redirect("/payment/status")
