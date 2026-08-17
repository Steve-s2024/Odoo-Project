import uuid
from urllib.parse import urlencode

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request
from odoo.addons.stock_subwarehouse_hierarchy.controllers.erp_auth import (
    SunErpAuthSignupHome,
    _erp_login_enabled,
)

from ..models.api_client import StorefrontApiError


class StorefrontAuthSignup(SunErpAuthSignupHome):
    """Create public storefront accounts only after authoritative ERP confirmation."""

    @staticmethod
    def _normalized_registration_values(values, qcontext):
        login = str(
            values.get("login") or qcontext.get("email") or ""
        ).strip().lower()
        password = str(values.get("password") or qcontext.get("password") or "")
        name = str(values.get("name") or qcontext.get("name") or "").strip()
        if not login or "@" not in login or not password:
            raise UserError(_(
                "Please enter a valid email address and password before creating the account."
            ))
        return {
            **values,
            "login": login,
            "password": password,
            "name": name or login.partition("@")[0],
        }

    @staticmethod
    def _rotate_registration_attempt(qcontext):
        attempt_id = str(uuid.uuid4())
        request.session["storefront_signup_attempt_id"] = attempt_id
        qcontext["signup_attempt_id"] = attempt_id

    @staticmethod
    def _new_password_reset_attempt(qcontext=None):
        attempt_id = str(uuid.uuid4())
        request.session["storefront_password_reset_attempt_id"] = attempt_id
        if qcontext is not None:
            qcontext["password_reset_attempt_id"] = attempt_id
        return attempt_id

    def _password_reset_attempt(self, qcontext, *, validate=False):
        submitted = str(
            request.params.get("password_reset_attempt_id") or ""
        ).strip()
        active = str(
            request.session.get("storefront_password_reset_attempt_id") or ""
        ).strip()
        if validate:
            if not submitted or submitted != active:
                self._new_password_reset_attempt(qcontext)
                raise UserError(_(
                    "The password reset request expired. Please submit the form again."
                ))
            try:
                uuid.UUID(submitted)
            except (ValueError, TypeError, AttributeError):
                self._new_password_reset_attempt(qcontext)
                raise UserError(_(
                    "The password reset request is invalid. Please submit the form again."
                )) from None
            qcontext["password_reset_attempt_id"] = submitted
            return submitted
        if active:
            try:
                uuid.UUID(active)
            except (ValueError, TypeError, AttributeError):
                active = ""
        if not active:
            active = self._new_password_reset_attempt()
        qcontext["password_reset_attempt_id"] = active
        return active

    @staticmethod
    def _render_password_reset(qcontext):
        response = request.render("auth_signup.reset_password", qcontext)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        return response

    @staticmethod
    def _request_erp_password_reset(login, attempt_id):
        try:
            confirmation = request.env["storefront.erp.client"].post(
                "/api/v1/customers/password-reset/request",
                {"login": login},
                idempotency_key=f"storefront-password-reset-{attempt_id}",
                timeout_seconds=30,
            )
        except StorefrontApiError:
            raise UserError(_(
                "ERP could not confirm the password reset request. "
                "Please try again or contact customer support."
            )) from None
        if (
            not isinstance(confirmation, dict)
            or confirmation.get("authoritative") is not True
            or confirmation.get("accepted") is not True
        ):
            raise UserError(_(
                "ERP could not confirm the password reset request. "
                "Please try again or contact customer support."
            ))

    @http.route()
    def web_auth_reset_password(self, *args, **kw):
        # The ERP host keeps its native token-completion page. On the separated
        # shop, only the initial request is proxied; ERP owns the token, sends
        # the email, and changes the password without exposing credentials.
        if _erp_login_enabled():
            return super().web_auth_reset_password(*args, **kw)

        # A reset token belongs to ERP, never to the presentation-only Shop
        # user database.  Completing it locally would store a second unrelated
        # hash and the next ERP-authoritative login would still fail.
        if request.params.get("token"):
            query = {
                key: request.params.get(key)
                for key in ("token", "db")
                if request.params.get(key)
            }
            target = request.env["storefront.erp.client"].erp_public_url()
            return request.redirect(
                f"{target}/web/reset_password?{urlencode(query)}",
                code=303,
                local=False,
            )

        qcontext = self.get_auth_signup_qcontext()
        if not qcontext.get("reset_password_enabled"):
            return super().web_auth_reset_password(*args, **kw)

        if request.httprequest.method != "POST":
            self._password_reset_attempt(qcontext)
            return self._render_password_reset(qcontext)

        try:
            attempt_id = self._password_reset_attempt(qcontext, validate=True)
            login = str(qcontext.get("login") or "").strip()
            if not login:
                raise UserError(_("No login provided."))
            self._request_erp_password_reset(login, attempt_id)
            request.session.pop("storefront_password_reset_attempt_id", None)
            qcontext["message"] = _(
                "Password reset instructions sent to your email address."
            )
        except UserError as error:
            qcontext["error"] = error.args[0]
        return self._render_password_reset(qcontext)

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

        values = self._normalized_registration_values(
            self._prepare_signup_values(qcontext), qcontext,
        )
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
            if 400 <= error.status < 500:
                # ERP made a definitive business rejection and did not create
                # an account. A corrected form is a new business attempt; do
                # not replay the completed rejection under the previous key.
                self._rotate_registration_attempt(qcontext)
            if error.code == "account_exists":
                raise UserError(_("Another user is already registered using this email address.")) from None
            if error.code == "invalid_registration":
                raise UserError(_(
                    "Please enter a valid email address and password before creating the account."
                )) from None
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
