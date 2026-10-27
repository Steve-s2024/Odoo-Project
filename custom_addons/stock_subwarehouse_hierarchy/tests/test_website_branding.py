from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.stock_subwarehouse_hierarchy.models.ir_http import IrHttp


@tagged("post_install", "-at_install")
class TestWebsiteBranding(TransactionCase):
    def _format_title(self, title, language):
        fake_request = SimpleNamespace(lang=SimpleNamespace(code=language))
        with patch(
            "odoo.addons.stock_subwarehouse_hierarchy.models.ir_http.request",
            fake_request,
        ):
            return IrHttp._sun_format_website_title(title)

    def test_custom_page_titles_follow_requested_language(self):
        self.assertEqual(
            self._format_title("Details | SUN", "en_US"),
            "Discover | SUN",
        )
        self.assertEqual(
            self._format_title("Purchase | SUN", "en_US"),
            "Stores | SUN",
        )
        self.assertEqual(
            self._format_title("Purchase | 思安奇", "zh_CN"),
            "门店 | 思安奇",
        )

    def test_language_selector_uses_supplied_flag_assets(self):
        view = self.env.ref(
            "stock_subwarehouse_hierarchy.sun_language_selector_flags"
        )
        self.assertIn("static/img/flags/en_US.png", view.arch_db)
        self.assertIn("static/img/flags/zh_CN.png", view.arch_db)
