import json
import logging
import os

from odoo import _, api, fields, models, tools
from odoo.exceptions import ValidationError

from odoo.addons.payment_wechatpay import const


_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("wechatpay", "微信支付")],
        ondelete={"wechatpay": "set default"},
    )
    wechatpay_appid = fields.Char(
        string="微信 AppID",
        groups="base.group_system",
    )
    wechatpay_mchid = fields.Char(
        string="微信支付商户号",
        groups="base.group_system",
    )
    wechatpay_api_v3_key = fields.Char(
        string="API v3 密钥",
        groups="base.group_system",
    )
    wechatpay_merchant_serial_no = fields.Char(
        string="商户证书序列号",
        groups="base.group_system",
    )
    wechatpay_private_key = fields.Text(
        string="商户 API 私钥",
        groups="base.group_system",
    )
    wechatpay_platform_serial_no = fields.Char(
        string="微信平台证书序列号",
        groups="base.group_system",
    )
    wechatpay_platform_certificate = fields.Text(
        string="微信平台证书",
        groups="base.group_system",
    )
    wechatpay_simulation_mode = fields.Boolean(
        string="模拟模式",
        help="Use fake WeChat Pay QR codes and manual success confirmation for checkout testing.",
    )

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == "wechatpay").update({
            "support_express_checkout": False,
            "support_manual_capture": None,
            "support_refund": "partial",
            "support_tokenization": False,
        })

    def _get_default_payment_method_codes(self):
        self.ensure_one()
        if self.code != "wechatpay":
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _get_supported_currencies(self):
        self.ensure_one()
        if self.code != "wechatpay":
            return super()._get_supported_currencies()
        return self.env["res.currency"].with_context(active_test=False).search([("name", "=", "CNY")])

    @api.constrains(
        "state",
        "code",
        "wechatpay_simulation_mode",
        "wechatpay_appid",
        "wechatpay_mchid",
        "wechatpay_api_v3_key",
        "wechatpay_merchant_serial_no",
        "wechatpay_private_key",
        "wechatpay_platform_serial_no",
        "wechatpay_platform_certificate",
    )
    def _check_wechatpay_credentials(self):
        credential_fields = [
            "wechatpay_appid",
            "wechatpay_mchid",
            "wechatpay_api_v3_key",
            "wechatpay_merchant_serial_no",
            "wechatpay_private_key",
        ]
        for provider in self.filtered(lambda p: p.code == "wechatpay" and p.state != "disabled"):
            if provider.wechatpay_simulation_mode:
                continue
            missing_labels = [
                self.env["ir.model.fields"]._get(self._name, field_name).field_description
                for field_name in credential_fields
                if not provider[field_name]
            ]
            if missing_labels:
                raise ValidationError(_("微信支付正式模式缺少配置字段：%s", ", ".join(missing_labels)))

    def _wechatpay_sdk(self):
        """Return the maintained SDK client; never fall back to local crypto."""
        self.ensure_one()
        try:
            from wechatpayv3 import WeChatPay, WeChatPayType
        except ImportError as error:
            raise ValidationError(_(
                "微信支付正式模式需要经过公开测试的 wechatpayv3 SDK；请安装锁定版本后重试。"
            )) from error
        certificate_dir = os.path.join(
            tools.config.filestore(self.env.cr.dbname),
            "wechatpay-certificates",
            str(self.id),
            "",
        )
        os.makedirs(certificate_dir, mode=0o700, exist_ok=True)
        return WeChatPay(
            wechatpay_type=WeChatPayType.NATIVE,
            mchid=self.wechatpay_mchid,
            private_key=self.wechatpay_private_key,
            cert_serial_no=self.wechatpay_merchant_serial_no,
            apiv3_key=self.wechatpay_api_v3_key,
            appid=self.wechatpay_appid,
            cert_dir=certificate_dir,
            logger=_logger,
            timeout=(10, 30),
        )

    @staticmethod
    def _wechatpay_sdk_response(code, message):
        try:
            payload = json.loads(message or "{}")
        except (TypeError, ValueError):
            payload = {"message": str(message or "")}
        if code not in (200, 201):
            raise ValidationError(
                payload.get("message") or payload.get("code") or _("微信支付请求失败。")
            )
        return payload

    def _wechatpay_native_pay(self, **values):
        self.ensure_one()
        client = self._wechatpay_sdk()
        from wechatpayv3 import WeChatPayType
        code, message = client.pay(
            pay_type=WeChatPayType.NATIVE,
            **values,
        )
        return self._wechatpay_sdk_response(code, message)

    def _wechatpay_refund(self, **values):
        self.ensure_one()
        code, message = self._wechatpay_sdk().refund(**values)
        return self._wechatpay_sdk_response(code, message)

    def _wechatpay_parse_notification(self, headers, body):
        self.ensure_one()
        notification = self._wechatpay_sdk().callback(dict(headers), body)
        if not notification or not notification.get("resource"):
            raise ValidationError(_("微信支付 SDK 未能验证或解密通知。"))
        return notification["resource"]
