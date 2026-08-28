import json
import os
import shutil
import subprocess
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_lianlian import const


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("lianlian", "连连收银台")],
        ondelete={"lianlian": "set default"},
    )
    lianlian_environment = fields.Selection(
        [("sandbox", "沙箱"), ("product", "生产")],
        string="连连环境",
        default="sandbox",
        required=True,
        groups="base.group_system",
    )
    lianlian_merchant_id = fields.Char(
        string="连连商户号", groups="base.group_system",
    )
    lianlian_sub_merchant_id = fields.Char(
        string="连连站点号", groups="base.group_system",
    )
    lianlian_merchant_private_key_path = fields.Char(
        string="商户 RSA 私钥文件",
        groups="base.group_system",
        help="仅填写 ERP 服务器上的 PKCS8 私钥文件路径；私钥不会保存到数据库或商店。",
    )
    lianlian_public_key_path = fields.Char(
        string="连连 RSA 公钥文件",
        groups="base.group_system",
        help="填写 ERP 服务器上的连连公钥文件路径，用于验证 API 响应和异步通知。",
    )
    lianlian_callback_base_url = fields.Char(
        string="连连 HTTPS 回调根地址",
        groups="base.group_system",
        help=(
            "必须是公开可访问的 HTTPS 根地址。可使用仅转发连连签名通知的"
            "商店端代理，但支付状态仍必须由 ERP 验签并最终裁决。"
        ),
    )
    lianlian_merchant_country = fields.Char(
        string="商户主体国家代码",
        default="CN",
        groups="base.group_system",
    )
    lianlian_currency_codes = fields.Char(
        string="支持币种",
        default="CNY,USD",
        groups="base.group_system",
        help="使用英文逗号分隔，例如 CNY,USD。",
    )
    lianlian_sandbox_customer_email = fields.Char(
        string="沙箱测试邮箱",
        default="pass@lianlianpay.com",
        groups="base.group_system",
        help="仅沙箱使用；连连验收用例可通过该邮箱选择预设测试结果。",
    )
    lianlian_node_binary = fields.Char(
        string="Node.js 可执行文件",
        default="node",
        groups="base.group_system",
    )

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda provider: provider.code == "lianlian").update({
            "support_express_checkout": False,
            "support_manual_capture": None,
            "support_refund": "partial",
            "support_tokenization": False,
        })

    def _get_default_payment_method_codes(self):
        self.ensure_one()
        if self.code != "lianlian":
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _get_supported_currencies(self):
        self.ensure_one()
        if self.code != "lianlian":
            return super()._get_supported_currencies()
        codes = [
            code.strip().upper()
            for code in (self.lianlian_currency_codes or "").split(",")
            if code.strip()
        ]
        return self.env["res.currency"].with_context(active_test=False).search([
            ("name", "in", codes),
        ])

    @api.constrains(
        "state", "code", "lianlian_environment", "lianlian_merchant_id",
        "lianlian_sub_merchant_id", "lianlian_merchant_private_key_path",
        "lianlian_public_key_path", "lianlian_callback_base_url",
    )
    def _check_lianlian_credentials(self):
        for provider in self.filtered(
            lambda item: item.code == "lianlian" and item.state != "disabled"
        ):
            missing = [
                field_name
                for field_name in (
                    "lianlian_merchant_id",
                    "lianlian_sub_merchant_id",
                    "lianlian_merchant_private_key_path",
                    "lianlian_public_key_path",
                    "lianlian_callback_base_url",
                )
                if not provider[field_name]
            ]
            if missing:
                labels = [provider._fields[name].string for name in missing]
                raise ValidationError(_("连连收银台缺少配置：%s", "、".join(labels)))
            for path_field in (
                "lianlian_merchant_private_key_path", "lianlian_public_key_path",
            ):
                path = os.path.abspath(os.path.expanduser(provider[path_field]))
                if not os.path.isfile(path):
                    raise ValidationError(_("连连密钥文件不存在：%s", path))
            parsed = urlparse(provider.lianlian_callback_base_url)
            if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
                raise ValidationError(_(
                    "连连 HTTPS 回调根地址必须为不含路径的 HTTPS 地址，例如 https://shop.example.com。"
                ))
            if provider.state == "test" and provider.lianlian_environment != "sandbox":
                raise ValidationError(_("测试状态只能使用连连沙箱环境。"))
            if provider.state == "enabled" and provider.lianlian_environment != "product":
                raise ValidationError(_("正式启用状态只能使用连连生产环境。"))

    def _lianlian_sdk_directory(self):
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), "sdk")

    def _lianlian_sdk_call(self, operation, params=None, body=None, headers=None):
        """Execute only the provider-maintained SDK; no local signing fallback exists."""
        self.ensure_one()
        node_binary = self.lianlian_node_binary or "node"
        resolved_node = shutil.which(node_binary) if not os.path.isabs(node_binary) else node_binary
        if not resolved_node or not os.path.isfile(resolved_node):
            raise ValidationError(_("未找到 Node.js，无法运行连连官方 SDK。"))

        sdk_dir = self._lianlian_sdk_directory()
        runner = os.path.join(sdk_dir, "bridge.js")
        package_marker = os.path.join(
            sdk_dir, "node_modules", const.SDK_PACKAGE_NAME, "package.json",
        )
        if not os.path.isfile(runner) or not os.path.isfile(package_marker):
            raise ValidationError(_(
                "连连官方 SDK 未安装。请在 %s 中安装 %s@%s。",
                sdk_dir, const.SDK_PACKAGE_NAME, const.SDK_PACKAGE_VERSION,
            ))

        request_values = {
            "operation": operation,
            "config": {
                "env": self.lianlian_environment,
                "merchant_id": self.lianlian_merchant_id,
                "sub_merchant_id": self.lianlian_sub_merchant_id or "",
                "merchant_private_key_path": os.path.abspath(
                    os.path.expanduser(self.lianlian_merchant_private_key_path)
                ),
                "lianlian_public_key_path": os.path.abspath(
                    os.path.expanduser(self.lianlian_public_key_path)
                ),
            },
            "params": params or {},
            "body": body or "",
            "headers": headers or {},
        }
        try:
            completed = subprocess.run(
                [resolved_node, runner],
                input=json.dumps(request_values, ensure_ascii=False),
                text=True,
                capture_output=True,
                cwd=sdk_dir,
                timeout=40,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ValidationError(_("连连支付请求超时；交易状态仍待 ERP 查询确认。")) from error
        except OSError as error:
            raise ValidationError(_("无法启动连连官方 SDK。")) from error

        try:
            response = json.loads((completed.stdout or "").strip())
        except (TypeError, ValueError) as error:
            raise ValidationError(_("连连官方 SDK 返回了无法解析的响应。")) from error
        if completed.returncode or not response.get("ok"):
            raise ValidationError(
                response.get("error") or _("连连官方 SDK 请求失败。")
            )
        return response.get("result")

    def _lianlian_verified_response_body(self, sdk_result):
        self.ensure_one()
        if not isinstance(sdk_result, dict) or sdk_result.get("verifySignResult") is not True:
            raise ValidationError(_("连连响应签名验证失败。"))
        body = sdk_result.get("body")
        try:
            values = json.loads(body) if isinstance(body, str) else dict(body or {})
        except (TypeError, ValueError) as error:
            raise ValidationError(_("连连响应正文无法解析。")) from error
        if values.get("return_code") != "SUCCESS":
            raise ValidationError(
                values.get("return_message") or values.get("decline_code")
                or _("连连支付请求被拒绝。")
            )
        return values

    def _lianlian_payment_request(self, params):
        return self._lianlian_verified_response_body(
            self._lianlian_sdk_call("pay", params=params)
        )

    def _lianlian_payment_query(self, merchant_transaction_id):
        return self._lianlian_verified_response_body(self._lianlian_sdk_call(
            "payResultQuery",
            params={"merchant_transaction_id": merchant_transaction_id},
        ))

    def _lianlian_refund_request(self, params):
        return self._lianlian_verified_response_body(
            self._lianlian_sdk_call("refund", params=params)
        )

    def _lianlian_refund_query(self, merchant_transaction_id):
        return self._lianlian_verified_response_body(self._lianlian_sdk_call(
            "refundResultQuery",
            params={"merchant_transaction_id": merchant_transaction_id},
        ))

    def _lianlian_verify_notice(self, body, headers):
        self.ensure_one()
        result = self._lianlian_sdk_call(
            "notice", body=body, headers=headers,
        )
        if not isinstance(result, dict) or result.get("verifySignResult") is not True:
            raise ValidationError(_("连连异步通知签名验证失败。"))
        verified_body = result.get("body")
        try:
            return json.loads(verified_body) if isinstance(verified_body, str) else dict(verified_body or {})
        except (TypeError, ValueError) as error:
            raise ValidationError(_("连连异步通知正文无法解析。")) from error

    def _lianlian_checkout_url_is_allowed(self, checkout_url):
        self.ensure_one()
        parsed = urlparse(checkout_url or "")
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        suffix = (
            const.SANDBOX_CHECKOUT_HOST_SUFFIX
            if self.lianlian_environment == "sandbox"
            else const.PRODUCTION_CHECKOUT_HOST_SUFFIX
        )
        return parsed.hostname.lower().endswith(suffix)
