from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import ValidationError


@tagged("post_install", "-at_install")
class TestWeChatPayProvider(TransactionCase):

    def test_provider_record_and_currency_support(self):
        provider = self.env.ref("payment_wechatpay.payment_provider_wechatpay")
        self.assertEqual(provider.code, "wechatpay")
        self.assertIn("wechatpay", provider._get_default_payment_method_codes())
        self.assertEqual(provider._get_supported_currencies().mapped("name"), ["CNY"])

    def test_qr_data_uri_uses_code_url(self):
        provider = self.env.ref("payment_wechatpay.payment_provider_wechatpay")
        provider.write({
            "state": "test",
            "wechatpay_appid": "wx-test",
            "wechatpay_mchid": "1234567890",
            "wechatpay_api_v3_key": "a" * 32,
            "wechatpay_merchant_serial_no": "merchant-serial",
            "wechatpay_private_key": "unused-in-this-test",
            "wechatpay_platform_serial_no": "platform-serial",
            "wechatpay_platform_certificate": "unused-in-this-test",
        })
        method = self.env.ref("payment_wechatpay.payment_method_wechatpay")
        cny = self.env.ref("base.CNY")
        tx = self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": method.id,
            "reference": "WX-QR-TEST",
            "amount": 1.0,
            "currency_id": cny.id,
            "partner_id": self.env.ref("base.public_partner").id,
            "operation": "online_redirect",
        })
        tx.wechatpay_code_url = "weixin://wxpay/bizpayurl?pr=test"
        self.assertTrue(tx._get_wechatpay_qr_data_uri().startswith("data:image/png;base64,"))

    def test_simulation_mode_creates_fake_qr_without_credentials(self):
        provider = self.env.ref("payment_wechatpay.payment_provider_wechatpay")
        provider.write({
            "state": "test",
            "wechatpay_simulation_mode": True,
        })
        method = self.env.ref("payment_wechatpay.payment_method_wechatpay")
        cny = self.env.ref("base.CNY")
        tx = self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": method.id,
            "reference": "WX-SIM-TEST",
            "amount": 1.0,
            "currency_id": cny.id,
            "partner_id": self.env.ref("base.public_partner").id,
            "operation": "online_redirect",
        })
        tx._wechatpay_ensure_native_order()
        self.assertEqual(tx.state, "pending")
        self.assertTrue(tx.wechatpay_code_url.startswith("weixin://wxpay/simulated/"))

        tx._process("wechatpay", {
            "reference": tx.reference,
            "trade_state": "NOTPAY",
        })
        self.assertEqual(tx.state, "pending")

        tx._process("wechatpay", {
            "reference": tx.reference,
            "out_trade_no": tx.wechatpay_out_trade_no,
            "transaction_id": f"SIM-{tx.reference}",
            "trade_state": "SUCCESS",
        })
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.provider_reference, f"SIM-{tx.reference}")

    def test_live_mode_requires_credentials(self):
        provider = self.env.ref("payment_wechatpay.payment_provider_wechatpay")
        with self.assertRaises(ValidationError):
            provider.write({
                "state": "test",
                "wechatpay_simulation_mode": False,
            })
