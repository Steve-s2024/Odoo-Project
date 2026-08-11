import uuid

from odoo import _
from odoo.exceptions import UserError
from odoo.http import request
from odoo.addons.stock_subwarehouse_hierarchy.controllers.erp_auth import (
    SunErpAuthSignupHome,
    _erp_login_enabled,
)

from ..models.api_client import StorefrontApiError


class StorefrontAuthSignup(SunErpAuthSignupHome):
    """Create public storefront accounts only after authoritative ERP confirmation."""

    def get_auth_signup_config(self):
        config = super().get_auth_signup_config()
        if not _erp_login_enabled():
            config["signup_enabled"] = True
        return config

    def get_auth_signup_qcontext(self):
        qcontext = super().get_auth_signup_qcontext()
        if _erp_login_enabled() or qcontext.get("token"):
            return qcontext
        submitted = str(request.params.get("signup_attempt_id") or "").strip()
        active = str(request.session.get("storefront_signup_attempt_id") or "").strip()
        if submitted and submitted == active:
            attempt_id = submitted
        elif active:
            attempt_id = active
        else:
            attempt_id = str(uuid.uuid4())
            request.session["storefront_signup_attempt_id"] = attempt_id
        qcontext["signup_attempt_id"] = attempt_id
        return qcontext

    def do_signup(self, qcontext, do_login=True):
        if _erp_login_enabled() or qcontext.get("token"):
            return super().do_signup(qcontext, do_login=do_login)

        values = self._prepare_signup_values(qcontext)
        attempt_id = str(qcontext.get("signup_attempt_id") or "").strip()
        if attempt_id != request.session.get("storefront_signup_attempt_id"):
            raise UserError(_("The registration attempt expired. Please submit the form again."))
        try:
            uuid.UUID(attempt_id)
        except (ValueError, TypeError, AttributeError):
            raise UserError(_("The registration attempt is invalid. Please submit the form again.")) from None

        client = request.env["storefront.erp.client"]
        try:
            created = client.post(
                "/api/v1/customers/register",
                {
                    "name": values["name"],
                    "login": values["login"],
                    "email": values["login"],
                    "password": values["password"],
                    "language": values.get("lang") or "zh_CN",
                },
                idempotency_key=f"storefront-signup-{attempt_id}",
                timeout_seconds=30,
            )
            remote_id = str(created.get("id") or "") if isinstance(created, dict) else ""
            if (
                not remote_id
                or created.get("authoritative") is not True
                or created.get("registered") is not True
                or str(created.get("login") or "").casefold() != values["login"].casefold()
            ):
                raise StorefrontApiError(
                    "ERP registration confirmation was invalid.",
                    code="registration_confirmation_invalid",
                    status=502,
                )
            confirmed = client.get(f"/api/v1/customers/{remote_id}")
            if (
                not isinstance(confirmed, dict)
                or confirmed.get("authoritative") is not True
                or str(confirmed.get("id") or "") != remote_id
                or str(confirmed.get("email") or "").casefold() != values["login"].casefold()
            ):
                raise StorefrontApiError(
                    "ERP registration readback did not match.",
                    code="registration_readback_mismatch",
                    status=502,
                )
        except StorefrontApiError as error:
            if error.code == "account_exists":
                raise UserError(_("Another user is already registered using this email address.")) from None
            raise UserError(_(
                "ERP could not confirm account creation. Please try again or contact customer support."
            )) from None

        profile = {
            **confirmed,
            "login": created["login"],
            "language": values.get("lang") or confirmed.get("language") or "zh_CN",
        }
        request.env["res.users"]._storefront_provision_portal_user(profile)
        if do_login:
            request.session.authenticate(request.env, {
                "login": values["login"],
                "password": values["password"],
                "type": "password",
            })
        request.session.pop("storefront_signup_attempt_id", None)
        request.env.cr.commit()
