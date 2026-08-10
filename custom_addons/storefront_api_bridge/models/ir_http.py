import werkzeug

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    # Database-management routes stay unavailable on the public storefront.
    # The normal Odoo web client and dataset routes are available only to an
    # authenticated internal user because the built-in website editor relies
    # on them. Portal/customer accounts remain storefront-only.
    _always_blocked_exact = {
        "/web/database/selector", "/web/database/manager",
    }
    _always_blocked_prefixes = ("/web/database/",)
    _internal_only_exact = {"/odoo", "/web"}
    _internal_only_prefixes = (
        "/odoo/", "/web/dataset/", "/web/action/",
    )
    # Standard portal deep links read cloned ERP business tables directly.
    # Their storefront replacements use the ERP API and UUID routes.
    _legacy_erp_data_exact = {
        "/my/invoices", "/my/quotes",
    }
    _legacy_erp_data_prefixes = (
        "/my/orders/", "/my/invoices/", "/my/quotes/",
        "/shop/payment/receipt/",
    )

    @classmethod
    def _is_blocked_storefront_path(cls, request_path, internal_user=False):
        normalized_path = request_path.rstrip("/") or "/"
        if (
            normalized_path in cls._always_blocked_exact
            or normalized_path.startswith(cls._always_blocked_prefixes)
        ):
            return True
        if not internal_user and (
            normalized_path in cls._legacy_erp_data_exact
            or normalized_path.startswith(cls._legacy_erp_data_prefixes)
        ):
            return True
        return not internal_user and (
            normalized_path in cls._internal_only_exact
            or normalized_path.startswith(cls._internal_only_prefixes)
        )

    @classmethod
    def _match(cls, path):
        request_path = request.httprequest.path
        internal_user = bool(request.env.user and request.env.user._is_internal())
        if cls._is_blocked_storefront_path(request_path, internal_user=internal_user):
            # Abort with a real response at routing time. Raising NotFound here
            # enters website language preprocessing without a matched route.
            werkzeug.exceptions.abort(request.redirect("/", code=302))
        return super()._match(path)
