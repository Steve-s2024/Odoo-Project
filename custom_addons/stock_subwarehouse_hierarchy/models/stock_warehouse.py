from odoo import _, fields, models


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    x_subwarehouse_ids = fields.Many2many(
        "stock.location",
        string="子仓库",
        compute="_compute_x_subwarehouse_ids",
    )
    x_subwarehouse_count = fields.Integer(
        string="子仓库数量",
        compute="_compute_x_subwarehouse_ids",
    )

    def _compute_x_subwarehouse_ids(self):
        Location = self.env["stock.location"].with_context(active_test=False)
        for warehouse in self:
            if not warehouse.view_location_id:
                warehouse.x_subwarehouse_ids = False
                warehouse.x_subwarehouse_count = 0
                continue
            subwarehouses = Location.search([
                ("id", "child_of", warehouse.view_location_id.id),
                ("id", "!=", warehouse.view_location_id.id),
                ("usage", "in", ("view", "internal")),
            ])
            warehouse.x_subwarehouse_ids = subwarehouses
            warehouse.x_subwarehouse_count = len(subwarehouses)

    def action_open_subwarehouse_structure(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("仓库结构"),
            "res_model": "stock.location",
            "view_mode": "list,form",
            "domain": [
                ("id", "child_of", self.view_location_id.id),
                ("id", "!=", self.view_location_id.id),
                ("usage", "in", ("view", "internal")),
            ],
            "context": {
                "default_company_id": self.company_id.id,
                "default_location_id": self.view_location_id.id,
                "default_usage": "view",
                "search_default_group_by_parent_location": 1,
            },
        }

    def _get_descendant_inventory_totals(self):
        """Return product inventory totals for descendant internal locations."""
        self.ensure_one()
        if not self.view_location_id:
            return {}

        groups = self.env["stock.quant"]._read_group(
            [
                ("location_id", "child_of", self.view_location_id.id),
                ("location_id", "!=", self.view_location_id.id),
                ("location_id.usage", "=", "internal"),
            ],
            ["product_id"],
            ["quantity:sum", "reserved_quantity:sum"],
        )
        return {
            product.id: {
                "product": product,
                "quantity": quantity,
                "reserved_quantity": reserved_quantity,
                "available_quantity": quantity - reserved_quantity,
            }
            for product, quantity, reserved_quantity in groups
        }

    def action_view_descendant_inventory_totals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("下级库存汇总"),
            "res_model": "stock.quant",
            "view_mode": "list,pivot,graph,form",
            "views": [
                (self.env.ref("stock.view_stock_quant_tree").id, "list"),
                (self.env.ref("stock.view_stock_quant_pivot").id, "pivot"),
                (self.env.ref("stock.stock_quant_view_graph").id, "graph"),
                (self.env.ref("stock.view_stock_quant_form").id, "form"),
            ],
            "domain": [
                ("location_id", "child_of", self.view_location_id.id),
                ("location_id", "!=", self.view_location_id.id),
                ("location_id.usage", "=", "internal"),
            ],
            "context": {
                "search_default_productgroup": 1,
                "search_default_locationgroup": 1,
                "default_location_id": self.lot_stock_id.id,
                "descendant_inventory_warehouse_id": self.id,
                "descendant_inventory_root_location_id": self.view_location_id.id,
                "inventory_mode": True,
                "readonly_form": True,
            },
        }

    def action_view_descendant_inventory_product_totals(self):
        self.ensure_one()
        totals = self._get_descendant_inventory_totals()
        total_records = self.env["stock.subwarehouse.inventory.total"].create([
            {
                "warehouse_id": self.id,
                "root_location_id": self.view_location_id.id,
                "product_id": total["product"].id,
                "quantity": total["quantity"],
                "reserved_quantity": total["reserved_quantity"],
                "available_quantity": total["available_quantity"],
            }
            for total in totals.values()
        ])
        return {
            "type": "ir.actions.act_window",
            "name": _("下级产品汇总"),
            "res_model": "stock.subwarehouse.inventory.total",
            "view_mode": "list,form",
            "views": [
                (self.env.ref("stock_subwarehouse_hierarchy.view_descendant_inventory_total_list").id, "list"),
                (False, "form"),
            ],
            "domain": [("id", "in", total_records.ids)],
            "context": {
                "create": False,
                "delete": False,
                "edit": False,
            },
        }

    def action_create_subwarehouse(self):
        self.ensure_one()
        existing_count = self.env["stock.location"].with_context(active_test=False).search_count([
            ("location_id", "=", self.view_location_id.id),
            ("usage", "in", ("view", "internal")),
        ])
        location = self.env["stock.location"].create({
            "name": _("新建子仓库 %s", existing_count + 1),
            "usage": "view",
            "location_id": self.view_location_id.id,
            "company_id": self.company_id.id,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("子仓库"),
            "res_model": "stock.location",
            "res_id": location.id,
            "view_mode": "form",
            "target": "current",
        }
