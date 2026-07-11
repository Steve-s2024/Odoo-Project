import json
import pprint

from odoo import _
from odoo.exceptions import ValidationError
from odoo.http import Controller, request, route

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_wechatpay import const


_logger = get_payment_logger(__name__)


class WeChatPayController(Controller):

    @route(const.PROCESS_ROUTE, type="http", auth="public", methods=["POST"], csrf=False)
    def wechatpay_process_transaction(self, **post):
        _logger.info("Handling WeChat Pay redirect processing with data:\n%s", pprint.pformat(post))
        request.env["payment.transaction"].sudo()._process(
            "wechatpay",
            {
                "reference": post.get("reference"),
                "trade_state": "NOTPAY",
            },
        )
        return request.redirect("/payment/status")

    @route(const.NOTIFY_ROUTE, type="http", auth="public", methods=["POST"], csrf=False)
    def wechatpay_notify(self, **kwargs):
        body = request.httprequest.get_data(as_text=True)
        headers = request.httprequest.headers
        serial_no = headers.get("Wechatpay-Serial")
        timestamp = headers.get("Wechatpay-Timestamp")
        nonce = headers.get("Wechatpay-Nonce")
        signature = headers.get("Wechatpay-Signature")

        try:
            notification = json.loads(body)
            resource = notification["resource"]
            provider = request.env["payment.provider"].sudo().search([
                ("code", "=", "wechatpay"),
                ("state", "in", ["enabled", "test"]),
                ("wechatpay_platform_serial_no", "=", serial_no),
            ], limit=1)
            if not provider:
                raise ValidationError(_("找不到匹配的微信支付提供商。"))

            provider._wechatpay_verify_notification_signature(
                timestamp,
                nonce,
                body,
                signature,
                serial_no,
            )
            payment_data = provider._wechatpay_decrypt_notification_resource(resource)
            tx = request.env["payment.transaction"].sudo().search([
                ("provider_code", "=", "wechatpay"),
                ("wechatpay_out_trade_no", "=", payment_data.get("out_trade_no")),
            ], limit=1)
            if not tx:
                raise ValidationError(_("找不到对应的 Odoo 支付交易。"))
            tx._process("wechatpay", payment_data)
        except Exception:
            _logger.exception("微信支付回调处理失败。")
            return request.make_json_response({"code": "FAIL", "message": "失败"}, status=500)

        return request.make_json_response({"code": "SUCCESS", "message": "成功"})
