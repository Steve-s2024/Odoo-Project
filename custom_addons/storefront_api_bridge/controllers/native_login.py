import ipaddress
import logging
import math

import odoo

from odoo import _, http
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.web.controllers.home import (
    CREDENTIAL_PARAMS,
    SIGN_UP_REQUEST_PARAMS,
)
from odoo.addons.web.controllers.utils import ensure_db
from odoo.exceptions import AccessDenied
from odoo.http import request

from ..models.api_client import StorefrontApiError


_logger = logging.getLogger(__name__)


class StorefrontNativeLogin(AuthSignupHome):
    """Use Odoo's native login page with ERP-authoritative verification.

    The GET and HTML are Odoo's own implementation.  On POST the submitted
    credential is verified once by ERP's native credential backend.  A local
    Odoo session is finalized only after that authoritative response.  There
    is no local-password fallback and no password/hash synchronization.
    """

    @staticmethod
    def _prepare_public_environment():
        if request.env.uid is None:
            if request.session.uid is None:
                request.env["ir.http"]._auth_method_public()
            else:
                request.update_env(user=request.session.uid)

    @staticmethod
    def _native_login_response(error=None):
        values = {
            key: value
            for key, value in request.params.items()
            if key in SIGN_UP_REQUEST_PARAMS
        }
        try:
            values["databases"] = http.db_list()
        except odoo.exceptions.AccessDenied:
            values["databases"] = None
        if error:
            values["error"] = error
        if "login" not in values and request.session.get("auth_login"):
            values["login"] = request.session.get("auth_login")
        if not odoo.tools.config["list_db"]:
            values["disable_database_manager"] = True
        response = request.render("web.login", values)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        return response

    @staticmethod
    def _finalize_local_session(user, canonical_login):
        """Finalize a normal Odoo session without storing an ERP password."""
        request.session.uid = None
        request.session["pre_login"] = canonical_login
        request.session["pre_uid"] = user.id
        request.session.finalize(request.env)
        request.update_env(user=user.id, context=request.session.context)

    @staticmethod
    def _cooldown_message(error):
        try:
            remaining_seconds = max(
                1, int(error.details.get("retry_after_seconds") or 3600)
            )
        except (TypeError, ValueError):
            remaining_seconds = 3600
        remaining_minutes = max(1, math.ceil(remaining_seconds / 60))
        if str(request.env.lang or "").startswith("zh"):
            return (
                f"登录失败次数过多，账户已暂停登录。请在约 {remaining_minutes} 分钟后重试。"
                "密码重置仍可使用。"
            )
        return (
            "Too many failed login attempts. "
            f"Please try again in about {remaining_minutes} minutes. "
            "Password reset remains available."
        )

    @staticmethod
    def _public_request_ip():
        """Return only the public peer address established by the Shop proxy.

        The public Shop's Odoo listener is bound to loopback and Nginx supplies
        the final trusted X-Forwarded-For hop.  Refuse loopback, private,
        link-local, reserved, malformed, or absent values so a proxy address
        such as 127.0.0.1 can never become a firewall target.
        """
        raw_address = str(
            getattr(request.httprequest, "remote_addr", "") or ""
        ).strip()
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            return False
        if not address.is_global:
            return False
        return address.compressed

    @classmethod
    def _record_failed_admin_login(cls, submitted_login):
        """Emit a credential-free, Fail2ban-safe event for public admin failures."""
        if str(submitted_login or "").strip().casefold() != "admin":
            return False
        source_ip = cls._public_request_ip()
        if not source_ip:
            return False
        _logger.warning(
            "STORE_SECURITY_ADMIN_LOGIN_REJECTED source_ip=%s", source_ip
        )
        return True

    @http.route()
    def web_login(self, redirect=None, **kw):
        if request.httprequest.method != "POST":
            return super().web_login(redirect=redirect, **kw)

        ensure_db()
        request.params["login_success"] = False
        self._prepare_public_environment()
        credential = {
            key: value
            for key, value in request.params.items()
            if key in CREDENTIAL_PARAMS and value
        }
        credential.setdefault("type", "password")
        login = str(credential.get("login") or "").strip()
        password = credential.get("password") or ""
        if not login or not password:
            return self._native_login_response(_("Wrong login/password"))

        try:
            if request.env["res.users"]._should_captcha_login(credential):
                request.env["ir.http"]._verify_request_recaptcha_token("login")
            profile = request.env["storefront.erp.client"].post(
                "/api/v2/native-auth/login",
                {"login": login, "password": password},
                timeout_seconds=15,
            )
            user = request.env["res.users"]._storefront_provision_native_user(
                profile
            )
            canonical_login = str(profile.get("login") or login).strip()
            self._finalize_local_session(user, canonical_login)
            user._update_last_login()
            request.params["login_success"] = True
            return request.redirect(
                self._login_redirect(user.id, redirect=redirect)
            )
        except StorefrontApiError as error:
            if error.code == "mfa_required":
                message = _(
                    "This account requires two-factor authentication and "
                    "cannot sign in to the Shop yet."
                )
            elif error.code == "invalid_credentials":
                message = _("Wrong login/password")
            elif error.code == "login_cooldown":
                message = self._cooldown_message(error)
            else:
                message = _(
                    "The account service is temporarily unavailable. "
                    "Please try again."
                )
            if error.code in ("invalid_credentials", "login_cooldown"):
                self._record_failed_admin_login(login)
        except AccessDenied:
            message = _("Wrong login/password")
        return self._native_login_response(message)
