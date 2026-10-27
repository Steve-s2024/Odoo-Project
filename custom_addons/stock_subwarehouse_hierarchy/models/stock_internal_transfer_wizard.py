from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command


class StockSubwarehouseInternalTransferWizard(models.TransientModel):
    _name = "stock.subwarehouse.internal.transfer.wizard"
    _description = "下级库存内部调拨"

    quant_ids = fields.Many2many(
        "stock.quant",
        string="已选库存",
        readonly=True,
        required=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="公司",
        required=True,
        readonly=True,
    )
    source_root_location_id = fields.Many2one(
        "stock.location",
        string="来源库存范围",
        required=True,
        readonly=True,
    )
    source_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="来源仓库",
        readonly=True,
    )
    destination_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="目的仓库",
        required=True,
        check_company=True,
        domain="[('company_id', '=', company_id)]",
    )
    destination_location_id = fields.Many2one(
        related="destination_warehouse_id.lot_stock_id",
        string="目的库位",
        readonly=True,
    )
    selected_inventory_count = fields.Integer(
        string="已选库存行",
        compute="_compute_selection_counts",
    )
    selected_product_count = fields.Integer(
        string="已选产品",
        compute="_compute_selection_counts",
    )

    @api.depends("quant_ids", "quant_ids.product_id")
    def _compute_selection_counts(self):
        for wizard in self:
            wizard.selected_inventory_count = len(wizard.quant_ids)
            wizard.selected_product_count = len(wizard.quant_ids.product_id)

    @api.model
    def action_open_for_quants(self, quants, source_root_location, source_warehouse=False):
        quants = quants.exists().filtered(
            lambda quant: quant.location_id.usage == "internal"
            and quant.product_uom_id.compare(quant.available_quantity, 0) > 0
        )
        source_root_location = source_root_location.exists()
        if not source_root_location:
            raise UserError(_("无法确定下级库存的来源范围。"))
        quants = quants.filtered(
            lambda quant: quant.location_id._child_of(source_root_location)
        )
        if not quants:
            raise UserError(_("请选择当前下级库存中有可用数量的库存行。"))

        companies = quants.mapped("company_id")
        if len(companies) > 1:
            raise UserError(_("一次内部调拨只能处理同一家公司的库存。"))
        company = companies[:1] or source_root_location.company_id or self.env.company
        source_warehouse = source_warehouse.exists()
        if not source_warehouse:
            source_warehouse = self.env["stock.warehouse"].search([
                ("company_id", "=", company.id),
                ("view_location_id", "parent_of", source_root_location.id),
            ], limit=1)

        destination_warehouse = self.env["stock.warehouse"].search([
            ("company_id", "=", company.id),
            ("id", "!=", source_warehouse.id),
        ], limit=1)
        destination_warehouse = destination_warehouse or source_warehouse
        wizard = self.create({
            "quant_ids": [Command.set(quants.ids)],
            "company_id": company.id,
            "source_root_location_id": source_root_location.id,
            "source_warehouse_id": source_warehouse.id,
            "destination_warehouse_id": destination_warehouse.id,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("内部调拨"),
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(
                self.env.ref(
                    "stock_subwarehouse_hierarchy.view_stock_internal_transfer_wizard_form"
                ).id,
                "form",
            )],
            "target": "new",
        }

    def action_create_internal_transfer(self):
        self.ensure_one()
        destination = self.destination_location_id.exists()
        if not destination or destination.usage != "internal":
            raise UserError(_("所选目的仓库没有可用的内部库存库位。"))
        if self.destination_warehouse_id.company_id != self.company_id:
            raise UserError(_("目的仓库必须与来源库存属于同一家公司。"))

        quants = self.quant_ids.exists().filtered(
            lambda quant: quant.company_id == self.company_id
            and quant.location_id.usage == "internal"
            and quant.location_id._child_of(self.source_root_location_id)
            and quant.location_id != destination
            and quant.product_uom_id.compare(quant.available_quantity, 0) > 0
        )
        if not quants:
            raise UserError(_("所选库存没有可转移数量，或库存已经位于目的仓库。"))

        quantity_by_key = defaultdict(float)
        for quant in quants:
            quantity_by_key[(quant.product_id, quant.location_id)] += quant.available_quantity

        picking_type = quants._get_descendant_inventory_internal_transfer_type(
            self.source_warehouse_id,
            quants,
        )
        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": quants[:1].location_id.id,
            "location_dest_id": destination.id,
            "company_id": self.company_id.id,
            "origin": _("下级库存内部调拨"),
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
