from lxml import html

from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestErpAuthentication(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "sun.erp_login.enabled", "True"
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "auth_signup.invitation_scope", "b2b"
        )
        cls.internal_login = "erp_internal"
        cls.portal_login = "erp_portal"
        new_test_user(
            cls.env,
            login=cls.internal_login,
            password=cls.internal_login,
            groups="base.group_user",
        )
        new_test_user(
            cls.env,
            login=cls.portal_login,
            password=cls.portal_login,
            groups="base.group_portal",
        )

    def _csrf_token(self):
        response = self.url_open("/web/login")
        token = html.fromstring(response.content).xpath(
            '//input[@name="csrf_token"]'
        )[0].get("value")
        return token, response.url

    def test_root_redirects_to_discuss(self):
        response = self.url_open("/", allow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/odoo/discuss")

    def test_public_signup_is_not_available(self):
        response = self.url_open("/web/signup")

        self.assertEqual(response.status_code, 404)

    def test_erp_login_page_uses_chinese_presentation(self):
        response = self.url_open("/web/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn("登录ERP系统", response.text)
        self.assertIn("请使用已授权的员工账户登录", response.text)
        self.assertIn("请输入账号或邮箱", response.text)
        self.assertIn("请输入密码", response.text)
        self.assertIn("sun_erp_login_logo", response.text)
        self.assertIn('font-family: Arial, "Microsoft YaHei", sans-serif', response.text)
        self.assertIn("#wrapwrap > header", response.text)
        self.assertIn("#wrapwrap > footer", response.text)
        self.assertNotIn('href="/web/signup', response.text)

    def test_internal_user_login_redirects_to_discuss(self):
        csrf_token, login_url = self._csrf_token()
        response = self.url_open(
            login_url,
            data={
                "login": self.internal_login,
                "password": self.internal_login,
                "type": "password",
                "csrf_token": csrf_token,
            },
            allow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/odoo/discuss")

    def test_portal_user_is_logged_out_with_erp_warning(self):
        csrf_token, login_url = self._csrf_token()
        response = self.url_open(
            login_url,
            data={
                "login": self.portal_login,
                "password": self.portal_login,
                "type": "password",
                "csrf_token": csrf_token,
                "redirect": "/odoo/discuss",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("此账户无权访问ERP", response.text)
        denied = self.url_open("/odoo/discuss", allow_redirects=False)
        self.assertEqual(denied.status_code, 303)
        self.assertIn("/web/login", denied.headers["Location"])
