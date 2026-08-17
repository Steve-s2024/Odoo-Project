import re

from odoo import _, api, models
from odoo.exceptions import AccessDenied, ValidationError


_ENCODED_PASSWORD_RE = re.compile(r"^\$[^$]+\$[^$]+\$.+")


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _shop_api_assert_plaintext_password(self, password):
        """Reject password hashes submitted through a plaintext password field.

        Odoo's password inverse hashes every supplied value.  Passing a hash
        copied from another database therefore creates a hash-of-a-hash which
        can never be used with the original password.  Authentication data is
        never portable between ERP and Shop; Shop must ask ERP to verify or
        change a plaintext credential over the authenticated API channel.
        """
        if password and _ENCODED_PASSWORD_RE.match(str(password)):
            raise ValidationError(_(
                "密码字段不能填写或导入加密后的密码。请填写新的原始密码，"
                "系统只会在 ERP 中安全加密一次。"
            ))

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self._shop_api_assert_plaintext_password(
                values.get("password") or values.get("new_password")
            )
        return super().create(vals_list)

    def write(self, values):
        self._shop_api_assert_plaintext_password(
            values.get("password") or values.get("new_password")
        )
        return super().write(values)

    def _shop_api_change_password(self, current_password, new_password):
        """Change one ERP-owned credential without exporting its hash."""
        self.ensure_one()
        if not current_password:
            raise AccessDenied()
        new_password = str(new_password or "").strip()
        if not new_password:
            raise ValidationError(_("新密码不能为空。"))
        self._shop_api_assert_plaintext_password(new_password)
        acting_user = self.with_user(self).sudo()
        acting_user._check_credentials({
            "type": "password",
            "login": self.login,
            "password": current_password,
        }, {"interactive": True})

        # Assign the plaintext value only to Odoo's password inverse on ERP.
        # This intentionally bypasses the Shop presentation module's
        # change_password proxy when both modules coexist in a test database.
        # Odoo stores a one-way hash exactly once at this boundary.
        self.sudo().password = new_password
        self.partner_id.sudo().signup_cancel()

        # Refuse an affirmative API response unless the new plaintext value
        # verifies against the hash ERP has just stored.
        acting_user._check_credentials({
            "type": "password",
            "login": self.login,
            "password": new_password,
        }, {"interactive": True})
        return True
