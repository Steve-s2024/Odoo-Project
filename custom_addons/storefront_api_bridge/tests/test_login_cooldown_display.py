from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.storefront_api_bridge.controllers import native_login as login_module
from odoo.addons.storefront_api_bridge.controllers.native_login import StorefrontNativeLogin
from odoo.addons.storefront_api_bridge.models.api_client import StorefrontApiError


@tagged("post_install", "-at_install")
class TestStorefrontLoginCooldownDisplay(TransactionCase):
    def _message_for_language(self, language, seconds):
        error = StorefrontApiError(
            "cooldown",
            code="login_cooldown",
            status=429,
            details={"retry_after_seconds": seconds},
        )
        fake_request = SimpleNamespace(env=SimpleNamespace(lang=language))
        with patch.object(login_module, "request", fake_request):
            return StorefrontNativeLogin._cooldown_message(error)

    def test_chinese_login_shows_remaining_cooldown_and_reset_availability(self):
        message = self._message_for_language("zh_CN", 3599)

        self.assertIn("60 分钟", message)
        self.assertIn("密码重置仍可使用", message)

    def test_english_login_shows_remaining_cooldown_and_reset_availability(self):
        message = self._message_for_language("en_US", 61)

        self.assertIn("2 minutes", message)
        self.assertIn("Password reset remains available", message)
