from lxml import html

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestErpLoginChinesePresentation(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "sun.erp_login.enabled", "True"
        )

    def _assert_chinese_native_login(self, response):
        self.assertEqual(response.status_code, 200)
        document = html.fromstring(response.content)

        page_title = "".join(document.xpath("//title/text()"))
        self.assertIn("思安奇ERP系统登录", page_title)
        self.assertNotIn("Login", page_title)
        self.assertEqual(
            document.xpath("string(//label[@for='login'])").strip(),
            "账号或邮箱",
        )
        self.assertEqual(
            document.xpath("//input[@id='login']/@placeholder"),
            ["请输入账号或邮箱"],
        )
        self.assertEqual(
            document.xpath("string(//label[@for='password'])").strip(),
            "密码",
        )
        self.assertEqual(
            document.xpath("//input[@id='password']/@placeholder"),
            ["请输入密码"],
        )
        self.assertEqual(
            document.xpath(
                "string(//div[contains(@class, 'oe_login_buttons')]/button[@type='submit'][1])"
            ).strip(),
            "登录",
        )
        self.assertIn("重置密码", response.text)
        self.assertIn("思安奇ERP系统", response.text)
        self.assertIn("sun_erp_login_logo", response.text)

        forms = document.xpath("//form[@method='post']")
        self.assertTrue(forms)
        self.assertTrue(document.xpath("//input[@name='csrf_token']/@value"))
        self.assertNotIn('<header id="top"', response.text)
        self.assertNotIn('<footer id="bottom"', response.text)

    def test_default_erp_login_is_chinese(self):
        self._assert_chinese_native_login(self.url_open("/web/login"))

    def test_english_prefixed_erp_login_is_chinese(self):
        response = self.url_open(
            "/en/web/login",
            headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        self._assert_chinese_native_login(response)
        self.assertNotIn(">Email</label>", response.text)
        self.assertNotIn(">Log in</button>", response.text)
        self.assertNotIn(">Reset Password</a>", response.text)

    def test_english_prefixed_access_error_is_chinese(self):
        response = self.url_open(
            "/en/web/login?error=access",
            headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("只有员工可以访问此数据库，请与管理员联系。", response.text)
        self.assertNotIn("Only employees can access this database", response.text)

    def test_non_erp_database_keeps_native_english_login(self):
        parameters = self.env["ir.config_parameter"].sudo()
        parameters.set_param("sun.erp_login.enabled", "False")
        try:
            response = self.url_open(
                "/en/web/login",
                headers={"Accept-Language": "en-US,en;q=0.9"},
            )
        finally:
            parameters.set_param("sun.erp_login.enabled", "True")

        self.assertEqual(response.status_code, 200)
        document = html.fromstring(response.content)
        self.assertEqual(
            document.xpath("string(//label[@for='login'])").strip(),
            "Email",
        )
        self.assertEqual(
            document.xpath("//input[@id='login']/@placeholder"),
            ["Enter your email"],
        )
        self.assertFalse(document.xpath("//*[contains(@class, 'sun_erp_login_page')]"))
