from odoo import _, api, fields, models
from odoo.tools import float_compare


class StockLocation(models.Model):
    _inherit = "stock.location"

    x_is_subwarehouse = fields.Boolean(
        string="子仓库",
        help="将此库位标记为受管理的子仓库节点。",
    )
    x_subwarehouse_code = fields.Char(
        string="子仓库编码",
        copy=False,
        index=True,
    )
    x_responsible_id = fields.Many2one(
        "res.users",
        string="负责人",
        check_company=True,
    )
    x_structure_note = fields.Text(string="结构备注")
    x_child_location_count = fields.Integer(
        string="子库位",
        compute="_compute_x_child_location_count",
    )

    _subwarehouse_code_company_uniq = models.Constraint(
        "unique(x_subwarehouse_code, company_id)",
        "每家公司内的子仓库编码必须唯一。",
    )

    @api.depends("child_ids")
    def _compute_x_child_location_count(self):
        for location in self:
            location.x_child_location_count = len(location.child_ids)

    @api.model
    def name_search(self, name="", domain=None, operator="ilike", limit=100, **kwargs):
        if domain is None and "args" in kwargs:
            domain = kwargs.pop("args")
        source_domain = self._get_sale_source_location_name_search_domain()
        if source_domain is not None:
            domain = list(domain or []) + source_domain
        return super().name_search(name=name, domain=domain, operator=operator, limit=limit)

    @api.model
    def _get_sale_source_location_name_search_domain(self):
        if not self.env.context.get("sale_source_inventory_filter"):
            return None

        product_id = self._get_context_record_id("sale_source_product_id")
        product_uom_id = self._get_context_record_id("sale_source_product_uom_id")
        company_id = self._get_context_record_id("sale_source_company_id")
        quantity = self._get_context_float("sale_source_product_uom_qty")
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        if not product_id or float_compare(quantity, 0.0, precision_digits=precision) <= 0:
            return [("id", "=", 0)]

        product = self.env["product.product"].browse(product_id).exists()
        if not product:
            return [("id", "=", 0)]

        required_qty = quantity
        product_uom = self.env["uom.uom"].browse(product_uom_id).exists() if product_uom_id else product.uom_id
        if product_uom and product_uom != product.uom_id:
            required_qty = product_uom._compute_quantity(quantity, product.uom_id)

        candidates_domain = [
            ("usage", "in", ["view", "internal"]),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", company_id or self.env.company.id),
        ]
        warehouse_roots = self.env["stock.warehouse"].search([
            "|",
            ("company_id", "=", False),
            ("company_id", "=", company_id or self.env.company.id),
        ]).mapped("view_location_id")
        if warehouse_roots:
            candidates_domain.append(("id", "child_of", warehouse_roots.ids))

        Quant = self.env["stock.quant"]
        eligible_ids = []
        for location in self.search(candidates_domain):
            available_qty = Quant._get_available_quantity(product, location)
            if float_compare(available_qty, required_qty, precision_digits=precision) >= 0:
                eligible_ids.append(location.id)
        return [("id", "in", eligible_ids)] if eligible_ids else [("id", "=", 0)]

    @api.model
    def _get_context_record_id(self, key):
        value = self.env.context.get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else False
        return value or False

    @api.model
    def _get_context_float(self, key):
        try:
            return float(self.env.context.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

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
                ("location_id", "child_of", self.id),
                ("location_id.usage", "=", "internal"),
            ],
            "context": {
                "search_default_productgroup": 1,
                "search_default_locationgroup": 1,
                "default_location_id": self.id,
                "descendant_inventory_root_location_id": self.id,
                "inventory_mode": True,
                "readonly_form": True,
            },
        }

    def action_view_descendant_inventory_product_totals(self):
        self.ensure_one()
        groups = self.env["stock.quant"]._read_group(
            [
                ("location_id", "child_of", self.id),
                ("location_id.usage", "=", "internal"),
            ],
            ["product_id"],
            ["quantity:sum", "reserved_quantity:sum"],
        )
        total_records = self.env["stock.subwarehouse.inventory.total"].create([
            {
                "root_location_id": self.id,
                "product_id": product.id,
                "quantity": quantity,
                "reserved_quantity": reserved_quantity,
                "available_quantity": quantity - reserved_quantity,
            }
            for product, quantity, reserved_quantity in groups
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

    def action_view_inventory_in_out(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("库存出入历史"),
            "res_model": "stock.move",
            "view_mode": "list,form",
            "views": [
                (self.env.ref("stock.view_move_tree").id, "list"),
                (self.env.ref("stock.view_move_form").id, "form"),
            ],
            "domain": [
                ("state", "=", "done"),
                "|",
                ("location_id", "child_of", self.id),
                ("location_dest_id", "child_of", self.id),
            ],
            "context": {
                "search_default_by_product": 1,
                "search_default_groupby_location_id": 1,
                "search_default_groupby_dest_location_id": 1,
                "default_location_id": self.id,
                "create": False,
            },
        }

    def _get_internal_transfer_picking_type(self):
        self.ensure_one()
        warehouse = self.warehouse_id
        if not warehouse:
            warehouses = self.env["stock.warehouse"].search([
                "|",
                ("company_id", "=", self.company_id.id),
                ("company_id", "=", False),
            ])
            warehouse = warehouses.filtered(lambda wh: self._child_of(wh.view_location_id))[:1]
        if warehouse and warehouse.int_type_id:
            return warehouse.int_type_id
        return self.env["stock.picking.type"].search([
            ("code", "=", "internal"),
            "|",
            ("company_id", "=", self.company_id.id),
            ("company_id", "=", False),
        ], limit=1)

    def action_create_internal_transfer(self):
        self.ensure_one()
        picking_type = self._get_internal_transfer_picking_type()
        context = {
            "contact_display": "partner_address",
            "restricted_picking_type_code": "internal",
            "default_picking_type_id": picking_type.id,
            "default_location_id": self.id,
            "default_location_dest_id": self.id,
            "default_company_id": self.company_id.id or self.env.company.id,
        }
        return {
            "type": "ir.actions.act_window",
            "name": _("内部调拨"),
            "res_model": "stock.picking",
            "view_mode": "form",
            "views": [(self.env.ref("stock.view_picking_form").id, "form")],
            "target": "current",
            "context": context,
        }

    def action_load_remove_inventory(self):
        self.ensure_one()
        action = self.env["stock.quant"].with_context(
            inventory_mode=True,
            default_location_id=self.id,
            search_default_location_id=self.id,
            always_show_loc=1,
        ).action_view_inventory()
        action["name"] = _("装入/移除产品")
        action["domain"] = [
            ("location_id", "child_of", self.id),
            ("location_id.usage", "in", ["internal", "transit"]),
        ]
        action["context"] = {
            **action.get("context", {}),
            "inventory_mode": True,
            "default_location_id": self.id,
            "search_default_location_id": self.id,
            "always_show_loc": 1,
        }
        return action

    def _get_manufacturing_picking_type(self):
        self.ensure_one()
        warehouse = self.warehouse_id
        if not warehouse:
            warehouses = self.env["stock.warehouse"].search([
                "|",
                ("company_id", "=", self.company_id.id),
                ("company_id", "=", False),
            ])
            warehouse = warehouses.filtered(lambda wh: self._child_of(wh.view_location_id))[:1]
        if warehouse and warehouse.manu_type_id:
            return warehouse.manu_type_id
        return self.env["stock.picking.type"].search([
            ("code", "=", "mrp_operation"),
            "|",
            ("company_id", "=", self.company_id.id),
            ("company_id", "=", False),
        ], limit=1)

    def _get_manufacturing_stock_location(self):
        self.ensure_one()
        if self.usage == "internal":
            return self
        child_location = self.env["stock.location"].search([
            ("id", "child_of", self.id),
            ("usage", "=", "internal"),
        ], limit=1)
        if child_location:
            return child_location
        if self.usage == "view":
            return self.env["stock.location"].create({
                "name": _("Stock"),
                "usage": "internal",
                "location_id": self.id,
                "company_id": self.company_id.id,
                "x_is_subwarehouse": True,
            })
        warehouse = self.warehouse_id or self.env["stock.warehouse"].search([
            "|",
            ("company_id", "=", self.company_id.id),
            ("company_id", "=", False),
        ], limit=1)
        return warehouse.lot_stock_id or self

    def action_manufacture_product(self):
        self.ensure_one()
        stock_location = self._get_manufacturing_stock_location()
        picking_type = self._get_manufacturing_picking_type()
        return {
            "type": "ir.actions.act_window",
            "name": _("制造产品"),
            "res_model": "mrp.production",
            "view_mode": "form",
            "views": [(self.env.ref("mrp.mrp_production_form_view").id, "form")],
            "target": "current",
            "context": {
                "subwarehouse_manufacturing_location_id": stock_location.id,
                "default_picking_type_id": picking_type.id,
                "default_location_src_id": stock_location.id,
                "default_location_dest_id": stock_location.id,
                "default_company_id": self.company_id.id or self.env.company.id,
            },
        }

    def action_import_manufacturing_sheet(self):
        self.ensure_one()
        stock_location = self._get_manufacturing_stock_location()
        return {
            "type": "ir.actions.client",
            "name": _("导入制造单"),
            "tag": "import",
            "target": "current",
            "params": {
                "model": "mrp.production",
                "active_model": "mrp.production",
                "context": {
                    "subwarehouse_manufacturing_location_id": stock_location.id,
                    "default_picking_type_id": self._get_manufacturing_picking_type().id,
                    "default_location_src_id": stock_location.id,
                    "default_location_dest_id": stock_location.id,
                    "default_company_id": self.company_id.id or self.env.company.id,
                },
            },
            "context": {
                "subwarehouse_manufacturing_location_id": stock_location.id,
                "default_picking_type_id": self._get_manufacturing_picking_type().id,
                "default_location_src_id": stock_location.id,
                "default_location_dest_id": stock_location.id,
                "default_company_id": self.company_id.id or self.env.company.id,
            },
        }

    def action_view_manufacturing_history(self):
        self.ensure_one()
        stock_location = self._get_manufacturing_stock_location()
        return {
            "type": "ir.actions.act_window",
            "name": _("制造历史"),
            "res_model": "mrp.production",
            "view_mode": "list,form,kanban,calendar,pivot,graph",
            "domain": [
                "|",
                ("location_src_id", "child_of", stock_location.id),
                ("location_dest_id", "child_of", stock_location.id),
            ],
            "context": {
                "search_default_filter_done": 1,
                "default_location_src_id": stock_location.id,
                "default_location_dest_id": stock_location.id,
                "subwarehouse_manufacturing_location_id": stock_location.id,
                "default_company_id": self.company_id.id or self.env.company.id,
            },
        }
