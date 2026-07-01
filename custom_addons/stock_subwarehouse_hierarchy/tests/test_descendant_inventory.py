from io import BytesIO

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDescendantInventoryTotals(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.StockLocation = cls.env["stock.location"]
        cls.StockQuant = cls.env["stock.quant"]

        cls.warehouse = cls.env["stock.warehouse"].create({
            "name": "Descendant Test Warehouse",
            "code": "DTW",
        })
        cls.product_a = cls.env["product.product"].create({
            "name": "Descendant Product A",
            "is_storable": True,
        })
        cls.product_b = cls.env["product.product"].create({
            "name": "Descendant Product B",
            "is_storable": True,
        })

        cls.subwarehouse = cls.StockLocation.create({
            "name": "Subwarehouse A",
            "usage": "view",
            "location_id": cls.warehouse.view_location_id.id,
        })
        cls.bin_a = cls.StockLocation.create({
            "name": "Subwarehouse A / Bin A",
            "usage": "internal",
            "location_id": cls.subwarehouse.id,
        })
        cls.bin_b = cls.StockLocation.create({
            "name": "Subwarehouse A / Bin B",
            "usage": "internal",
            "location_id": cls.subwarehouse.id,
        })
        cls.supplier_location = cls.env.ref("stock.stock_location_suppliers")
        cls.customer = cls.env["res.partner"].create({
            "name": "Subwarehouse Test Customer",
        })

    def test_descendant_inventory_totals_sum_child_locations(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        self.StockQuant._update_available_quantity(self.product_a, self.bin_b, 7.0)
        self.StockQuant._update_available_quantity(self.product_b, self.bin_b, 3.0)
        self.StockQuant._update_available_quantity(self.product_a, self.supplier_location, 11.0)

        totals = self.warehouse._get_descendant_inventory_totals()

        self.assertEqual(totals[self.product_a.id]["quantity"], 12.0)
        self.assertEqual(totals[self.product_a.id]["available_quantity"], 12.0)
        self.assertEqual(totals[self.product_b.id]["quantity"], 3.0)
        self.assertNotIn(
            self.supplier_location.id,
            self.StockQuant.search([
                ("location_id", "child_of", self.warehouse.view_location_id.id),
                ("location_id.usage", "=", "internal"),
            ]).mapped("location_id").ids,
        )

    def test_descendant_inventory_action_filters_internal_descendants(self):
        action = self.warehouse.action_view_descendant_inventory_totals()

        self.assertEqual(action["res_model"], "stock.quant")
        self.assertEqual(action["view_mode"], "list,pivot,graph,form")
        self.assertIn(("location_id", "child_of", self.warehouse.view_location_id.id), action["domain"])
        self.assertIn(("location_id", "!=", self.warehouse.view_location_id.id), action["domain"])
        self.assertIn(("location_id.usage", "=", "internal"), action["domain"])
        self.assertEqual(action["context"]["search_default_productgroup"], 1)
        self.assertEqual(action["context"]["search_default_locationgroup"], 1)
        self.assertEqual(action["context"]["descendant_inventory_warehouse_id"], self.warehouse.id)
        self.assertEqual(action["context"]["descendant_inventory_root_location_id"], self.warehouse.view_location_id.id)

    def test_descendant_product_totals_action_lists_products(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        self.StockQuant._update_available_quantity(self.product_a, self.bin_b, 7.0)
        self.StockQuant._update_available_quantity(self.product_b, self.bin_b, 3.0)

        action = self.warehouse.action_view_descendant_inventory_product_totals()
        totals = self.env["stock.subwarehouse.inventory.total"].search(action["domain"])
        totals_by_product = {
            total.product_id: total
            for total in totals
        }

        self.assertEqual(action["res_model"], "stock.subwarehouse.inventory.total")
        self.assertEqual(action["views"][0][1], "list")
        self.assertEqual(totals_by_product[self.product_a].quantity, 12.0)
        self.assertEqual(totals_by_product[self.product_a].available_quantity, 12.0)
        self.assertEqual(totals_by_product[self.product_b].quantity, 3.0)

    def test_descendant_product_totals_transfer_selected_creates_internal_transfer(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        self.StockQuant._update_available_quantity(self.product_a, self.bin_b, 7.0)

        action = self.warehouse.action_view_descendant_inventory_product_totals()
        total = self.env["stock.subwarehouse.inventory.total"].search([
            *action["domain"],
            ("product_id", "=", self.product_a.id),
        ])
        transfer_action = total.action_transfer_selected_out_of_current_warehouse()
        picking = self.env["stock.picking"].browse(transfer_action["res_id"])

        self.assertEqual(transfer_action["res_model"], "stock.picking")
        self.assertEqual(picking.picking_type_id, self.warehouse.int_type_id)
        self.assertEqual(sum(picking.move_ids.mapped("product_uom_qty")), 12.0)
        self.assertEqual(set(picking.move_ids.mapped("location_id").ids), set((self.bin_a | self.bin_b).ids))
        self.assertTrue(all(move.product_id == self.product_a for move in picking.move_ids))

    def test_quant_descendant_transfer_button_creates_internal_transfer(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        self.StockQuant._update_available_quantity(self.product_b, self.bin_b, 3.0)
        quants = self.StockQuant.search([
            ("product_id", "in", (self.product_a | self.product_b).ids),
            ("location_id", "in", (self.bin_a | self.bin_b).ids),
        ])

        transfer_action = quants.with_context(
            descendant_inventory_warehouse_id=self.warehouse.id,
            descendant_inventory_root_location_id=self.warehouse.view_location_id.id,
        ).action_transfer_selected_out_of_descendant_inventory()
        picking = self.env["stock.picking"].browse(transfer_action["res_id"])

        self.assertEqual(picking.picking_type_id, self.warehouse.int_type_id)
        self.assertEqual(
            {move.product_id.id: move.product_uom_qty for move in picking.move_ids},
            {self.product_a.id: 5.0, self.product_b.id: 3.0},
        )

    def test_location_descendant_inventory_action_filters_location_subtree(self):
        action = self.subwarehouse.action_view_descendant_inventory_totals()

        self.assertEqual(action["res_model"], "stock.quant")
        self.assertEqual(action["view_mode"], "list,pivot,graph,form")
        self.assertIn(("location_id", "child_of", self.subwarehouse.id), action["domain"])
        self.assertIn(("location_id.usage", "=", "internal"), action["domain"])
        self.assertEqual(action["context"]["search_default_productgroup"], 1)
        self.assertEqual(action["context"]["search_default_locationgroup"], 1)

    def test_location_inventory_in_out_action_filters_source_or_destination(self):
        action = self.subwarehouse.action_view_inventory_in_out()

        self.assertEqual(action["res_model"], "stock.move")
        self.assertEqual(action["name"], "库存出入历史")
        self.assertEqual(action["view_mode"], "list,form")
        self.assertIn(("state", "=", "done"), action["domain"])
        self.assertIn(("location_id", "child_of", self.subwarehouse.id), action["domain"])
        self.assertIn(("location_dest_id", "child_of", self.subwarehouse.id), action["domain"])
        self.assertEqual(action["context"]["search_default_by_product"], 1)
        self.assertEqual(action["context"]["search_default_groupby_location_id"], 1)
        self.assertEqual(action["context"]["search_default_groupby_dest_location_id"], 1)
        self.assertFalse(action["context"]["create"])

    def test_location_internal_transfer_action_prefills_current_location(self):
        action = self.subwarehouse.action_create_internal_transfer()

        self.assertEqual(action["res_model"], "stock.picking")
        self.assertEqual(action["view_mode"], "form")
        self.assertEqual(action["context"]["restricted_picking_type_code"], "internal")
        self.assertEqual(action["context"]["default_location_id"], self.subwarehouse.id)
        self.assertEqual(action["context"]["default_location_dest_id"], self.subwarehouse.id)
        self.assertEqual(
            action["context"]["default_picking_type_id"],
            self.warehouse.int_type_id.id,
        )

    def test_location_load_remove_inventory_action_filters_current_location(self):
        action = self.subwarehouse.action_load_remove_inventory()

        self.assertEqual(action["res_model"], "stock.quant")
        self.assertEqual(action["view_mode"], "list")
        self.assertEqual(action["name"], "装入/移除产品")
        self.assertIn(("location_id", "child_of", self.subwarehouse.id), action["domain"])
        self.assertIn(("location_id.usage", "in", ["internal", "transit"]), action["domain"])
        self.assertTrue(action["context"]["inventory_mode"])
        self.assertEqual(action["context"]["default_location_id"], self.subwarehouse.id)

    def test_location_manufacture_product_action_prefills_internal_location(self):
        action = self.bin_a.action_manufacture_product()

        self.assertEqual(action["res_model"], "mrp.production")
        self.assertEqual(action["view_mode"], "form")
        self.assertEqual(action["context"]["subwarehouse_manufacturing_location_id"], self.bin_a.id)
        self.assertEqual(action["context"]["default_location_src_id"], self.bin_a.id)
        self.assertEqual(action["context"]["default_location_dest_id"], self.bin_a.id)
        self.assertEqual(
            action["context"]["default_picking_type_id"],
            self.warehouse.manu_type_id.id,
        )

    def test_view_location_manufacture_product_action_uses_internal_child(self):
        action = self.subwarehouse.action_manufacture_product()

        self.assertEqual(action["res_model"], "mrp.production")
        self.assertIn(
            action["context"]["default_location_dest_id"],
            (self.bin_a | self.bin_b).ids,
        )

    def test_manufacturing_order_defaults_keep_subwarehouse_location(self):
        action = self.bin_a.action_manufacture_product()
        defaults = self.env["mrp.production"].with_context(
            action["context"],
        ).default_get(["location_src_id", "location_dest_id"])

        self.assertEqual(defaults["location_src_id"], self.bin_a.id)
        self.assertEqual(defaults["location_dest_id"], self.bin_a.id)
        self.assertNotEqual(defaults["location_dest_id"], self.warehouse.lot_stock_id.id)

    def test_manufacturing_order_create_keeps_subwarehouse_location(self):
        action = self.bin_a.action_manufacture_product()
        production = self.env["mrp.production"].with_context(action["context"]).create({
            "product_id": self.product_a.id,
            "product_qty": 1.0,
            "product_uom_id": self.product_a.uom_id.id,
            "picking_type_id": self.warehouse.manu_type_id.id,
        })

        self.assertEqual(production.location_src_id, self.bin_a)
        self.assertEqual(production.location_dest_id, self.bin_a)
        self.assertNotEqual(production.location_dest_id, self.warehouse.lot_stock_id)

    def test_location_import_manufacturing_sheet_action_uses_builtin_import(self):
        action = self.subwarehouse.action_import_manufacturing_sheet()

        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "import")
        self.assertEqual(action["target"], "current")
        self.assertEqual(action["params"]["model"], "mrp.production")
        self.assertEqual(action["params"]["active_model"], "mrp.production")
        self.assertIn(
            action["params"]["context"]["subwarehouse_manufacturing_location_id"],
            (self.bin_a | self.bin_b).ids,
        )
        self.assertEqual(
            action["params"]["context"]["default_location_src_id"],
            action["params"]["context"]["subwarehouse_manufacturing_location_id"],
        )
        self.assertEqual(
            action["params"]["context"]["default_location_dest_id"],
            action["params"]["context"]["subwarehouse_manufacturing_location_id"],
        )

    def test_location_manufacturing_history_action_filters_current_inventory(self):
        action = self.subwarehouse.action_view_manufacturing_history()

        self.assertEqual(action["res_model"], "mrp.production")
        self.assertIn(("location_src_id", "child_of", action["context"]["subwarehouse_manufacturing_location_id"]), action["domain"])
        self.assertIn(("location_dest_id", "child_of", action["context"]["subwarehouse_manufacturing_location_id"]), action["domain"])
        self.assertIn(
            action["context"]["subwarehouse_manufacturing_location_id"],
            (self.bin_a | self.bin_b).ids,
        )

    def test_sale_line_source_inventory_shows_available_quantity(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "order_line": [(0, 0, {
                "product_id": self.product_a.id,
                "product_uom_qty": 3.0,
                "x_source_location_id": self.bin_a.id,
            })],
        })
        line = order.order_line

        self.assertIn(line.x_source_location_id._origin, line.x_eligible_source_location_ids)
        self.assertEqual(line.x_source_available_qty, 5.0)
        self.assertTrue(line.x_source_can_fulfill)

    def test_sale_line_source_inventory_parent_location_shows_child_quantity(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "order_line": [(0, 0, {
                "product_id": self.product_a.id,
                "product_uom_qty": 3.0,
                "x_source_location_id": self.subwarehouse.id,
            })],
        })
        line = order.order_line

        self.assertEqual(line.x_source_location_id, self.subwarehouse)
        self.assertEqual(line.x_source_available_qty, 5.0)
        self.assertTrue(line.x_source_can_fulfill)

    def test_sale_line_source_inventory_options_only_include_locations_that_can_fulfill(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        self.StockQuant._update_available_quantity(self.product_a, self.bin_b, 2.0)
        other_warehouse = self.env["stock.warehouse"].create({
            "name": "Other Source Warehouse",
            "code": "OSW",
        })
        self.StockQuant._update_available_quantity(self.product_a, other_warehouse.lot_stock_id, 4.0)
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "order_line": [(0, 0, {
                "product_id": self.product_a.id,
                "product_uom_qty": 3.0,
            })],
        })
        line = order.order_line

        self.assertIn(self.bin_a, line.x_eligible_source_location_ids)
        self.assertNotIn(self.bin_b, line.x_eligible_source_location_ids)
        self.assertIn(other_warehouse.lot_stock_id, line.x_eligible_source_location_ids)
        self.assertIn(line.x_source_location_id._origin, line.x_eligible_source_location_ids)
        onchange_result = line._onchange_x_source_location_domain()
        self.assertEqual(
            onchange_result["domain"]["x_source_location_id"],
            [("id", "in", line.x_eligible_source_location_ids.ids)],
        )
        dropdown_options = self.StockLocation.with_context(
            sale_source_inventory_filter=True,
            sale_source_product_id=self.product_a.id,
            sale_source_product_uom_id=self.product_a.uom_id.id,
            sale_source_product_uom_qty=3.0,
            sale_source_warehouse_id=self.warehouse.id,
        ).name_search(limit=100)
        dropdown_location_ids = {location_id for location_id, _name in dropdown_options}
        self.assertIn(self.bin_a.id, dropdown_location_ids)
        self.assertNotIn(self.bin_b.id, dropdown_location_ids)
        self.assertIn(other_warehouse.lot_stock_id.id, dropdown_location_ids)

    def test_sale_line_source_inventory_options_are_empty_for_zero_or_negative_quantity(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "order_line": [(0, 0, {
                "product_id": self.product_a.id,
                "product_uom_qty": 0.0,
            })],
        })
        line = order.order_line

        self.assertFalse(line.x_eligible_source_location_ids)
        self.assertFalse(line.x_source_location_id)

        line.product_uom_qty = -1.0
        line._compute_x_eligible_source_location_ids()
        line._compute_x_source_location_id()
        self.assertFalse(line.x_eligible_source_location_ids)
        self.assertFalse(line.x_source_location_id)
        dropdown_options = self.StockLocation.with_context(
            sale_source_inventory_filter=True,
            sale_source_product_id=self.product_a.id,
            sale_source_product_uom_id=self.product_a.uom_id.id,
            sale_source_product_uom_qty=0.0,
            sale_source_warehouse_id=self.warehouse.id,
        ).name_search(limit=100)
        self.assertFalse(dropdown_options)

    def test_sale_confirmation_blocks_aggregate_source_shortage(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "order_line": [
                (0, 0, {
                    "product_id": self.product_a.id,
                    "product_uom_qty": 3.0,
                    "x_source_location_id": self.bin_a.id,
                }),
                (0, 0, {
                    "product_id": self.product_a.id,
                    "product_uom_qty": 3.0,
                    "x_source_location_id": self.bin_a.id,
                }),
            ],
        })

        with self.assertRaises(UserError):
            order.action_confirm()

    def test_sale_delivery_move_uses_selected_source_inventory(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "order_line": [(0, 0, {
                "product_id": self.product_a.id,
                "product_uom_qty": 3.0,
                "x_source_location_id": self.bin_a.id,
            })],
        })

        order.action_confirm()

        move = order.order_line.move_ids.filtered(lambda stock_move: stock_move.product_id == self.product_a)
        self.assertTrue(move)
        self.assertEqual(move[:1].location_id, self.bin_a)

    def test_product_attribute_apply_wizard_adds_attribute_to_all_products(self):
        templates = self.env["product.template"].search([])
        wizard = self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "Global Test Attribute",
            "value_name": "Default Test Value",
        })

        action = wizard.action_apply()

        attribute = self.env["product.attribute"].search([
            ("name", "=", "Global Test Attribute"),
        ], limit=1)
        value = self.env["product.attribute.value"].search([
            ("attribute_id", "=", attribute.id),
            ("name", "=", "Default Test Value"),
        ], limit=1)
        self.assertTrue(attribute)
        self.assertEqual(attribute.create_variant, "no_variant")
        self.assertTrue(attribute.x_apply_to_all_products)
        self.assertEqual(attribute.x_default_custom_value, "Default Test Value")
        self.assertTrue(value)
        self.assertTrue(value.is_custom)
        for template in templates:
            line = template.attribute_line_ids.filtered(
                lambda attribute_line: attribute_line.attribute_id == attribute
            )
            custom_value = template.x_custom_attribute_value_ids.filtered(
                lambda record: record.attribute_id == attribute
            )
            self.assertTrue(line)
            self.assertIn(value, line.value_ids)
            self.assertTrue(custom_value)
            self.assertEqual(custom_value.value_text, "Default Test Value")
        self.assertEqual(action["res_model"], "product.template")

    def test_new_product_gets_global_custom_attributes_as_free_text(self):
        wizard = self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "Future Product Attribute",
            "value_name": "Future Any Text 42.5 / 批次A",
        })
        wizard.action_apply()

        product = self.env["product.template"].create({
            "name": "Product Created After Global Attribute",
        })
        attribute = self.env["product.attribute"].search([
            ("name", "=", "Future Product Attribute"),
        ], limit=1)
        value = self.env["product.attribute.value"].search([
            ("attribute_id", "=", attribute.id),
            ("name", "=", "Future Any Text 42.5 / 批次A"),
        ], limit=1)
        line = product.attribute_line_ids.filtered(
            lambda attribute_line: attribute_line.attribute_id == attribute
        )
        custom_value = product.x_custom_attribute_value_ids.filtered(
            lambda record: record.attribute_id == attribute
        )

        self.assertTrue(line)
        self.assertIn(value, line.value_ids)
        self.assertTrue(custom_value)
        self.assertEqual(custom_value.value_text, "Future Any Text 42.5 / 批次A")

    def test_product_form_has_visible_custom_attributes_page(self):
        view = self.env.ref(
            "stock_subwarehouse_hierarchy.product_template_form_custom_attributes_visible"
        )

        self.assertIn('name="custom_attributes"', view.arch_db)
        self.assertIn('name="x_custom_attribute_value_ids"', view.arch_db)
        self.assertIn('name="value_text"', view.arch_db)

    def test_remove_global_custom_attribute_only_removes_managed_attribute(self):
        wizard = self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "Removable Custom Attribute",
            "value_name": "Remove Me",
        })
        wizard.action_apply()
        attribute = self.env["product.attribute"].search([
            ("name", "=", "Removable Custom Attribute"),
        ], limit=1)
        normal_attribute = self.env["product.attribute"].create({
            "name": "Normal Variant Attribute",
            "create_variant": "no_variant",
        })
        normal_value = self.env["product.attribute.value"].create({
            "name": "Normal Value",
            "attribute_id": normal_attribute.id,
        })
        self.product_a.product_tmpl_id.write({
            "attribute_line_ids": [(0, 0, {
                "attribute_id": normal_attribute.id,
                "value_ids": [(6, 0, normal_value.ids)],
            })],
        })

        remove_wizard = self.env["stock.subwarehouse.product.attribute.remove.wizard"].create({
            "attribute_id": attribute.id,
        })
        remove_wizard.action_remove()

        self.assertFalse(attribute.x_apply_to_all_products)
        self.assertFalse(self.env["product.template.custom.attribute.value"].search([
            ("attribute_id", "=", attribute.id),
        ]))
        self.assertTrue(self.product_a.product_tmpl_id.attribute_line_ids.filtered(
            lambda line: line.attribute_id == normal_attribute
        ))

    def test_product_import_template_is_available_on_import_page(self):
        templates = self.env["product.template"].get_import_templates()

        self.assertEqual(
            [template["template"] for template in templates],
            ["/stock_subwarehouse_hierarchy/import_template/product_template.xlsx"],
        )

    def test_product_import_template_auto_matches_global_custom_attributes(self):
        from openpyxl import load_workbook

        wizard = self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "Import Matched Attribute",
            "value_name": "Import Matched Value",
        })
        wizard.action_apply()

        content = self.env["product.template"]._generate_dynamic_product_import_template_xlsx()
        workbook = load_workbook(BytesIO(content), read_only=True)

        import_rows = list(workbook["产品导入"].iter_rows(values_only=True))
        import_headers = import_rows[0]
        chinese_labels = import_rows[1]
        self.assertIn("name", import_headers)
        self.assertIn("x_import_custom_attribute_1", import_headers)
        self.assertIn("x_import_custom_attribute_value_1", import_headers)
        self.assertIn("产品名称", chinese_labels)
        self.assertIn("自定义属性1", chinese_labels)
        self.assertIn("自定义属性值1", chinese_labels)

        custom_attribute_rows = list(workbook["自定义属性列表"].iter_rows(values_only=True))
        flattened_attribute_rows = "\n".join(
            " ".join(str(value or "") for value in row)
            for row in custom_attribute_rows
        )
        self.assertIn("Import Matched Attribute", flattened_attribute_rows)
        self.assertIn("Import Matched Value", flattened_attribute_rows)

    def test_product_import_preserves_multiple_custom_attribute_values_with_unique_columns(self):
        self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "测试属性",
            "value_name": "default",
        }).action_apply()
        self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "测试属性2号",
            "value_name": "default",
        }).action_apply()

        result = self.env["product.template"].load(
            [
                "name",
                "default_code",
                "x_import_custom_attribute_1",
                "x_import_custom_attribute_value_1",
                "x_import_custom_attribute_2",
                "x_import_custom_attribute_value_2",
            ],
            [[
                "Imported Multi Custom Attribute Product",
                "IMPORTED-MULTI-CUSTOM-ATTRIBUTE",
                "测试属性",
                "全场9九折 这个商品非常好",
                "测试属性2号",
                "第二个属性也应该显示导入值",
            ]],
        )

        self.assertFalse(result["messages"])
        product = self.env["product.template"].browse(result["ids"][0])
        values_by_attribute = {
            value.attribute_id.name: value.value_text
            for value in product.x_custom_attribute_value_ids
        }
        self.assertEqual(values_by_attribute["测试属性"], "全场9九折 这个商品非常好")
        self.assertEqual(values_by_attribute["测试属性2号"], "第二个属性也应该显示导入值")

    def test_product_import_preserves_legacy_repeated_custom_attribute_columns(self):
        self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "Legacy Attribute A",
            "value_name": "default",
        }).action_apply()
        self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "Legacy Attribute B",
            "value_name": "default",
        }).action_apply()

        result = self.env["product.template"].load(
            [
                "name",
                "default_code",
                "x_custom_attribute_value_ids/attribute_id",
                "x_custom_attribute_value_ids/value_text",
                "x_custom_attribute_value_ids/attribute_id",
                "x_custom_attribute_value_ids/value_text",
            ],
            [[
                "Imported Legacy Custom Attribute Product",
                "IMPORTED-LEGACY-CUSTOM-ATTRIBUTE",
                "Legacy Attribute A",
                "Legacy Value A",
                "Legacy Attribute B",
                "Legacy Value B",
            ]],
        )

        self.assertFalse(result["messages"])
        product = self.env["product.template"].browse(result["ids"][0])
        values_by_attribute = {
            value.attribute_id.name: value.value_text
            for value in product.x_custom_attribute_value_ids
        }
        self.assertEqual(values_by_attribute["Legacy Attribute A"], "Legacy Value A")
        self.assertEqual(values_by_attribute["Legacy Attribute B"], "Legacy Value B")

    def test_mrp_production_import_template_is_available_on_import_page(self):
        templates = self.env["mrp.production"].get_import_templates()

        self.assertEqual(
            [template["template"] for template in templates],
            ["/stock_subwarehouse_hierarchy/import_template/mrp_production.xlsx"],
        )

    def test_mrp_production_import_template_uses_current_product_attributes(self):
        from openpyxl import load_workbook

        attribute = self.env["product.attribute"].create({
            "name": "Template Dynamic Attribute",
            "create_variant": "no_variant",
        })
        value = self.env["product.attribute.value"].create({
            "name": "Template Dynamic Value",
            "attribute_id": attribute.id,
        })

        content = self.env["mrp.production"]._generate_dynamic_import_template_xlsx()
        workbook = load_workbook(BytesIO(content), read_only=True)

        import_headers = next(workbook["制造单导入"].iter_rows(values_only=True))
        self.assertIn("product_id", import_headers)
        self.assertIn("product_qty", import_headers)
        self.assertIn("never_product_template_attribute_value_ids", import_headers)

        attribute_rows = list(workbook["产品属性"].iter_rows(values_only=True))
        self.assertIn(
            (attribute.id, attribute.display_name, attribute.create_variant, value.id, value.display_name),
            attribute_rows,
        )

    def test_sale_order_import_template_uses_product_internal_reference_and_chinese_labels(self):
        from openpyxl import load_workbook

        templates = self.env["sale.order"].get_import_templates()
        self.assertEqual(
            [template["template"] for template in templates],
            ["/stock_subwarehouse_hierarchy/import_template/sale_order.xlsx"],
        )

        content = self.env["sale.order"]._generate_sale_order_import_template_xlsx()
        workbook = load_workbook(BytesIO(content), read_only=True)
        rows = list(workbook["报价单导入"].iter_rows(values_only=True))

        self.assertEqual(rows[0][0], "Order Reference")
        self.assertEqual(rows[1][0], "订单参考号")
        self.assertEqual(rows[0][:10], (
            "Order Reference",
            "Customer*",
            "Order Date",
            "Expiration",
            "Payment Terms",
            "Order Lines/Products*",
            "Order Lines/Quantity",
            "Order Lines/Unit Price",
            "Order Lines/Taxes",
            "Sales Team",
        ))
        self.assertIn("Order Lines/Products*", rows[0])
        self.assertNotIn("order_line/product_id/default_code", rows[0])
        self.assertNotIn("order_line/product_id/.id", rows[0])
        self.assertNotIn("order_line/product_id", rows[0])
        self.assertEqual(
            rows[1][rows[0].index("Order Lines/Products*")],
            "产品名称或产品ID",
        )

    def test_template_format_exports_match_import_headers(self):
        from openpyxl import load_workbook

        self.product_a.default_code = "EXPORT-PRODUCT-A"
        export_attribute_name = "Export Custom Attribute"
        export_attribute_value = "Export Real Custom Value 88"
        self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": export_attribute_name,
            "value_name": export_attribute_value,
        }).action_apply()
        sale_order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "order_line": [(
                0,
                0,
                {
                    "product_id": self.product_a.id,
                    "product_uom_qty": 2,
                    "price_unit": 15,
                },
            )],
        })
        production = self.env["mrp.production"].create({
            "product_id": self.product_a.id,
            "product_qty": 3,
            "product_uom_id": self.product_a.uom_id.id,
            "location_src_id": self.bin_a.id,
            "location_dest_id": self.bin_a.id,
        })

        product_workbook = load_workbook(
            BytesIO(self.product_a.product_tmpl_id._generate_dynamic_product_export_xlsx()),
            read_only=True,
        )
        product_rows = list(product_workbook["产品导出"].iter_rows(values_only=True))
        self.assertEqual(
            product_rows[0],
            tuple(field_name for field_name, _label in self.env["product.template"]._get_dynamic_product_import_columns()),
        )
        self.assertEqual(product_rows[2][product_rows[0].index("default_code")], "EXPORT-PRODUCT-A")
        attribute_column_index = product_rows[2].index(export_attribute_name)
        value_field = product_rows[0][attribute_column_index].replace(
            "x_import_custom_attribute_",
            "x_import_custom_attribute_value_",
        )
        self.assertEqual(product_rows[2][product_rows[0].index(value_field)], export_attribute_value)

        manufacturing_workbook = load_workbook(
            BytesIO(production._generate_dynamic_export_xlsx()),
            read_only=True,
        )
        manufacturing_rows = list(manufacturing_workbook["制造单导出"].iter_rows(values_only=True))
        self.assertEqual(
            manufacturing_rows[0],
            tuple(field_name for field_name, _label in self.env["mrp.production"]._get_dynamic_import_template_columns()),
        )
        self.assertEqual(manufacturing_rows[2][manufacturing_rows[0].index("product_qty")], 3)

        sale_workbook = load_workbook(
            BytesIO(sale_order._generate_sale_order_export_xlsx()),
            read_only=True,
        )
        sale_rows = list(sale_workbook["报价单导出"].iter_rows(values_only=True))
        self.assertEqual(
            sale_rows[0],
            tuple(field_name for field_name, _label in self.env["sale.order"]._get_sale_order_import_template_columns()),
        )
        self.assertEqual(sale_rows[1][sale_rows[0].index("Order Lines/Products*")], "产品名称或产品ID")
        self.assertEqual(sale_rows[2][sale_rows[0].index("Order Lines/Products*")], "EXPORT-PRODUCT-A")

        product_action = self.product_a.product_tmpl_id.action_export_import_template_format()
        manufacturing_action = production.action_export_import_template_format()
        sale_action = sale_order.action_export_import_template_format()
        self.assertIn("/stock_subwarehouse_hierarchy/export/product_template.xlsx", product_action["url"])
        self.assertIn("/stock_subwarehouse_hierarchy/export/mrp_production.xlsx", manufacturing_action["url"])
        self.assertIn("/stock_subwarehouse_hierarchy/export/sale_order.xlsx", sale_action["url"])

    def test_new_products_do_not_get_default_product_taxes(self):
        product = self.env["product.template"].create({
            "name": "No Default Tax Product",
            "is_storable": True,
        })

        self.assertFalse(product.taxes_id)
        self.assertFalse(product.supplier_taxes_id)
