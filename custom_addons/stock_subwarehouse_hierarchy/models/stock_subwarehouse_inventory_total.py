from collections import defaultdict

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command


class StockSubwarehouseInventoryTotal(models.TransientModel):
    _name = "stock.subwarehouse.inventory.total"
    _description = "下级库存产品汇总"
    _order = "product_id"

    product_id = fields.Many2one("product.product", string="产品", required=True, readonly=True)
    product_uom_id = fields.Many2one(related="product_id.uom_id", string="计量单位", readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="仓库", readonly=True)
    root_location_id = fields.Many2one("stock.location", string="根库位", required=True, readonly=True)
    quantity = fields.Float(string="现有数量", readonly=True)
    reserved_quantity = fields.Float(string="已预留数量", readonly=True)
    available_quantity = fields.Float(string="可用数量", readonly=True)

    def action_transfer_selected_out_of_current_warehouse(self):
        if not self:
            raise UserError(_("请至少选择一个要转移的产品汇总项。"))

        root_locations = self.mapped("root_location_id")
        if len(root_locations) != 1:
            raise UserError(_("请选择同一个下级库存页面中的产品。"))
        root_location = root_locations[0]
        warehouse = self[:1].warehouse_id or self.env["stock.warehouse"].search([
            ("view_location_id", "parent_of", root_location.id),
        ], limit=1)
        picking_type = self._get_internal_transfer_picking_type(warehouse, root_location)
        destination = (
            picking_type.default_location_dest_id
            or warehouse.lot_stock_id
            or self.env.company.internal_transit_location_id
            or root_location
        )

        quants = self.env["stock.quant"].search([
            ("product_id", "in", self.mapped("product_id").ids),
            ("location_id", "child_of", root_location.id),
            ("location_id.usage", "=", "internal"),
        ]).filtered(lambda quant: quant.product_uom_id.compare(quant.available_quantity, 0) > 0)
        if not quants:
            raise UserError(_("所选产品没有可转移的可用数量。"))

        quantity_by_key = defaultdict(float)
        for quant in quants:
            key = (quant.product_id, quant.location_id)
            quantity_by_key[key] += quant.available_quantity

        first_source_location = next(iter(quantity_by_key))[1]
        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": first_source_location.id,
            "location_dest_id": destination.id,
            "company_id": (warehouse.company_id or self.env.company).id,
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

    def _get_internal_transfer_picking_type(self, warehouse, root_location):
        if warehouse and warehouse.int_type_id:
            return warehouse.int_type_id
        picking_type = self.env["stock.picking.type"].search([
            ("code", "=", "internal"),
            "|",
            ("company_id", "=", root_location.company_id.id),
            ("company_id", "=", False),
        ], limit=1)
        if not picking_type:
            raise UserError(_("尚未配置内部调拨作业类型。"))
        return picking_type
