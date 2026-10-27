import pprint

from odoo.http import Controller, request, route

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_alipay import const


_logger = get_payment_logger(__name__)


class AlipayController(Controller):

    @route(const.PROCESS_ROUTE, type="http", auth="public", methods=["POST"], csrf=False)
    def alipay_process_transaction(self, **post):
        _logger.info("Handling Alipay redirect processing with data:\n%s", pprint.pformat(post))
        request.env["payment.transaction"].sudo()._process("alipay", {
            "reference": post.get("reference"),
            "trade_status": "WAIT_BUYER_PAY",
        })
        return request.redirect("/payment/status")

    @route(const.SIMULATE_SUCCESS_ROUTE, type="http", auth="public", methods=["POST"], csrf=False)
    def alipay_simulate_success(self, **post):
        tx = request.env["payment.transaction"].sudo().search([
            ("provider_code", "=", "alipay"),
            ("reference", "=", post.get("reference")),
            ("alipay_simulation_token", "=", post.get("simulation_token")),
        ], limit=1)
        if not tx or not tx.provider_id.sudo().alipay_simulation_mode:
            return request.redirect("/payment/status")

        tx._process("alipay", {
            "reference": tx.reference,
            "out_trade_no": tx.alipay_out_trade_no,
            "trade_no": f"SIM-{tx.id}",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": str(tx.amount),
        })
        tx.alipay_simulation_token = False
        return request.redirect("/payment/status")

    @route(const.NOTIFY_ROUTE, type="http", auth="public", methods=["POST"], csrf=False)
    def alipay_notify(self, **post):
        notification = dict(post)
        try:
            app_id = notification.get("app_id")
            provider = request.env["payment.provider"].sudo().search([
                ("code", "=", "alipay"),
                ("state", "in", ["enabled", "test"]),
                ("alipay_app_id", "=", app_id),
            ], limit=1)
            if not provider:
                raise ValueError("No matching Alipay provider.")
            provider._alipay_verify_notification(notification)

            tx = request.env["payment.transaction"].sudo().search([
                ("provider_code", "=", "alipay"),
                ("alipay_out_trade_no", "=", notification.get("out_trade_no")),
            ], limit=1)
            if not tx or tx.provider_id != provider:
                raise ValueError("No matching Alipay transaction.")
            tx._process("alipay", notification)
        except Exception:
            _logger.exception("Alipay notification processing failed.")
            return request.make_response("failure", headers=[("Content-Type", "text/plain")])

        return request.make_response("success", headers=[("Content-Type", "text/plain")])
