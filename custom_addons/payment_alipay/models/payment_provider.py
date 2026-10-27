import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_alipay import const


_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("alipay", "Alipay")],
        ondelete={"alipay": "set default"},
    )
    alipay_app_id = fields.Char(string="Alipay App ID", groups="base.group_system")
    alipay_seller_id = fields.Char(string="Alipay Seller ID", groups="base.group_system")
    alipay_private_key = fields.Text(string="Merchant RSA private key", groups="base.group_system")
    alipay_public_key = fields.Text(string="Alipay RSA public key", groups="base.group_system")
    alipay_official_sandbox = fields.Boolean(
        string="Use Alipay official sandbox gateway",
        help="Use Alipay's official sandbox endpoint with sandbox App ID and keys.",
    )
    alipay_simulation_mode = fields.Boolean(
        string="Local payment simulator",
        help="Simulate successful Alipay payments locally. No request is sent to Alipay and no money is charged.",
    )

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda provider: provider.code == "alipay").update({
            "support_express_checkout": False,
            "support_manual_capture": None,
            "support_refund": "partial",
            "support_tokenization": False,
        })

    def _get_default_payment_method_codes(self):
        self.ensure_one()
        if self.code != "alipay":
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _get_supported_currencies(self):
        self.ensure_one()
        if self.code != "alipay":
            return super()._get_supported_currencies()
        return self.env["res.currency"].with_context(active_test=False).search([("name", "=", "CNY")])

    @api.constrains(
        "state", "code", "alipay_simulation_mode", "alipay_app_id",
        "alipay_private_key", "alipay_public_key",
    )
    def _check_alipay_credentials(self):
        for provider in self.filtered(lambda item: item.code == "alipay" and item.state != "disabled"):
            if provider.alipay_simulation_mode:
                continue
            missing = []
            for field_name in ("alipay_app_id", "alipay_private_key", "alipay_public_key"):
                if not provider[field_name]:
                    missing.append(provider._fields[field_name].string)
            if missing:
                raise ValidationError(_("Alipay live or official sandbox mode is missing: %s", ", ".join(missing)))

    def _alipay_sdk_client(self):
        """Build the official Alipay client; production never uses local crypto."""
        self.ensure_one()
        try:
            from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
            from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
        except ImportError as error:
            raise ValidationError(_(
                "Alipay production mode requires the official alipay-sdk-python package."
            )) from error
        config = AlipayClientConfig()
        config.server_url = (
            const.SANDBOX_GATEWAY if self.alipay_official_sandbox
            else const.PRODUCTION_GATEWAY
        )
        config.app_id = self.alipay_app_id
        config.app_private_key = self.alipay_private_key
        config.alipay_public_key = self.alipay_public_key
        config.sign_type = "RSA2"
        return DefaultAlipayClient(alipay_client_config=config, logger=_logger)

    def _alipay_verify_notification(self, values):
        self.ensure_one()
        if values.get("app_id") != self.alipay_app_id:
            raise ValidationError(_("The Alipay notification App ID does not match."))
        if self.alipay_seller_id and values.get("seller_id") != self.alipay_seller_id:
            raise ValidationError(_("The Alipay notification seller does not match."))
        signature = values.get("sign")
        if not signature:
            raise ValidationError(_("The Alipay notification has no signature."))
        try:
            from alipay.aop.api.util.SignatureUtils import get_sign_content, verify_with_rsa
            signed_values = {
                key: value for key, value in values.items()
                if key not in ("sign", "sign_type") and value not in (None, "")
            }
            valid = verify_with_rsa(
                self.alipay_public_key,
                get_sign_content(signed_values).encode("utf-8"),
                signature,
            )
        except Exception as error:
            raise ValidationError(_("The Alipay notification signature is invalid.")) from error
        if not valid:
            raise ValidationError(_("The Alipay notification signature is invalid."))

    def _alipay_api_request(self, method, biz_content, notify_url=None, reference=None):
        self.ensure_one()
        try:
            if method == "alipay.trade.precreate":
                from alipay.aop.api.domain.AlipayTradePrecreateModel import AlipayTradePrecreateModel
                from alipay.aop.api.request.AlipayTradePrecreateRequest import AlipayTradePrecreateRequest
                from alipay.aop.api.response.AlipayTradePrecreateResponse import AlipayTradePrecreateResponse
                model = AlipayTradePrecreateModel.from_alipay_dict(biz_content)
                sdk_request = AlipayTradePrecreateRequest(biz_model=model)
                sdk_request.notify_url = notify_url
                response = AlipayTradePrecreateResponse()
            elif method == "alipay.trade.refund":
                from alipay.aop.api.domain.AlipayTradeRefundModel import AlipayTradeRefundModel
                from alipay.aop.api.request.AlipayTradeRefundRequest import AlipayTradeRefundRequest
                from alipay.aop.api.response.AlipayTradeRefundResponse import AlipayTradeRefundResponse
                model = AlipayTradeRefundModel.from_alipay_dict(biz_content)
                sdk_request = AlipayTradeRefundRequest(biz_model=model)
                response = AlipayTradeRefundResponse()
            else:
                raise ValidationError(_("Unsupported Alipay SDK operation: %s", method))
            content = self._alipay_sdk_client().execute(sdk_request)
            response.parse_response_content(content)
        except ValidationError:
            raise
        except Exception as error:
            raise ValidationError(_("Alipay SDK request failed: %s", str(error))) from error
        result = {
            "code": response.code,
            "msg": response.msg,
            "sub_code": response.sub_code,
            "sub_msg": response.sub_msg,
            "out_trade_no": response.out_trade_no,
        }
        if method == "alipay.trade.precreate":
            result["qr_code"] = response.qr_code
        else:
            result.update({
                "trade_no": response.trade_no,
                "refund_fee": response.refund_fee,
                "fund_change": response.fund_change,
            })
        return result
