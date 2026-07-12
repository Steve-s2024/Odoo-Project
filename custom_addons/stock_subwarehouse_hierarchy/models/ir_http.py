import werkzeug

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _match(cls, path):
        if path.rstrip("/") == "/shop" and request.httprequest.method != "POST":
            werkzeug.exceptions.abort(request.redirect("/product-categories"))
        return super()._match(path)
