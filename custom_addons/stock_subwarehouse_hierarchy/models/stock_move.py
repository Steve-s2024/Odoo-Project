from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _update_reserved_quantity(self, need, location_id, lot_id=None, package_id=None, owner_id=None, strict=True):
        return super()._update_reserved_quantity(
            need,
            location_id,
            lot_id=lot_id,
            package_id=package_id,
            owner_id=owner_id,
            strict=self._should_reserve_from_exact_source_location(location_id, strict),
        )

    def _update_reserved_quantity_vals(self, need, location_id, lot_id=None, package_id=None, owner_id=None, strict=True):
        return super()._update_reserved_quantity_vals(
            need,
            location_id,
            lot_id=lot_id,
            package_id=package_id,
            owner_id=owner_id,
            strict=self._should_reserve_from_exact_source_location(location_id, strict),
        )

    def _get_available_quantity(self, location_id, lot_id=None, package_id=None, owner_id=None, strict=False, allow_negative=False):
        return super()._get_available_quantity(
            location_id,
            lot_id=lot_id,
            package_id=package_id,
            owner_id=owner_id,
            strict=self._should_reserve_from_exact_source_location(location_id, strict),
            allow_negative=allow_negative,
        )

    def _should_reserve_from_exact_source_location(self, location_id, strict):
        if strict:
            return True
        location = location_id.exists()
        if not location or location.should_bypass_reservation():
            return strict
        return location.usage in ("internal", "view")
