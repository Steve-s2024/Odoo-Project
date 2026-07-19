import base64
import json
import secrets
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_wechatpay import const


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
            "wechatpay_platform_serial_no",
            "wechatpay_platform_certificate",
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

    def _build_request_url(self, endpoint, **kwargs):
        if self.code != "wechatpay":
            return super()._build_request_url(endpoint, **kwargs)
        return f"{const.API_BASE_URL}{endpoint}"

    def _build_request_headers(self, method, endpoint, payload, **kwargs):
        if self.code != "wechatpay":
            return super()._build_request_headers(method, endpoint, payload, **kwargs)

        body = payload if isinstance(payload, str) else json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        signature = self._wechatpay_sign_message(f"{method.upper()}\n{endpoint}\n{timestamp}\n{nonce}\n{body}\n")
        authorization = (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self.wechatpay_mchid}",'
            f'nonce_str="{nonce}",'
            f'timestamp="{timestamp}",'
            f'serial_no="{self.wechatpay_merchant_serial_no}",'
            f'signature="{signature}"'
        )
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": authorization,
        }

    def _parse_response_error(self, response):
        if self.code != "wechatpay":
            return super()._parse_response_error(response)
        try:
            data = response.json()
        except ValueError:
            return response.text
        return data.get("message") or data.get("code") or response.text

    def _wechatpay_sign_message(self, message):
        self.ensure_one()
        private_key = serialization.load_pem_private_key(
            self.wechatpay_private_key.encode(),
            password=None,
        )
        signature = private_key.sign(
            message.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode()

    def _wechatpay_verify_notification_signature(self, timestamp, nonce, body, signature, serial_no):
        self.ensure_one()
        if serial_no != self.wechatpay_platform_serial_no:
            raise ValidationError(_("微信支付平台证书序列号不匹配。"))
        public_key = x509.load_pem_x509_certificate(
            self.wechatpay_platform_certificate.encode()
        ).public_key()
        message = f"{timestamp}\n{nonce}\n{body}\n".encode()
        try:
            public_key.verify(
                base64.b64decode(signature),
                message,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as error:
            raise ValidationError(_("微信支付通知签名验证失败。")) from error

    def _wechatpay_decrypt_notification_resource(self, resource):
        self.ensure_one()
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            aesgcm = AESGCM(self.wechatpay_api_v3_key.encode())
            plaintext = aesgcm.decrypt(
                resource["nonce"].encode(),
                base64.b64decode(resource["ciphertext"]),
                (resource.get("associated_data") or "").encode(),
            )
            return json.loads(plaintext.decode())
        except Exception as error:
            raise ValidationError(_("微信支付通知解密失败。")) from error
