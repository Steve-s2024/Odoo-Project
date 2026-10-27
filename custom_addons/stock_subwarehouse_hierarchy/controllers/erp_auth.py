import werkzeug

from odoo import http
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.website.controllers.main import Website
from odoo.http import request


ERP_LOGIN_ENABLED_PARAM = "sun.erp_login.enabled"
ERP_DISCUSS_URL = "/odoo/discuss"
ERP_ACCESS_ERROR = "erp_access"
ERP_ACCESS_ERROR_MESSAGE = "此账户无权访问ERP"


def _erp_login_enabled():
    return bool(
        request.db
        and request.env["ir.config_parameter"].sudo().get_param(
            ERP_LOGIN_ENABLED_PARAM
        )
        == "True"
    )


def _is_internal_session_user():
    return bool(
        request.session.uid
        and request.env["res.users"].sudo().browse(request.session.uid)._is_internal()
    )


def _erp_access_denied_redirect(redirect=None):
    request.session.logout(keep_db=True)
    query = {"error": ERP_ACCESS_ERROR}
    if redirect:
        query["redirect"] = redirect
    return request.redirect_query("/web/login", query=query, code=303)


class SunErpWebsite(Website):
    @http.route(multilang=False)
    def index(self, **kw):
        if _erp_login_enabled():
            return request.redirect(ERP_DISCUSS_URL, code=302)
        return super().index(**kw)


class SunErpAuthSignupHome(AuthSignupHome):
    def _login_redirect(self, uid, redirect=None):
        if _erp_login_enabled():
            user = request.env["res.users"].sudo().browse(uid)
            if user._is_internal():
                redirect = redirect or ERP_DISCUSS_URL
        return super()._login_redirect(uid, redirect=redirect)

    @http.route()
    def web_login(self, *args, **kw):
        erp_login_enabled = _erp_login_enabled()
        if (
            erp_login_enabled
            and not kw.get("redirect")
            and not request.params.get("redirect")
        ):
            kw["redirect"] = ERP_DISCUSS_URL
            request.params["redirect"] = ERP_DISCUSS_URL
        response = super().web_login(*args, **kw)
        if not erp_login_enabled:
            return response

        redirect = kw.get("redirect") or request.params.get("redirect")
        if request.session.uid and not _is_internal_session_user():
            return _erp_access_denied_redirect(redirect=redirect)

        qcontext = getattr(response, "qcontext", None)
        if qcontext is not None:
            qcontext["erp_login"] = True
            qcontext["signup_enabled"] = False
            if request.params.get("error") == ERP_ACCESS_ERROR:
                qcontext["error"] = ERP_ACCESS_ERROR_MESSAGE
        return response

    @http.route()
    def web_auth_signup(self, *args, **kw):
        if _erp_login_enabled() and not request.params.get("token"):
            raise werkzeug.exceptions.NotFound()
        return super().web_auth_signup(*args, **kw)

    @http.route()
    def login_successful_external_user(self, **kwargs):
        if _erp_login_enabled() and not _is_internal_session_user():
            return _erp_access_denied_redirect(redirect=ERP_DISCUSS_URL)
        return super().login_successful_external_user(**kwargs)
