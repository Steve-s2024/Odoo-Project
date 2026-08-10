from odoo import models
from odoo.http import request

from .api_client import StorefrontApiError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def _storefront_inventory_map(self):
        if not request:
            return {}
        cache = getattr(request, "_storefront_erp_inventory_snapshot", None)
        if cache is not None:
            return cache
        try:
            cache = self.env["storefront.erp.client"].inventory_snapshot()
        except StorefrontApiError:
            cache = {}
        request._storefront_erp_inventory_snapshot = cache
        return cache

    def _get_shop_available_quantity(self):
        self.ensure_one()
        inventory = self._storefront_inventory_map()
        remote = inventory.get(self.shop_api_uuid)
        if remote is None and self.product_variant_id.shop_api_uuid:
            remote = inventory.get(self.product_variant_id.shop_api_uuid)
        if remote is not None:
            return float(remote.get("available_quantity") or 0.0)
        return super()._get_shop_available_quantity()

    def _is_shop_available(self):
        self.ensure_one()
        inventory = self._storefront_inventory_map()
        remote = inventory.get(self.shop_api_uuid)
        if remote is None and self.product_variant_id.shop_api_uuid:
            remote = inventory.get(self.product_variant_id.shop_api_uuid)
        if remote is not None:
            return bool(remote.get("available"))
        return super()._is_shop_available()
