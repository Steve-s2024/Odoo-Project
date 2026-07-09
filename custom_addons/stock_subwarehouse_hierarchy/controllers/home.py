from odoo import http
from odoo.http import request

from odoo.addons.web.controllers.home import Home


class ChineseDefaultHome(Home):
    @http.route("/web/login", type="http", auth="none", readonly=False, list_as_website_content="登录")
    def web_login(self, redirect=None, **kw):
        if request.session:
            request.session.context = dict(request.session.context or {}, lang="zh_CN")
        request.update_context(lang="zh_CN")
        return super().web_login(redirect=redirect, **kw)
