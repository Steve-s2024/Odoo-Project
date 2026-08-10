import re

from odoo import models
from odoo.http import request

from .api_client import StorefrontApiError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def _storefront_stable_group_key(self):
        """Group variants by product-code family, independent of translations."""
        self.ensure_one()
        code = (self.default_code or "").strip().strip("[]")
        parts = [part.strip().upper() for part in code.split("-") if part.strip()]
        if len(parts) >= 2 and re.match(r"^\d{6}[A-Z0-9]+$", parts[0]):
            return "code:%s-%s" % (parts[0], parts[1])
        return "name:%s" % super()._normalize_shop_group_name()

    def _normalize_shop_group_name(self):
        self.ensure_one()
        return self._storefront_stable_group_key()

    def _get_shop_group_siblings(self):
        self.ensure_one()
        key = self._storefront_stable_group_key()
        products = self.sudo().search([
            ("sale_ok", "=", True),
            ("website_published", "=", True),
        ], order="default_code, id")
        return products.filtered(lambda product: product._storefront_stable_group_key() == key)

    def _get_all_shop_group_siblings(self):
        self.ensure_one()
        key = self._storefront_stable_group_key()
        return self.sudo().search([], order="default_code, id").filtered(
            lambda product: product._storefront_stable_group_key() == key
        )

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
