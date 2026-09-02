import json
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLianLianProvider(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_lianlian.payment_provider_lianlian")
        cls.method = cls.env.ref("payment_lianlian.payment_method_lianlian")
        cls.currency = cls.env.ref("base.USD")
        cls.partner = cls.env.ref("base.public_partner")

    def _create_transaction(self, reference="LL-SANDBOX-TEST"):
        return self.env["payment.transaction"].create({
            "provider_id": self.provider.id,
            "payment_method_id": self.method.id,
            "reference": reference,
            "amount": 12.34,
            "currency_id": self.currency.id,
            "partner_id": self.partner.id,
            "operation": "online_redirect",
            "landing_route": "https://shop.example.test/shop/payment/status",
        })

    def test_provider_record_and_currency_support(self):
        self.provider.lianlian_currency_codes = "CNY,USD"
        self.assertEqual(self.provider.code, "lianlian")
        self.assertIn("lianlian", self.provider._get_default_payment_method_codes())
        self.assertEqual(set(self.provider._get_supported_currencies().mapped("name")), {"CNY", "USD"})
        self.assertEqual(self.provider.support_refund, "partial")

    def test_product_quantity_is_a_positive_json_integer(self):
        transaction_model = self.env["payment.transaction"]
        self.assertEqual(transaction_model._lianlian_product_quantity(1.0), 1)
        self.assertEqual(transaction_model._lianlian_product_quantity("12"), 12)
        with self.assertRaises(ValidationError):
            transaction_model._lianlian_product_quantity(1.5)
        with self.assertRaises(ValidationError):
            transaction_model._lianlian_product_quantity(0)

    def test_sdk_response_requires_verified_signature(self):
        with self.assertRaises(ValidationError):
            self.provider._lianlian_verified_response_body({
                "verifySignResult": False,
                "body": json.dumps({"return_code": "SUCCESS"}),
            })

    def test_checkout_url_is_environment_scoped(self):
        self.provider.lianlian_environment = "sandbox"
        self.assertTrue(self.provider._lianlian_checkout_url_is_allowed(
            "https://celer-gateway.lianlianpay-inc.com/publish/test"
        ))
        self.assertFalse(self.provider._lianlian_checkout_url_is_allowed(
            "https://attacker.example/payment"
        ))

    def test_signed_sandbox_create_marks_transaction_pending(self):
        tx = self._create_transaction()
        self.provider.write({
            "lianlian_environment": "sandbox",
            "lianlian_merchant_id": "202608200004113001",
            "lianlian_sub_merchant_id": "1020260820021001",
            "lianlian_callback_base_url": "https://erp.example.test",
        })
        with patch.object(type(self.provider), "_lianlian_payment_request", return_value={
            "return_code": "SUCCESS",
            "order": {
                "merchant_transaction_id": f"ODOO-{tx.company_id.id}-{tx.id}",
                "ll_transaction_id": "LL-SANDBOX-1",
                "payment_url": "https://celer-gateway.lianlianpay-inc.com/publish/test",
                "payment_data": {
                    "payment_status": "PP",
                    "payment_currency_code": "USD",
                    "payment_amount": "12.34",
                },
            },
        }):
            tx._lianlian_ensure_payment()
        self.assertEqual(tx.state, "pending")
        self.assertEqual(tx.provider_reference, "LL-SANDBOX-1")
        self.assertTrue(tx.lianlian_payment_url.startswith("https://celer-gateway."))

    def test_cashier_payload_forces_hosted_card_entry_without_card_data(self):
        tx = self._create_transaction(reference="LL-HOSTED-CARD")
        self.provider.write({
            "lianlian_environment": "sandbox",
            "lianlian_merchant_id": "202608200004113001",
            "lianlian_sub_merchant_id": "1020260820021001",
            "lianlian_callback_base_url": "https://shop.example.test",
        })

        payload = tx._lianlian_order_payload()

        self.assertEqual(payload["payment_method"], "inter_credit_card")
        self.assertEqual(
            payload["notification_url"],
            "https://shop.example.test/payment/lianlian/notify",
        )
        self.assertNotIn("payment_data", payload)
        self.assertNotIn("card_number", json.dumps(payload))

    def test_checkout_products_use_order_language_and_never_expose_product_code(self):
        partner = self.env["res.partner"].create({
            "name": "Checkout Language Test",
            "email": "checkout-language@example.invalid",
            "lang": "en_US",
        })
        product = self.env["product.product"].create({
            "name": "双板滑雪鞋",
            "default_code": "012307S2-MA100-H001230",
            "list_price": 12.34,
        })
        template = product.product_tmpl_id
        if "x_website_english_name" in template._fields:
            template.x_website_english_name = "Ski Boots"
        pricelist = self.env["product.pricelist"].search([
                ("currency_id", "=", self.currency.id),
            ], limit=1) or self.env["product.pricelist"].create({
                "name": "LianLian localization test USD",
                "currency_id": self.currency.id,
            })
        order_values = {
            "partner_id": partner.id,
            "pricelist_id": pricelist.id,
            "order_line": [Command.create({
                "product_id": product.id,
                "product_uom_qty": 1,
                "price_unit": 12.34,
            })],
        }
        if "x_website_checkout_language" in self.env["sale.order"]._fields:
            order_values["x_website_checkout_language"] = "en_US"
        order = self.env["sale.order"].create(order_values)
        transaction = self._create_transaction(reference="LL-LOCALIZED-PRODUCT")
        transaction.write({
            "partner_id": partner.id,
            "sale_order_ids": [Command.set(order.ids)],
        })

        english_payload = transaction._lianlian_order_payload()
        english_product = english_payload["merchant_order"]["products"][0]
        serialized_english = json.dumps(english_product, ensure_ascii=False)
        self.assertEqual(english_product["name"], "Ski Boots")
        self.assertIn("Color: Black", english_product["description"])
        self.assertIn("Size: 230", english_product["description"])
        self.assertNotIn(product.default_code, serialized_english)
        self.assertTrue(english_product["product_id"].startswith("ITEM-"))
        self.assertEqual(english_product["sku"], english_product["product_id"])
        self.assertNotIn(product.default_code.lower(), english_product["url"].lower())

        if "x_website_checkout_language" in order._fields:
            order.x_website_checkout_language = "zh_CN"
        else:
            partner.lang = "zh_CN"
        chinese_payload = transaction._lianlian_order_payload()
        chinese_product = chinese_payload["merchant_order"]["products"][0]
        serialized_chinese = json.dumps(chinese_product, ensure_ascii=False)
        self.assertEqual(chinese_product["name"], "双板滑雪鞋")
        self.assertIn("颜色：黑", chinese_product["description"])
        self.assertIn("尺码：230", chinese_product["description"])
        self.assertNotIn(product.default_code, serialized_chinese)

    def test_duplicate_signed_success_is_idempotent(self):
        tx = self._create_transaction(reference="LL-CALLBACK-REPLAY")
        tx.write({
            "lianlian_merchant_transaction_id": f"ODOO-{tx.company_id.id}-{tx.id}",
            "lianlian_transaction_id": "LL-PAID-1",
        })
        notice = {
            "merchant_transaction_id": tx.lianlian_merchant_transaction_id,
            "ll_transaction_id": "LL-PAID-1",
            "payment_data": {
                "payment_status": "PS",
                "payment_currency_code": "USD",
                "payment_amount": "12.34",
            },
        }
        tx._process("lianlian", notice)
        tx._process("lianlian", notice)
        self.assertEqual(tx.state, "done")

    def test_checkout_initialized_status_is_pending(self):
        tx = self._create_transaction(reference="LL-CHECKOUT-INITIALIZED")
        tx._process("lianlian", {
            "merchant_transaction_id": f"ODOO-{tx.company_id.id}-{tx.id}",
            "ll_transaction_id": "LL-INITIALIZED-1",
            "payment_url": "https://gacashier.lianlianpay-inc.com/test",
            "payment_data": {
                "payment_status": "IN",
                "payment_currency_code": "USD",
                "payment_amount": "12.34",
            },
        })
        self.assertEqual(tx.state, "pending")

    def test_payment_failed_status_is_error(self):
        tx = self._create_transaction(reference="LL-CHECKOUT-FAILED")
        tx._process("lianlian", {
            "merchant_transaction_id": f"ODOO-{tx.company_id.id}-{tx.id}",
            "ll_transaction_id": "LL-FAILED-1",
            "payment_data": {
                "payment_status": "PF",
                "payment_currency_code": "USD",
                "payment_amount": "12.34",
            },
        })
        self.assertEqual(tx.state, "error")
        self.assertIn("支付失败", tx.state_message)

    def test_refund_request_stays_pending_until_signed_query_confirms_success(self):
        source = self._create_transaction(reference="LL-REFUND-SOURCE")
        source.write({
            "lianlian_merchant_transaction_id": f"ODOO-{source.company_id.id}-{source.id}",
            "lianlian_transaction_id": "LL-PAID-REFUND-SOURCE",
        })
        source._set_done()
        refund = source._create_child_transaction(5.0, is_refund=True)
        refund_id = f"ODOO-REF-{refund.company_id.id}-{refund.id}"

        with patch.object(type(self.provider), "_lianlian_refund_request", return_value={
            "return_code": "SUCCESS",
            "order": {
                "merchant_transaction_id": refund_id,
                "original_transaction_id": source.lianlian_merchant_transaction_id,
                "ll_transaction_id": "LL-REFUND-PENDING-1",
                "refund_data": {
                    "refund_status": "RP",
                    "refund_currency_code": "USD",
                    "refund_amount": "5.00",
                },
            },
        }):
            refund._send_refund_request()

        self.assertEqual(refund.state, "pending")
        self.assertEqual(refund.lianlian_merchant_transaction_id, refund_id)

        with patch.object(type(self.provider), "_lianlian_refund_query", return_value={
            "return_code": "SUCCESS",
            "order": {
                "merchant_transaction_id": refund_id,
                "original_transaction_id": source.lianlian_merchant_transaction_id,
                "ll_transaction_id": "LL-REFUND-DONE-1",
                "refund_data": {
                    "refund_status": "RS",
                    "actual_refund_currency_code": "USD",
                    "actual_refund_amount": "5.00",
                },
            },
        }) as query_mock:
            refund._lianlian_refresh_status(force=True)

        query_mock.assert_called_once_with(refund_id)
        self.assertEqual(refund.state, "done")
        self.assertEqual(refund.provider_reference, "LL-REFUND-DONE-1")

    def test_refund_response_rejects_wrong_original_payment(self):
        source = self._create_transaction(reference="LL-REFUND-WRONG-SOURCE")
        source.write({
            "lianlian_merchant_transaction_id": f"ODOO-{source.company_id.id}-{source.id}",
        })
        source._set_done()
        refund = source._create_child_transaction(5.0, is_refund=True)
        refund.lianlian_merchant_transaction_id = f"ODOO-REF-{refund.company_id.id}-{refund.id}"

        with self.assertRaises(ValidationError):
            refund._lianlian_apply_refund_order({
                "merchant_transaction_id": refund.lianlian_merchant_transaction_id,
                "original_transaction_id": "WRONG-PAYMENT",
                "refund_data": {
                    "refund_status": "RS",
                    "actual_refund_currency_code": "USD",
                    "actual_refund_amount": "5.00",
                },
            })

    def test_failed_refund_can_reuse_same_id_and_return_to_pending(self):
        source = self._create_transaction(reference="LL-REFUND-RETRY-SOURCE")
        source.write({
            "lianlian_merchant_transaction_id": f"ODOO-{source.company_id.id}-{source.id}",
        })
        source._set_done()
        refund = source._create_child_transaction(5.0, is_refund=True)
        refund_id = f"ODOO-REF-{refund.company_id.id}-{refund.id}"
        refund.write({
            "lianlian_merchant_transaction_id": refund_id,
            "state": "error",
        })

        with patch.object(type(self.provider), "_lianlian_refund_request", return_value={
            "return_code": "SUCCESS",
            "order": {
                "merchant_transaction_id": refund_id,
                "original_transaction_id": source.lianlian_merchant_transaction_id,
                "refund_data": {
                    "refund_status": "RP",
                    "refund_currency_code": "USD",
                    "refund_amount": "5.00",
                },
            },
        }):
            refund._send_refund_request()

        self.assertEqual(refund.state, "pending")
        self.assertEqual(refund.lianlian_merchant_transaction_id, refund_id)
