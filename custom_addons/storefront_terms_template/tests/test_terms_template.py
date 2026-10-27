from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStorefrontTermsTemplate(TransactionCase):
    def test_terms_are_a_marked_bilingual_regional_template(self):
        view = self.env.ref(
            "storefront_terms_template.sun_account_terms_conditions_page"
        )
        source = str(view.arch_db)

        self.assertIn("Terms of Service", source)
        self.assertIn("服务条款", source)
        self.assertIn("COMPLETE EVERY MARKED FIELD", source)
        self.assertIn("发布前须填写全部标记项", source)
        self.assertIn("TO BE COMPLETED BY REGION", source)
        self.assertIn("按地区待填写", source)
        self.assertIn("may differ between", source)
        self.assertIn("可能因国家或地区而异", source)
        self.assertIn("Adverse or severe weather may postpone", source)
        self.assertIn("恶劣或极端天气可能导致", source)
        self.assertNotIn("You should update this document", source)
        self.assertNotIn("10% of the sum remaining due", source)
        self.assertNotIn("思安奇冰雪科技（张家口）有限公司", source)
        self.assertNotIn("sun@snowlandholdings.com", source)
        self.assertNotIn("+86 400-008-5355", source)
        self.assertNotIn("15-minute countdown", source)
        self.assertNotIn("七日内无理由退货", source)
        self.assertNotIn("laws of the People’s Republic of China", source)
