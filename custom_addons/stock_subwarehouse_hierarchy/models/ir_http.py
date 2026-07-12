import werkzeug

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _match(cls, path):
        if request.httprequest.method != "POST":
            request.is_frontend_multilang = False
        if path.rstrip("/") == "/shop" and request.httprequest.method != "POST":
            werkzeug.exceptions.abort(request.redirect("/product-categories"))
        routing = super()._match(path)
        shop_lang = request.cookies.get("stock_shop_lang")
        if shop_lang:
            lang = request.env["res.lang"]._get_data(code=shop_lang)
            if lang:
                request.lang = lang
                request.update_context(lang=lang.code)
        return routing
