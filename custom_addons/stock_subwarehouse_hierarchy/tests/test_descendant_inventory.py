from io import BytesIO
from unittest.mock import patch

from odoo import Command
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

    def test_webclient_bootstrap_uses_dashboard_home_instead_of_discuss(self):
        user = self.env.user.sudo()
        dashboard = self.env.ref("spreadsheet_dashboard.ir_actions_dashboard_action")
        discuss = self.env.ref("mail.action_discuss")
        user.action_id = discuss.id

        user._on_webclient_bootstrap()

        self.assertEqual(user.action_id.id, dashboard.id)

    def test_website_pages_and_menus_are_made_public(self):
        view = self.env["ir.ui.view"].create({
            "name": "Private website page test",
            "type": "qweb",
            "key": "stock_subwarehouse_hierarchy.private_website_page_test",
            "arch": "<t t-call='website.layout'><main>Public access test</main></t>",
            "visibility": "connected",
        })
        page = self.env["website.page"].create({
            "name": "Private website page test",
            "url": "/private-website-page-test",
            "view_id": view.id,
            "is_published": False,
        })
        menu = self.env["website.menu"].create({
            "name": "Private website page test",
            "url": page.url,
            "page_id": page.id,
            "group_ids": [Command.set([self.env.ref("base.group_user").id])],
        })

        self.env["website.page"].action_make_all_pages_public()

        self.assertTrue(page.website_published)
        self.assertFalse(page.visibility)
        self.assertFalse(page.group_ids)
        self.assertFalse(menu.group_ids)

    def test_apply_sun_logo_updates_company_logo(self):
        expected_logo = self.env["res.company"]._get_sun_logo_binary()

        self.env["res.company"].action_apply_sun_logo()

        self.assertEqual(self.env.company.logo, expected_logo)

    def test_chinese_language_is_default_for_users_and_partners(self):
        self.env["res.lang"].action_use_chinese_by_default()

        zh_cn = self.env["res.lang"].with_context(active_test=False).search([("code", "=", "zh_CN")])
        self.assertTrue(zh_cn.active)
        self.assertEqual(self.env["ir.default"]._get("res.partner", "lang"), "zh_CN")
        self.assertEqual(self.env.user.lang, "zh_CN")
        self.assertEqual(self.env.user.partner_id.lang, "zh_CN")

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

    def test_warehouse_create_subwarehouse_creates_internal_location(self):
        action = self.warehouse.action_create_subwarehouse()
        location = self.StockLocation.browse(action["res_id"])

        self.assertEqual(location.usage, "internal")
        self.assertEqual(location.location_id, self.warehouse.view_location_id)
        self.assertTrue(location.x_is_subwarehouse)

    def _create_sale_order_line(self, product, quantity, source_location=False):
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
        })
        line_values = {
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": quantity,
            "product_uom_id": product.uom_id.id,
        }
        if source_location:
            line_values["x_source_location_id"] = source_location.id
        line = self.env["sale.order.line"].create(line_values)
        return order, line

    def test_website_payment_auto_assigns_exact_stocked_subwarehouse(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 2.0)
        self.StockQuant._update_available_quantity(self.product_a, self.bin_b, 5.0)
        order, line = self._create_sale_order_line(self.product_a, 4.0)
        line.x_source_location_id = False

        order._prepare_website_stock_for_payment()

        self.assertEqual(line.x_source_location_id, self.bin_b)
        self.assertTrue(line.x_website_stock_reserved_until)
        self.assertTrue(order.x_website_stock_reserved_at)

    def test_website_payment_hold_blocks_second_quotation(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        first_order, first_line = self._create_sale_order_line(self.product_a, 4.0)
        first_line.x_source_location_id = False
        first_order._prepare_website_stock_for_payment()

        second_order, second_line = self._create_sale_order_line(self.product_a, 2.0)
        second_line.x_source_location_id = False

        with self.assertRaises(UserError):
            second_order._prepare_website_stock_for_payment()

    def _create_simulated_wechat_transaction(self, order, reference):
        provider = self.env.ref("payment_wechatpay.payment_provider_wechatpay")
        provider.write({
            "state": "test",
            "wechatpay_simulation_mode": True,
        })
        return self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": self.env.ref("payment_wechatpay.payment_method_wechatpay").id,
            "reference": reference,
            "amount": order.amount_total,
            "currency_id": order.currency_id.id,
            "partner_id": order.partner_id.id,
            "operation": "online_redirect",
            "sale_order_ids": [Command.set(order.ids)],
        })

    def test_simulated_wechat_payment_confirms_stocked_website_order(self):
        self.product_a.list_price = 120.0
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        order, line = self._create_sale_order_line(self.product_a, 2.0)
        order.website_id = self.env["website"].get_current_website()
        line.x_source_location_id = False
        order._prepare_website_stock_for_payment()
        tx = self._create_simulated_wechat_transaction(order, "WX-WEBSITE-SUCCESS")
        tx._wechatpay_ensure_native_order()

        tx._process("wechatpay", {
            "reference": tx.reference,
            "out_trade_no": tx.wechatpay_out_trade_no,
            "transaction_id": f"SIM-{tx.reference}",
            "trade_state": "SUCCESS",
        })
        with patch.object(
            self.env.registry["sale.order"],
            "_send_order_confirmation_mail",
            side_effect=UserError("Simulated PDF/email failure"),
        ):
            tx._post_process()

        self.assertEqual(tx.state, "done")
        self.assertEqual(order.state, "sale")
        self.assertEqual(line.x_source_location_id, self.bin_a)
        delivery_move = line.move_ids.filtered(lambda move: move.product_id == self.product_a)
        self.assertTrue(delivery_move)
        self.assertEqual(delivery_move[:1].location_id, self.bin_a)

    def test_simulated_wechat_payment_does_not_charge_after_stock_disappears(self):
        self.product_a.list_price = 120.0
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 2.0)
        order, line = self._create_sale_order_line(self.product_a, 2.0)
        line.x_source_location_id = False
        order._prepare_website_stock_for_payment()
        tx = self._create_simulated_wechat_transaction(order, "WX-WEBSITE-NO-STOCK")
        tx._wechatpay_ensure_native_order()
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, -2.0)

        tx._process("wechatpay", {
            "reference": tx.reference,
            "out_trade_no": tx.wechatpay_out_trade_no,
            "transaction_id": f"SIM-{tx.reference}",
            "trade_state": "SUCCESS",
        })

        self.assertEqual(tx.state, "error")
        self.assertEqual(order.state, "draft")
        self.assertIn("库存复核失败", tx.state_message)

    def test_shop_groups_published_same_name_products_by_representative(self):
        product_1, product_2, product_3 = self.env["product.template"].create([
            {"name": "Shop Group Test", "sale_ok": True},
            {"name": "Shop Group Test", "sale_ok": True},
            {"name": "Different Shop Group", "sale_ok": True},
        ])
        (product_1 | product_2 | product_3).action_publish_to_shop()

        grouped_products = (product_1 | product_2 | product_3)._get_shop_grouped_products()

        self.assertEqual(grouped_products, product_1 | product_3)
        self.assertEqual(product_1.x_shop_group_variant_count, 2)

    def test_shop_product_family_filter_splits_ski_snowboard_and_other(self):
        ski, bracketed_ski, snowboard, other = self.env["product.template"].create([
            {"name": "零售滑雪双板", "default_code": "062410X-MA006-W001170", "sale_ok": True},
            {"name": "滑雪杖", "default_code": "[012410Z-MA000-HR01130]", "sale_ok": True},
            {"name": "儿童单板刻滑滑雪板", "default_code": "052411Dc-MK787-HX02135", "sale_ok": True},
            {"name": "滑雪手套", "default_code": "112411T1-MA000-P001##L", "sale_ok": True},
        ])

        products = ski | bracketed_ski | snowboard | other

        self.assertEqual(products._filter_shop_products_by_family("ski"), ski | bracketed_ski)
        self.assertEqual(products._filter_shop_products_by_family("snowboard"), snowboard)
        self.assertEqual(products._filter_shop_products_by_family("other"), other)

    def test_managed_custom_attributes_are_hidden_from_website_lines(self):
        custom_attribute = self.env["product.attribute"].create({
            "name": "测试自定义属性",
            "x_apply_to_all_products": True,
        })
        visible_attribute = self.env["product.attribute"].create({
            "name": "公开规格",
        })
        visible_value = self.env["product.attribute.value"].create({
            "name": "公开值",
            "attribute_id": visible_attribute.id,
        })
        product = self.env["product.template"].create({
            "name": "Website Attribute Test",
            "sale_ok": True,
            "attribute_line_ids": [
                Command.create({
                    "attribute_id": visible_attribute.id,
                    "value_ids": [Command.set(visible_value.ids)],
                }),
            ],
        })

        visible_lines = product._get_visible_website_attribute_lines()
        single_values = product.valid_product_template_attribute_line_ids._prepare_single_value_for_display()

        self.assertNotIn(custom_attribute, visible_lines.attribute_id)
        self.assertIn(visible_attribute, visible_lines.attribute_id)
        self.assertNotIn(custom_attribute, single_values)
        self.assertIn(visible_attribute, single_values)

    def test_shop_group_variant_values_use_custom_attributes(self):
        color_attribute = self.env["product.attribute"].create({
            "name": "颜色",
            "x_apply_to_all_products": True,
        })
        size_attribute = self.env["product.attribute"].create({
            "name": "尺码",
            "x_apply_to_all_products": True,
        })
        product = self.env["product.template"].create({
            "name": "Variant Value Test",
            "default_code": "SKU-001",
            "sale_ok": True,
        })
        product.x_custom_attribute_value_ids.filtered(
            lambda value: value.attribute_id == color_attribute
        ).value_text = "黑色"
        product.x_custom_attribute_value_ids.filtered(
            lambda value: value.attribute_id == size_attribute
        ).value_text = "260"

        variant_values = product._get_shop_variant_display_values()

        self.assertEqual(variant_values["default_code"], "SKU-001")
        self.assertEqual(variant_values["color"], "黑色")
        self.assertEqual(variant_values["size"], "260")

    def test_shop_variant_values_decode_missing_values_from_product_id(self):
        product = self.env["product.template"].create({
            "name": "Decoded Variant Test",
            "default_code": "152410Yb-MK000-H001150",
            "sale_ok": True,
        })

        variant_values = product._get_shop_variant_display_values()

        self.assertEqual(variant_values["color"], "黑")
        self.assertEqual(variant_values["size"], "150")
        self.assertEqual(variant_values["flex"], "无硬度")
        self.assertEqual(variant_values["audience"], "儿童/青少年")

    def test_shop_variant_values_decode_mixed_color_and_letter_size(self):
        product = self.env["product.template"].create({
            "name": "Decoded Letter Size Test",
            "default_code": "072409Y-MA000-G001##S",
            "sale_ok": True,
        })

        variant_values = product._get_shop_variant_display_values()

        self.assertEqual(variant_values["color"], "绿")
        self.assertEqual(variant_values["size"], "S")
        self.assertEqual(variant_values["flex"], "无硬度")
        self.assertEqual(variant_values["audience"], "成人")

    def test_shop_variant_values_decode_hardness_and_multiple_colors(self):
        product = self.env["product.template"].create({
            "name": "Decoded Flex Test",
            "default_code": "152410Y-MK787-HW02130",
            "sale_ok": True,
        })

        variant_values = product._get_shop_variant_display_values()

        self.assertEqual(variant_values["color"], "黑白")
        self.assertEqual(variant_values["size"], "130")
        self.assertEqual(variant_values["flex"], "787")
        self.assertEqual(variant_values["audience"], "儿童/青少年")

    def test_shop_group_variant_option_groups_use_same_name_products(self):
        product_1, product_2, product_3 = self.env["product.template"].create([
            {
                "name": "Shop Selector Test",
                "default_code": "152410Yb-MK000-H001150",
                "sale_ok": True,
            },
            {
                "name": "Shop Selector Test",
                "default_code": "152410Yb-MK000-W001155",
                "sale_ok": True,
            },
            {
                "name": "Shop Selector Test",
                "default_code": "152410Yb-MK100-H001150",
                "sale_ok": True,
            },
        ])
        (product_1 | product_2 | product_3).action_publish_to_shop()

        option_groups = product_1._get_shop_group_variant_option_groups()
        options_by_key = {
            group["key"]: group["values"]
            for group in option_groups
        }

        self.assertEqual(options_by_key["color"], ["黑", "白"])
        self.assertEqual(options_by_key["size"], ["150", "155"])
        self.assertEqual(options_by_key["flex"], ["无硬度", "100"])

    def test_shop_availability_uses_current_product_on_hand(self):
        product = self.env["product.template"].create({
            "name": "Shop Stock Test",
            "is_storable": True,
            "sale_ok": True,
        })

        self.assertFalse(product._is_shop_available())

        self.StockQuant._update_available_quantity(
            product.product_variant_id,
            self.warehouse.lot_stock_id,
            2.0,
        )

        self.assertTrue(product._is_shop_available())
        self.assertEqual(product._get_shop_available_quantity(), 2.0)

    def test_shop_publish_actions_toggle_website_visibility(self):
        product = self.env["product.template"].create({
            "name": "Publish Action Test",
            "sale_ok": False,
            "website_published": False,
        })

        product.action_publish_to_shop()

        self.assertTrue(product.sale_ok)
        self.assertTrue(product.website_published)

        product.action_unpublish_from_shop()

        self.assertFalse(product.website_published)

    def test_source_location_check_does_not_use_descendant_stock(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        order, _line = self._create_sale_order_line(self.product_a, 1.0, self.subwarehouse)

        with self.assertRaises(UserError):
            order._check_source_inventory_availability()

    def test_view_location_manufacture_product_action_creates_internal_child_when_missing(self):
        view_location = self.StockLocation.create({
            "name": "Empty Manufacturing Subwarehouse",
            "usage": "view",
            "location_id": self.warehouse.view_location_id.id,
        })

        action = view_location.action_manufacture_product()
        internal_child = self.StockLocation.browse(action["context"]["default_location_dest_id"])

        self.assertEqual(internal_child.usage, "internal")
        self.assertEqual(internal_child.location_id, view_location)
        self.assertTrue(internal_child.x_is_subwarehouse)

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

    def test_manufacturing_completion_adds_stock_to_subwarehouse_inventory(self):
        action = self.bin_a.action_manufacture_product()
        production = self.env["mrp.production"].with_context(action["context"]).create({
            "product_id": self.product_a.id,
            "product_qty": 2.0,
            "product_uom_id": self.product_a.uom_id.id,
            "picking_type_id": self.warehouse.manu_type_id.id,
        })

        production.action_confirm()
        production.qty_producing = 2.0
        production.button_mark_done()

        quantity = self.StockQuant._get_available_quantity(self.product_a, self.bin_a)
        self.assertEqual(quantity, 2.0)

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
        self.assertEqual(line.x_source_available_qty, 0.0)
        self.assertFalse(line.x_source_can_fulfill)
        with self.assertRaises(UserError):
            order.action_confirm()

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

    def test_sale_source_inventory_name_search_can_return_more_than_default_dropdown_limit(self):
        stocked_locations = self.env["stock.location"]
        for index in range(9):
            location = self.StockLocation.create({
                "name": f"Overflow Source {index}",
                "usage": "internal",
                "location_id": self.warehouse.view_location_id.id,
            })
            stocked_locations |= location
            self.StockQuant._update_available_quantity(self.product_a, location, 5.0)

        dropdown_options = self.StockLocation.with_context(
            sale_source_inventory_filter=True,
            sale_source_product_id=self.product_a.id,
            sale_source_product_uom_id=self.product_a.uom_id.id,
            sale_source_product_uom_qty=3.0,
            sale_source_warehouse_id=self.warehouse.id,
        ).name_search(limit=10)
        dropdown_location_ids = {location_id for location_id, _name in dropdown_options}

        self.assertTrue(set(stocked_locations.ids).issubset(dropdown_location_ids))

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

    def test_internal_transfer_from_parent_location_does_not_reserve_child_stock(self):
        self.StockQuant._update_available_quantity(self.product_a, self.bin_a, 5.0)
        picking = self.env["stock.picking"].create({
            "picking_type_id": self.warehouse.int_type_id.id,
            "location_id": self.subwarehouse.id,
            "location_dest_id": self.warehouse.lot_stock_id.id,
            "move_ids": [(0, 0, {
                "description_picking": self.product_a.display_name,
                "product_id": self.product_a.id,
                "product_uom_qty": 3.0,
                "product_uom": self.product_a.uom_id.id,
                "location_id": self.subwarehouse.id,
                "location_dest_id": self.warehouse.lot_stock_id.id,
            })],
        })

        picking.action_confirm()
        picking.action_assign()

        move = picking.move_ids.filtered(lambda stock_move: stock_move.product_id == self.product_a)
        self.assertNotEqual(move.state, "assigned")
        self.assertFalse(move.move_line_ids.filtered(lambda line: line.location_id == self.bin_a))
        with self.assertRaises(UserError):
            picking.button_validate()

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

    def test_product_views_show_material_type(self):
        form_view = self.env.ref(
            "stock_subwarehouse_hierarchy.product_template_form_material_type_visible"
        )
        list_view = self.env.ref(
            "stock_subwarehouse_hierarchy.product_template_list_material_type_visible"
        )
        search_view = self.env.ref(
            "stock_subwarehouse_hierarchy.product_template_search_material_type_filters"
        )

        self.assertIn('name="x_material_type"', form_view.arch_db)
        self.assertIn('name="x_material_type"', list_view.arch_db)
        self.assertIn("material_component", search_view.arch_db)
        self.assertIn("group_by_material_type", search_view.arch_db)

    def test_inventory_material_type_actions_filter_finished_and_components(self):
        self.product_a.product_tmpl_id.x_material_type = "finished"
        self.product_b.product_tmpl_id.x_material_type = "component"

        finished_action = self.env.ref(
            "stock_subwarehouse_hierarchy.action_finished_product_inventory"
        )
        component_action = self.env.ref(
            "stock_subwarehouse_hierarchy.action_component_inventory"
        )
        search_view = self.env.ref(
            "stock_subwarehouse_hierarchy.stock_quant_search_material_type_filters"
        )

        self.assertIn("x_material_type", finished_action.domain)
        self.assertIn("finished", finished_action.domain)
        self.assertIn("x_material_type", component_action.domain)
        self.assertIn("component", component_action.domain)
        self.assertIn("material_finished_inventory", search_view.arch_db)
        self.assertIn("material_component_inventory", search_view.arch_db)

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
        self.assertIn("x_material_type", import_headers)
        self.assertIn("is_storable", import_headers)
        self.assertIn("x_import_custom_attribute_value_1", import_headers)
        self.assertNotIn("x_import_custom_attribute_1", import_headers)
        self.assertEqual(
            chinese_labels[import_headers.index("x_material_type")],
            "\u7269\u6599\u7c7b\u578b",
        )
        self.assertIn("产品名称", chinese_labels)
        self.assertIn("Import Matched Attribute", chinese_labels)

        custom_attribute_rows = list(workbook["自定义属性列表"].iter_rows(values_only=True))
        flattened_attribute_rows = "\n".join(
            " ".join(str(value or "") for value in row)
            for row in custom_attribute_rows
        )
        self.assertIn("Import Matched Attribute", flattened_attribute_rows)
        self.assertIn("Import Matched Value", flattened_attribute_rows)

    def test_product_import_preserves_multiple_custom_attribute_values_with_one_column_per_attribute(self):
        self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "测试属性",
            "value_name": "default",
        }).action_apply()
        self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": "测试属性2号",
            "value_name": "default",
        }).action_apply()
        self.env["product.attribute"].search([
            ("name", "=", "测试属性"),
        ], limit=1).sequence = -100
        self.env["product.attribute"].search([
            ("name", "=", "测试属性2号"),
        ], limit=1).sequence = -99

        result = self.env["product.template"].load(
            [
                "name",
                "default_code",
                "x_import_custom_attribute_value_1",
                "x_import_custom_attribute_value_2",
            ],
            [[
                "Imported Multi Custom Attribute Product",
                "IMPORTED-MULTI-CUSTOM-ATTRIBUTE",
                "全场9九折 这个商品非常好",
                "第二个属性也应该显示导入值",
            ]],
        )

        self.assertFalse(result["messages"])
        product = self.env["product.template"].browse(result["ids"][0])
        self.assertTrue(product.is_storable)
        values_by_attribute = {
            value.attribute_id.name: value.value_text
            for value in product.x_custom_attribute_value_ids
        }
        self.assertEqual(values_by_attribute["测试属性"], "全场9九折 这个商品非常好")
        self.assertEqual(values_by_attribute["测试属性2号"], "第二个属性也应该显示导入值")

    def test_product_import_without_is_storable_still_tracks_inventory(self):
        result = self.env["product.template"].load(
            ["name", "default_code", "type", "x_material_type"],
            [["Imported Stock Product", "IMPORTED-STOCK-PRODUCT", "consu", "component"]],
        )

        self.assertFalse(result["messages"])
        product_template = self.env["product.template"].browse(result["ids"][0])
        self.assertTrue(product_template.is_storable)
        self.assertEqual(product_template.x_material_type, "component")

        product = product_template.product_variant_id
        action = self.bin_a.action_manufacture_product()
        production = self.env["mrp.production"].with_context(action["context"]).create({
            "product_id": product.id,
            "product_qty": 1.0,
            "product_uom_id": product.uom_id.id,
            "picking_type_id": self.warehouse.manu_type_id.id,
        })
        production.action_confirm()
        production.qty_producing = 1.0
        production.button_mark_done()

        quantity = self.StockQuant._get_available_quantity(product, self.bin_a)
        self.assertEqual(quantity, 1.0)

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

    def test_bom_import_template_is_available_on_import_page(self):
        templates = self.env["mrp.bom"].get_import_templates()

        self.assertEqual(
            [template["template"] for template in templates],
            ["/stock_subwarehouse_hierarchy/import_template/mrp_bom.xlsx"],
        )
        self.assertTrue(
            getattr(type(self.env["mrp.bom"]).get_import_templates, "_api_model", False),
            "BOM import templates must be callable by the import screen without record ids.",
        )

    def test_product_bom_button_opens_component_bom_or_new_form(self):
        self.env["mrp.bom"].create({
            "product_tmpl_id": self.product_a.product_tmpl_id.id,
            "product_qty": 1,
            "product_uom_id": self.product_a.uom_id.id,
            "type": "normal",
        })
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": self.product_a.product_tmpl_id.id,
            "product_qty": 1,
            "product_uom_id": self.product_a.uom_id.id,
            "type": "normal",
            "bom_line_ids": [(0, 0, {
                "product_id": self.product_b.id,
                "product_qty": 2,
                "product_uom_id": self.product_b.uom_id.id,
            })],
        })

        existing_action = self.product_a.product_tmpl_id.action_configure_product_bom()
        self.assertEqual(existing_action["res_model"], "mrp.bom")
        self.assertEqual(existing_action["res_id"], bom.id)
        self.assertEqual(existing_action["context"]["default_product_tmpl_id"], self.product_a.product_tmpl_id.id)

        product = self.env["product.product"].create({
            "name": "Finished Without BOM Yet",
            "is_storable": True,
            "x_material_type": "finished",
        })
        new_action = product.product_tmpl_id.action_configure_product_bom()
        self.assertEqual(new_action["res_model"], "mrp.bom")
        self.assertNotIn("res_id", new_action)
        self.assertEqual(new_action["context"]["default_product_tmpl_id"], product.product_tmpl_id.id)

    def test_mrp_production_uses_product_bom_components_by_default(self):
        self.product_a.product_tmpl_id.x_material_type = "finished"
        self.product_b.product_tmpl_id.x_material_type = "component"
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": self.product_a.product_tmpl_id.id,
            "product_qty": 1,
            "product_uom_id": self.product_a.uom_id.id,
            "type": "normal",
            "bom_line_ids": [(0, 0, {
                "product_id": self.product_b.id,
                "product_qty": 2,
                "product_uom_id": self.product_b.uom_id.id,
            })],
        })

        production = self.env["mrp.production"].create({
            "product_id": self.product_a.id,
            "product_qty": 3,
            "product_uom_id": self.product_a.uom_id.id,
            "location_src_id": self.bin_a.id,
            "location_dest_id": self.bin_a.id,
        })

        self.assertEqual(production.bom_id, bom)
        raw_move = production.move_raw_ids.filtered(lambda move: move.product_id == self.product_b)
        self.assertEqual(len(raw_move), 1)
        self.assertEqual(raw_move.product_uom_qty, 6)

    def test_mrp_production_form_onchange_shows_bom_components(self):
        self.product_a.product_tmpl_id.x_material_type = "finished"
        self.product_b.product_tmpl_id.x_material_type = "component"
        self.env["mrp.bom"].create({
            "product_tmpl_id": self.product_a.product_tmpl_id.id,
            "product_qty": 1,
            "product_uom_id": self.product_a.uom_id.id,
            "type": "normal",
        })
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": self.product_a.product_tmpl_id.id,
            "product_qty": 1,
            "product_uom_id": self.product_a.uom_id.id,
            "type": "normal",
            "bom_line_ids": [(0, 0, {
                "product_id": self.product_b.id,
                "product_qty": 2,
                "product_uom_id": self.product_b.uom_id.id,
            })],
        })

        production = self.env["mrp.production"].new({
            "product_id": self.product_a.id,
            "product_qty": 3,
            "product_uom_id": self.product_a.uom_id.id,
            "location_src_id": self.bin_a.id,
            "location_dest_id": self.bin_a.id,
            "company_id": self.env.company.id,
        })
        production._onchange_product_id_use_default_bom_components()

        self.assertEqual(production.bom_id, bom)
        raw_move = production.move_raw_ids.filtered(lambda move: move.product_id == self.product_b)
        self.assertEqual(len(raw_move), 1)
        self.assertEqual(raw_move.product_uom_qty, 2)

    def test_bom_import_template_can_create_component_lines(self):
        self.product_b.default_code = "BOM-COMPONENT-B"
        self.product_b.product_tmpl_id.x_material_type = "component"

        result = self.env["mrp.bom"].load(
            [
                "product_tmpl_id",
                "product_qty",
                "product_uom_id",
                "type",
                "code",
                "x_import_bom_component_product_1",
                "x_import_bom_component_qty_1",
                "x_import_bom_component_uom_1",
            ],
            [[
                self.product_a.product_tmpl_id.display_name,
                1,
                self.product_a.uom_id.display_name,
                "normal",
                "BOM-IMPORT-TEST",
                "BOM-COMPONENT-B",
                2,
                self.product_b.uom_id.display_name,
            ]],
        )

        self.assertFalse(result["messages"])
        bom = self.env["mrp.bom"].browse(result["ids"][0])
        self.assertEqual(bom.code, "BOM-IMPORT-TEST")
        self.assertEqual(len(bom.bom_line_ids), 1)
        self.assertEqual(bom.bom_line_ids.product_id, self.product_b)
        self.assertEqual(bom.bom_line_ids.product_qty, 2)

    def test_bom_import_fails_when_component_product_is_missing(self):
        with self.assertRaisesRegex(UserError, "MISSING-COMPONENT-REF"):
            self.env["mrp.bom"].load(
                [
                    "product_tmpl_id",
                    "product_qty",
                    "product_uom_id",
                    "type",
                    "code",
                    "x_import_bom_component_product_1",
                    "x_import_bom_component_qty_1",
                    "x_import_bom_component_uom_1",
                ],
                [[
                    self.product_a.product_tmpl_id.display_name,
                    1,
                    self.product_a.uom_id.display_name,
                    "normal",
                    "BOM-MISSING-COMPONENT-TEST",
                    "MISSING-COMPONENT-REF",
                    2,
                    self.product_b.uom_id.display_name,
                ]],
            )

    def test_bom_import_export_template_matches_component_slots(self):
        from openpyxl import load_workbook

        self.product_b.default_code = "BOM-EXPORT-COMPONENT"
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": self.product_a.product_tmpl_id.id,
            "product_qty": 1,
            "product_uom_id": self.product_a.uom_id.id,
            "code": "BOM-EXPORT-TEST",
            "bom_line_ids": [(0, 0, {
                "product_id": self.product_b.id,
                "product_qty": 3,
                "product_uom_id": self.product_b.uom_id.id,
            })],
        })

        import_workbook = load_workbook(
            BytesIO(self.env["mrp.bom"]._generate_bom_import_template_xlsx()),
            read_only=True,
        )
        import_rows = list(import_workbook["物料清单导入"].iter_rows(values_only=True))
        self.assertIn("x_import_bom_component_product_1", import_rows[0])
        self.assertEqual(
            import_rows[1][import_rows[0].index("x_import_bom_component_product_1")],
            "组件产品 1",
        )

        export_workbook = load_workbook(
            BytesIO(bom._generate_bom_export_xlsx()),
            read_only=True,
        )
        export_rows = list(export_workbook["物料清单导出"].iter_rows(values_only=True))
        self.assertEqual(
            export_rows[0],
            tuple(field_name for field_name, _label in self.env["mrp.bom"]._get_bom_import_template_columns()),
        )
        self.assertEqual(export_rows[2][export_rows[0].index("code")], "BOM-EXPORT-TEST")
        self.assertEqual(
            export_rows[2][export_rows[0].index("x_import_bom_component_product_1")],
            "BOM-EXPORT-COMPONENT",
        )
        self.assertEqual(export_rows[2][export_rows[0].index("x_import_bom_component_qty_1")], 3)

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
        self.assertEqual(rows[1][0], "订单号")
        self.assertEqual(rows[0][:16], (
            "Order Reference",
            "Customer*",
            "Order Date",
            "x_platform",
            "x_channel",
            "Salesperson",
            "x_sale_nature",
            "Order Lines/Products*",
            "Order Lines/x_import_product_name",
            "Order Lines/x_color",
            "Order Lines/x_size",
            "Order Lines/x_flex",
            "Order Lines/Quantity",
            "Order Lines/Unit Price",
            "Order Lines/x_source_location_id",
            "x_finance_remark",
        ))
        self.assertIn("Order Lines/Products*", rows[0])
        self.assertNotIn("order_line/product_id/default_code", rows[0])
        self.assertNotIn("order_line/product_id/.id", rows[0])
        self.assertNotIn("order_line/product_id", rows[0])
        self.assertEqual(
            rows[1][rows[0].index("Order Lines/Products*")],
            "产品ID",
        )
        self.assertEqual(rows[1][rows[0].index("Order Lines/x_color")], "颜色")
        self.assertEqual(rows[1][rows[0].index("Order Lines/x_source_location_id")], "发货仓库")

    def test_template_format_exports_match_import_headers(self):
        from openpyxl import load_workbook

        self.product_a.default_code = "EXPORT-PRODUCT-A"
        self.product_a.product_tmpl_id.x_material_type = "component"
        export_attribute_name = "Export Custom Attribute"
        export_attribute_value = "Export Real Custom Value 88"
        self.env["stock.subwarehouse.product.attribute.apply.wizard"].create({
            "attribute_name": export_attribute_name,
            "value_name": export_attribute_value,
        }).action_apply()
        sale_order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "x_platform": "有赞",
            "x_channel": "凌动雪具",
            "x_sale_nature": "retail",
            "x_finance_remark": "财务备注",
            "order_line": [(
                0,
                0,
                {
                    "product_id": self.product_a.id,
                    "product_uom_qty": 2,
                    "price_unit": 15,
                    "x_import_product_name": "双板鞋",
                    "x_color": "黑色",
                    "x_size": "260",
                    "x_flex": "硬度100",
                    "x_source_location_id": self.bin_a.id,
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
        self.assertEqual(product_rows[2][product_rows[0].index("x_material_type")], "component")
        self.assertNotIn(export_attribute_name, product_rows[2])
        self.assertIn(export_attribute_name, product_rows[1])
        attribute_column_index = product_rows[1].index(export_attribute_name)
        self.assertTrue(product_rows[0][attribute_column_index].startswith("x_import_custom_attribute_value_"))
        self.assertEqual(product_rows[2][attribute_column_index], export_attribute_value)

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
        self.assertEqual(sale_rows[1][sale_rows[0].index("Order Lines/Products*")], "产品ID")
        self.assertEqual(sale_rows[2][sale_rows[0].index("Order Lines/Products*")], "EXPORT-PRODUCT-A")
        self.assertEqual(sale_rows[2][sale_rows[0].index("x_platform")], "有赞")
        self.assertEqual(sale_rows[2][sale_rows[0].index("x_sale_nature")], "零售")
        self.assertEqual(sale_rows[2][sale_rows[0].index("Order Lines/x_import_product_name")], "双板鞋")
        self.assertEqual(sale_rows[2][sale_rows[0].index("Order Lines/x_color")], "黑色")
        self.assertEqual(sale_rows[2][sale_rows[0].index("Order Lines/x_size")], "260")
        self.assertEqual(sale_rows[2][sale_rows[0].index("Order Lines/x_flex")], "硬度100")
        self.assertEqual(sale_rows[2][sale_rows[0].index("Order Lines/x_source_location_id")], self.bin_a.display_name)
        self.assertEqual(sale_rows[2][sale_rows[0].index("x_finance_remark")], "财务备注")

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

    def test_currency_symbols_use_yuan(self):
        usd = self.env.ref("base.USD")
        cny = self.env.ref("base.CNY")

        self.env["res.currency"].action_use_yuan_symbol_everywhere()

        self.assertTrue(cny.active)
        self.assertEqual(cny.symbol, "￥")
        self.assertEqual(usd.symbol, "￥")
        self.assertEqual(self.env.company.currency_id, cny)
        if "product.pricelist" in self.env:
            self.assertTrue(all(
                pricelist.currency_id == cny
                for pricelist in self.env["product.pricelist"].search([])
            ))
