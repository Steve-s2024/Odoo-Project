from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAlipayProvider(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_alipay.payment_provider_alipay")
        cls.method = cls.env.ref("payment.payment_method_alipay")
        cls.currency = cls.env.ref("base.CNY")

    def _create_transaction(self, reference="ALI-SIM-TEST"):
        return self.env["payment.transaction"].create({
            "provider_id": self.provider.id,
            "payment_method_id": self.method.id,
            "reference": reference,
            "amount": 12.34,
            "currency_id": self.currency.id,
            "partner_id": self.env.ref("base.public_partner").id,
            "operation": "online_redirect",
        })

    def test_provider_record_and_currency_support(self):
        self.assertEqual(self.provider.code, "alipay")
        self.assertIn("alipay", self.provider._get_default_payment_method_codes())
        self.assertEqual(self.provider._get_supported_currencies().mapped("name"), ["CNY"])
        self.assertEqual(self.provider.support_refund, "partial")

    def test_simulator_creates_pending_trade_and_completes(self):
        self.provider.write({"state": "test", "alipay_simulation_mode": True})
        tx = self._create_transaction()
        tx._alipay_ensure_trade()
        self.assertEqual(tx.state, "pending")
        self.assertTrue(tx.alipay_qr_code.startswith("alipay://simulated/"))
        self.assertTrue(tx.alipay_simulation_token)
        self.assertFalse(tx._get_alipay_qr_data_uri())

        tx._process("alipay", {
            "reference": tx.reference,
            "out_trade_no": tx.alipay_out_trade_no,
            "trade_no": "SIM-123",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "12.34",
        })
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.provider_reference, "SIM-123")

    def test_notification_signature_verification(self):
        self.provider.write({
            "alipay_app_id": "test-app",
            "alipay_public_key": "official-sdk-public-key",
        })
        values = {
            "app_id": "test-app",
            "out_trade_no": "ODOO1",
            "trade_no": "202608070001",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "12.34",
            "sign_type": "RSA2",
        }
        values["sign"] = "official-sdk-signature"
        with patch(
            "alipay.aop.api.util.SignatureUtils.verify_with_rsa",
            return_value=True,
        ) as verifier:
            self.provider._alipay_verify_notification(values)
        verifier.assert_called_once()

    def test_precreate_is_delegated_to_official_sdk(self):
        client = SimpleNamespace(execute=lambda request: (
            '{"code":"10000","msg":"Success","out_trade_no":"ODOO1",'
            '"qr_code":"https://qr.example.test/1"}'
        ))
        with patch.object(type(self.provider), "_alipay_sdk_client", return_value=client):
            response = self.provider._alipay_api_request(
                "alipay.trade.precreate",
                {
                    "out_trade_no": "ODOO1",
                    "total_amount": "12.34",
                    "subject": "SDK test",
                },
                notify_url="https://example.test/notify",
            )
        self.assertEqual(response["code"], "10000")
        self.assertEqual(response["qr_code"], "https://qr.example.test/1")

    def test_simulated_partial_refund_completes_authoritatively(self):
        self.provider.write({"state": "test", "alipay_simulation_mode": True})
        tx = self._create_transaction(reference="ALI-SIM-REFUND")
        tx.write({
            "alipay_out_trade_no": "ODOO-SOURCE-1",
            "provider_reference": "SIM-SOURCE-1",
        })
        tx._set_done()
        refund = tx._refund(2.34)
        self.assertEqual(refund.operation, "refund")
        self.assertEqual(refund.state, "done")
        self.assertEqual(refund.amount, -2.34)
        self.assertTrue(refund.alipay_out_refund_no)
