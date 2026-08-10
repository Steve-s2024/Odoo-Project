from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessDenied

from .api_client import StorefrontApiError


class ResUsers(models.Model):
    _inherit = "res.users"

    x_storefront_remote_customer_id = fields.Char(
        string="ERP customer identifier",
        copy=False,
        readonly=True,
        index=True,
    )

    @api.model
    def authenticate(self, credential, user_agent_env):
        login = str((credential or {}).get("login") or "").strip()
        is_interactive_password = bool(
            login
            and credential.get("type") == "password"
            and credential.get("password")
            and (user_agent_env or {}).get("interactive", True)
        )
        if not is_interactive_password:
            return super().authenticate(credential, user_agent_env)

        local_user = self.sudo().with_context(active_test=False).search([
            ("login", "=", login), ("active", "=", True),
        ], limit=1)
        # Keep local editor authorization, but allow its password to be verified
        # by ERP so cloned internal accounts do not drift after separation.
        if local_user and local_user._is_internal():
            try:
                return super().authenticate(credential, user_agent_env)
            except AccessDenied:
                pass

        try:
            profile = self.env["storefront.erp.client"].post(
                "/api/v1/customers/authenticate",
                {"login": login, "password": credential["password"]},
            )
        except StorefrontApiError as error:
            if error.code == "mfa_required":
                raise AccessDenied(_(
                    "This account requires two-factor authentication and cannot yet sign in here."
                )) from None
            raise AccessDenied() from None

        if profile.get("website_editor") and profile.get("is_internal"):
            remote_id = str(profile.get("id") or "").strip()
            mapped_users = self.sudo().with_context(active_test=False).search([
                ("active", "=", True),
                ("partner_id.shop_api_uuid", "=", remote_id),
            ]) if remote_id else self.browse()
            mapped_editors = mapped_users.filtered(
                lambda user: user._is_internal() and (
                    user.has_group("website.group_website_designer")
                    or user.has_group("website.group_website_restricted_editor")
                )
            )
            if len(mapped_editors) == 1:
                local_user = mapped_editors

            # A customer may have signed in before the same ERP account was
            # granted website-editor access. Promote that existing local portal
            # copy only after ERP has authenticated it and explicitly confirmed
            # both internal-user and website-editor authorization.
            if local_user and not local_user._is_internal():
                canonical_login = str(
                    profile.get("login") or profile.get("email") or login
                ).strip()
                local_user = local_user.sudo()
                local_user.write({
                    "login": canonical_login,
                    "x_storefront_remote_customer_id": remote_id or False,
                    "group_ids": [Command.set([
                        self.env.ref("base.group_user").id,
                        self.env.ref("website.group_website_designer").id,
                    ])],
                })
                if remote_id:
                    uuid_owner = self.env["res.partner"].sudo().search([
                        ("shop_api_uuid", "=", remote_id),
                    ], limit=1)
                    if not uuid_owner or uuid_owner == local_user.partner_id:
                        local_user.partner_id.sudo().shop_api_uuid = remote_id

        if local_user and local_user._is_internal():
            if not profile.get("website_editor"):
                raise AccessDenied(_("This ERP account is not authorized to edit the website."))
            local_user._update_last_login()
            return {
                "uid": local_user.id,
                "auth_method": "password",
                "mfa": "skip",
            }

        user = self._storefront_provision_portal_user(profile, local_user=local_user)
        user._update_last_login()
        return {
            "uid": user.id,
            "auth_method": "password",
            "mfa": "skip",
        }

    @api.model
    def _storefront_provision_portal_user(self, profile, local_user=None):
        remote_id = str(profile.get("id") or "").strip()
        canonical_login = str(profile.get("login") or profile.get("email") or "").strip()
        if not remote_id or not canonical_login:
            raise AccessDenied()

        Users = self.sudo().with_context(active_test=False)
        user = local_user or Users.search([
            ("x_storefront_remote_customer_id", "=", remote_id),
        ], limit=1)
        if not user:
            user = Users.search([("login", "=", canonical_login)], limit=1)
        if user and user._is_internal():
            raise AccessDenied(_("This ERP account is reserved for local website administration."))

        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        partner = Partner.search([("shop_api_uuid", "=", remote_id)], limit=1)
        if not partner and user:
            partner = user.partner_id
        partner_values = {
            "name": profile.get("name") or canonical_login,
            "email": profile.get("email") or False,
            "phone": profile.get("phone") or False,
            "lang": profile.get("language") if profile.get("language") in ("zh_CN", "en_US") else "zh_CN",
            "active": True,
        }
        if partner:
            partner.write(partner_values)
            if not partner.shop_api_uuid:
                partner.shop_api_uuid = remote_id
        else:
            partner = Partner.create({"shop_api_uuid": remote_id, **partner_values})

        portal_group = self.env.ref("base.group_portal")
        if user:
            user.write({
                "login": canonical_login,
                "partner_id": partner.id,
                "active": True,
                "x_storefront_remote_customer_id": remote_id,
                "group_ids": [Command.set([portal_group.id])],
            })
        else:
            user = Users.with_context(no_reset_password=True).create({
                "login": canonical_login,
                "partner_id": partner.id,
                "active": True,
                "x_storefront_remote_customer_id": remote_id,
                "group_ids": [Command.set([portal_group.id])],
            })

        for address_data in profile.get("addresses") or []:
            address_id = str(address_data.get("id") or "").strip()
            if not address_id:
                continue
            address = Partner.search([("shop_api_uuid", "=", address_id)], limit=1)
            country = self.env["res.country"]
            country_code = str(address_data.get("country") or "").upper()
            if country_code:
                country = self.env["res.country"].sudo().search([
                    ("code", "=", country_code),
                ], limit=1)
            values = {
                "parent_id": partner.commercial_partner_id.id,
                "type": address_data.get("type") if address_data.get("type") in ("delivery", "invoice", "contact") else "delivery",
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
                address.write(values)
            else:
                Partner.create({"shop_api_uuid": address_id, **values})
        return user
