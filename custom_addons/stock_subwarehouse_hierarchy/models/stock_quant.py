from collections import defaultdict

from odoo import _, models
from odoo.exceptions import UserError
from odoo.fields import Command


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def action_transfer_selected_out_of_descendant_inventory(self):
        quants = self.filtered(lambda quant: quant.product_uom_id.compare(quant.available_quantity, 0) > 0)
        if not quants:
            raise UserError(_("请选择有可用数量的库存行进行转移。"))

        warehouse = self.env["stock.warehouse"].browse(
            self.env.context.get("descendant_inventory_warehouse_id")
        ).exists()
        root_location = self.env["stock.location"].browse(
            self.env.context.get("descendant_inventory_root_location_id")
        ).exists()
        if root_location:
            quants = quants.filtered(lambda quant: quant.location_id._child_of(root_location))
        if not quants:
            raise UserError(_("所选库存不在当前下级库存范围内。"))

        picking_type = self._get_descendant_inventory_internal_transfer_type(warehouse, quants)
        destination = (
            picking_type.default_location_dest_id
            or (warehouse and warehouse.lot_stock_id)
            or self.env.company.internal_transit_location_id
            or quants[:1].location_id
        )
        quantity_by_key = defaultdict(float)
        for quant in quants:
            quantity_by_key[(quant.product_id, quant.location_id)] += quant.available_quantity

        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": quants[:1].location_id.id,
            "location_dest_id": destination.id,
            "company_id": (warehouse.company_id or quants[:1].company_id or self.env.company).id,
            "origin": _("下级库存转移"),
            "move_ids": [
                Command.create({
                    "description_picking": product.display_name,
                    "product_id": product.id,
                    "product_uom": product.uom_id.id,
                    "product_uom_qty": quantity,
                    "location_id": source_location.id,
                    "location_dest_id": destination.id,
                })
                for (product, source_location), quantity in quantity_by_key.items()
                if product.uom_id.compare(quantity, 0) > 0
            ],
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("内部调拨"),
            "res_model": "stock.picking",
            "res_id": picking.id,
            "view_mode": "form",
            "views": [(self.env.ref("stock.view_picking_form").id, "form")],
            "target": "current",
        }

    def _get_descendant_inventory_internal_transfer_type(self, warehouse, quants):
        if warehouse and warehouse.int_type_id:
            return warehouse.int_type_id
        picking_type = self.env["stock.picking.type"].search([
            ("code", "=", "internal"),
            "|",
            ("company_id", "=", quants[:1].company_id.id),
            ("company_id", "=", False),
        ], limit=1)
        if not picking_type:
            raise UserError(_("尚未配置内部调拨作业类型。"))
        return picking_type
