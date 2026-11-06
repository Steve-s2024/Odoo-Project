from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStorefrontTermsTemplate(TransactionCase):
    def test_terms_replace_odoo_placeholder_with_bilingual_consumer_terms(self):
        view = self.env.ref(
            "storefront_terms_template.sun_account_terms_conditions_page"
        )
        source = str(view.arch_db)

        self.assertIn("Terms of Service", source)
        self.assertIn("服务条款", source)
        self.assertIn("15-minute countdown", source)
        self.assertIn("15 分钟倒计时", source)
        self.assertIn("seven days", source)
        self.assertIn("七日内无理由退货", source)
        self.assertNotIn("You should update this document", source)
        self.assertNotIn("10% of the sum remaining due", source)
