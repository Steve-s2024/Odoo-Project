from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        self._check_exact_source_location_stock()
        return super().button_validate()

    def _check_exact_source_location_stock(self):
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        Quant = self.env["stock.quant"]
        shortages = []
        for picking in self:
            for move in picking.move_ids.filtered(
                lambda stock_move: stock_move.state not in ("done", "cancel")
                and stock_move.product_id
                and stock_move.product_id.is_storable
                and stock_move.location_id
                and stock_move.location_id.usage in ("internal", "view")
                and not stock_move.location_id.should_bypass_reservation()
            ):
                source_location = move.location_id
                product = move.product_id
                requested_qty = move.product_uom._compute_quantity(
                    move.product_uom_qty,
                    product.uom_id,
                )
                if float_compare(requested_qty, 0.0, precision_digits=precision) <= 0:
                    continue

                wrong_source_lines = move.move_line_ids.filtered(
                    lambda line: line.location_id and line.location_id != source_location
                )
                if wrong_source_lines:
                    wrong_locations = ", ".join(wrong_source_lines.mapped("location_id.display_name"))
                    shortages.append(_(
                        "%(product)s：调拨来源为 %(source)s，但明细正在使用 %(locations)s。请从实际库存所在仓库/子仓库发货。",
                        product=product.display_name,
                        source=source_location.display_name,
                        locations=wrong_locations,
                    ))
                    continue

                current_move_reserved_qty = sum(
                    line.product_uom_id._compute_quantity(line.quantity, product.uom_id)
                    for line in move.move_line_ids
                    if line.location_id == source_location
                )
                available_qty = Quant._get_available_quantity(product, source_location, strict=True)
                if float_compare(
                    requested_qty,
                    available_qty + current_move_reserved_qty,
                    precision_digits=precision,
                ) > 0:
                    shortages.append(_(
                        "%(product)s 来自 %(source)s：需要 %(requested)s %(uom)s，可用 %(available)s %(uom)s。不能使用下级仓库库存。",
                        product=product.display_name,
                        source=source_location.display_name,
                        requested=requested_qty,
                        available=available_qty + current_move_reserved_qty,
                        uom=product.uom_id.display_name,
                    ))

        if shortages:
            raise UserError(_("当前调拨的来源库存不足：\n%s") % "\n".join(shortages))
