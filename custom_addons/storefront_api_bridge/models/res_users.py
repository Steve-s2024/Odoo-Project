from odoo import Command, fields, models
from odoo.exceptions import AccessDenied


class ResUsers(models.Model):
    _inherit = "res.users"

    x_storefront_remote_customer_id = fields.Char(
        string="ERP customer identifier",
        copy=False,
        readonly=True,
        index=True,
    )

    def _check_credentials(self, credential, user_agent_env):
        """Block all interactive password checks against the Shop database.

        `/web/login` never calls this method: it obtains an authoritative ERP
        decision and finalizes a native Odoo session directly.  Blocking here
        closes JSON-RPC and other alternate local-password login paths while
        leaving API-key and non-interactive session validation untouched.
        """
        if (
            (credential or {}).get("type") == "password"
            and (user_agent_env or {}).get("interactive", True)
        ):
            raise AccessDenied()
        return super()._check_credentials(credential, user_agent_env)

    def _storefront_editor_groups(self):
        """Return the smallest local group set needed by an ERP-approved editor."""
        return [
            self.env.ref("base.group_user").id,
            self.env.ref("website.group_website_designer").id,
        ]

    def _storefront_provision_native_user(self, profile):
        """Map an ERP-confirmed identity to one local Odoo user.

        This method deliberately does not receive or write a password.  ERP is
        the only credential authority; the Shop stores profile/session data
        only.  Internal access is granted only when the authoritative response
        explicitly marks the identity as both internal and a website editor.
        """
        remote_id = str(profile.get("id") or "").strip()
        canonical_login = str(
            profile.get("login") or profile.get("email") or ""
        ).strip()
        if (
            not remote_id
            or not canonical_login
            or profile.get("authoritative") is not True
            or profile.get("authentication_backend") != "odoo_native"
            or profile.get("authentication_version") != 2
        ):
            raise AccessDenied()

        Users = self.sudo().with_context(active_test=False)
        user = Users.search([
            ("x_storefront_remote_customer_id", "=", remote_id),
        ], limit=1)
        if not user:
            remote_partner = self.env["res.partner"].sudo().search([
                ("shop_api_uuid", "=", remote_id),
            ], limit=1)
            if remote_partner:
                user = Users.search([
                    ("partner_id", "=", remote_partner.id),
                ], limit=1)
        if not user:
            user = Users.search([("login", "=", canonical_login)], limit=1)

        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        partner = user.partner_id if user else Partner.search([
            ("shop_api_uuid", "=", remote_id),
        ], limit=1)
        partner_values = {
            "name": profile.get("name") or canonical_login,
            "email": profile.get("email") or False,
            "phone": profile.get("phone") or False,
            "lang": (
                profile.get("language")
                if profile.get("language") in ("zh_CN", "en_US")
                else "zh_CN"
            ),
            "active": True,
        }
        if partner:
            partner.write(partner_values)
            uuid_owner = Partner.search([
                ("shop_api_uuid", "=", remote_id),
                ("id", "!=", partner.id),
            ], limit=1)
            if not uuid_owner:
                partner.shop_api_uuid = remote_id
        else:
            partner = Partner.create({"shop_api_uuid": remote_id, **partner_values})

        is_editor = bool(
            profile.get("is_internal") and profile.get("website_editor")
        )
        group_ids = (
            self._storefront_editor_groups()
            if is_editor
            else [self.env.ref("base.group_portal").id]
        )
        user_values = {
            "login": canonical_login,
            "partner_id": partner.id,
            "active": True,
            "x_storefront_remote_customer_id": remote_id,
            "group_ids": [Command.set(group_ids)],
        }
        if user:
            user.write(user_values)
        else:
            user = Users.with_context(no_reset_password=True).create(user_values)

        for address_data in profile.get("addresses") or []:
            address_id = str(address_data.get("id") or "").strip()
            if not address_id:
                continue
            address = Partner.search([
                ("shop_api_uuid", "=", address_id),
            ], limit=1)
            country = self.env["res.country"]
            country_code = str(address_data.get("country") or "").upper()
            if country_code:
                country = self.env["res.country"].sudo().search([
                    ("code", "=", country_code),
                ], limit=1)
            address_values = {
                "parent_id": partner.commercial_partner_id.id,
                "type": (
                    address_data.get("type")
                    if address_data.get("type") in (
                        "delivery", "invoice", "contact"
                    )
                    else "delivery"
                ),
                "name": address_data.get("name") or partner.name,
                "street": address_data.get("street") or False,
                "street2": address_data.get("street2") or False,
                "city": address_data.get("city") or False,
                "zip": address_data.get("zip") or False,
                "phone": address_data.get("phone") or False,
                "country_id": country.id if country else False,
                "active": bool(address_data.get("active", True)),
            }
            if address:
                address.write(address_values)
            else:
                Partner.create({"shop_api_uuid": address_id, **address_values})
        return user

    def _storefront_provision_portal_user(self, profile, local_user=None):
        """Compatibility shim for non-login customer synchronization only."""
        if local_user and not profile.get("id"):
            profile = {**profile, "id": local_user.x_storefront_remote_customer_id}
        profile = {
            **profile,
            "authoritative": True,
            "authentication_backend": "odoo_native",
            "authentication_version": 2,
            "is_internal": False,
            "website_editor": False,
        }
        return self._storefront_provision_native_user(profile)
