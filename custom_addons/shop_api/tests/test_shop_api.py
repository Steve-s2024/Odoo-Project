import json
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessDenied, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.shop_api.models.api_catalog import BUILTIN_ENDPOINTS, BUILTIN_SCOPES
from odoo.addons.shop_api.models.api_runtime import redact_payload


class ShopApiTestMixin:
    @classmethod
    def _setup_shop_api_data(cls):
        cls.warehouse = cls.env["stock.warehouse"].create({
            "name": "Shop API Test Warehouse",
            "code": "SAT",
        })
        cls.bin_location = cls.env["stock.location"].create({
            "name": "Shop API Test Bin",
            "usage": "internal",
            "location_id": cls.warehouse.view_location_id.id,
        })
        cls.product = cls.env["product.product"].create({
            "name": "Shop API Test Product",
            "default_code": "SHOP-API-TEST",
            "is_storable": True,
            "list_price": 100.0,
        })
        cls.product.product_tmpl_id.write({
            "website_published": True,
            "x_website_english_name": "Shop API Test Product",
        })
        cls.env["stock.quant"]._update_available_quantity(
            cls.product, cls.bin_location, 5.0,
        )
        integration_group = cls.env.ref("shop_api.group_shop_api_integration")
        cls.integration_user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Shop API Test Client",
            "login": "shop-api-test-client",
            "group_ids": [Command.set([
                cls.env.ref("base.group_user").id,
                integration_group.id,
            ])],
        })
        scopes = cls.env["shop.api.scope"].search([])
        cls.client = cls.env["shop.api.client"].create({
            "name": "Test separated shop",
            "code": "test_shop",
            "user_id": cls.integration_user.id,
            "scope_ids": [Command.set(scopes.ids)],
            "company_ids": [Command.set(cls.env.company.ids)],
            "website_ids": [Command.set(cls.env["website"].get_current_website().ids)],
        })
        cls.customer = cls.env["res.partner"].create({
            "name": "Shop API Customer",
            "email": "customer@example.test",
            "customer_rank": 1,
        })
        cls.chinese_pricelist = cls.env["product.pricelist"].create({
            "name": "Shop API Test CNY Pricelist",
            "currency_id": cls.env.ref("base.CNY").id,
        })
        cls.english_pricelist = cls.env["product.pricelist"].create({
            "name": "Shop API Test USD Pricelist",
            "currency_id": cls.env.ref("base.USD").id,
        })
        cls.env["shop.api.configuration"]._ensure_default_configuration().write({
            "chinese_pricelist_id": cls.chinese_pricelist.id,
            "english_pricelist_id": cls.english_pricelist.id,
        })


@tagged("post_install", "-at_install")
class TestShopApiBackend(ShopApiTestMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_shop_api_data()

    def test_builtin_endpoint_catalog_and_scopes_are_complete(self):
        endpoint_codes = set(self.env["shop.api.endpoint"].search([]).mapped("code"))
        scope_codes = set(self.env["shop.api.scope"].search([]).mapped("code"))

        self.assertTrue({item[0] for item in BUILTIN_ENDPOINTS}.issubset(endpoint_codes))
        self.assertTrue({item[0] for item in BUILTIN_SCOPES}.issubset(scope_codes))
        self.assertEqual(
            len(BUILTIN_ENDPOINTS),
            len({(item[1], item[2]) for item in BUILTIN_ENDPOINTS}),
        )
        self.assertTrue(all(endpoint.name for endpoint in self.env["shop.api.endpoint"].search([])))
        authentication = self.env["shop.api.endpoint"].search([
            ("code", "=", "customer_authenticate"),
        ], limit=1)
        self.assertEqual(authentication.path, "/api/v1/customers/authenticate")
        self.assertEqual(authentication.scope_id.code, "customer:read")
        self.assertFalse(authentication.idempotency_required)
        self.assertFalse(authentication.log_request_body)
        self.assertFalse(authentication.log_response_body)
        reset_request = self.env["shop.api.endpoint"].search([
            ("code", "=", "customer_password_reset_request"),
        ], limit=1)
        self.assertEqual(
            reset_request.path,
            "/api/v1/customers/password-reset/request",
        )
        self.assertEqual(reset_request.scope_id.code, "customer:write")
        self.assertTrue(reset_request.idempotency_required)
        self.assertFalse(reset_request.log_request_body)
        self.assertFalse(reset_request.log_response_body)
        password_change = self.env["shop.api.endpoint"].search([
            ("code", "=", "customer_password_change"),
        ], limit=1)
        self.assertEqual(
            password_change.path,
            "/api/v1/customers/password/change",
        )
        self.assertEqual(password_change.scope_id.code, "customer:write")
        self.assertTrue(password_change.idempotency_required)
        self.assertFalse(password_change.log_request_body)
        self.assertFalse(password_change.log_response_body)

    def test_encoded_password_cannot_be_imported_or_double_hashed(self):
        partner = self.env["res.partner"].create({"name": "Encoded Password Guard"})
        encoded = "$pbkdf2-sha512$600000$copied$credential"
        with self.assertRaisesRegex(ValidationError, "不能填写或导入加密后的密码"):
            self.env["res.users"].with_context(no_reset_password=True).create({
                "name": partner.name,
                "login": "encoded-password@example.test",
                "password": encoded,
                "partner_id": partner.id,
                "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
            })
        safe_user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": partner.name,
            "login": "plaintext-password@example.test",
            "password": "one-plain-input",
            "partner_id": partner.id,
            "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
        })
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [safe_user.id])
        stored_password = self.env.cr.fetchone()[0]
        self.assertNotEqual(stored_password, "one-plain-input")
        self.assertTrue(stored_password.startswith("$pbkdf2-sha512$"))

    def test_erp_password_change_verifies_old_and_new_plaintext_once(self):
        partner = self.env["res.partner"].create({
            "name": "Password Change Customer",
            "email": "password-change-customer@example.test",
        })
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": partner.name,
            "login": partner.email,
            "password": "old-password",
            "partner_id": partner.id,
            "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
        })
        self.assertTrue(user._shop_api_change_password(
            "old-password", "new-permanent-password",
        ))
        acting_user = user.with_user(user).sudo()
        acting_user._check_credentials({
            "type": "password",
            "login": user.login,
            "password": "new-permanent-password",
        }, {"interactive": True})
        with self.assertRaises(AccessDenied):
            acting_user._check_credentials({
                "type": "password",
                "login": user.login,
                "password": "old-password",
            }, {"interactive": True})

    def test_password_fields_are_always_redacted_from_api_audit_payloads(self):
        self.assertEqual(redact_payload({
            "login": "customer@example.test",
            "password": "one",
            "current_password": "two",
            "new_password": "three",
            "nested": {"temporary_password": "four"},
        }), {
            "login": "customer@example.test",
            "password": "***",
            "current_password": "***",
            "new_password": "***",
            "nested": {"temporary_password": "***"},
        })

    def test_payment_return_origins_support_parallel_migration_hosts(self):
        configuration = self.env["shop.api.configuration"]._ensure_default_configuration()
        configuration.write({
            "shop_base_url": "http://127.0.0.1:8070/shop",
            "allowed_shop_return_origins": "http://127.0.0.1:8071\nhttps://shop.example.test/path",
        })
        self.assertEqual(configuration.payment_return_origins(), {
            "http://127.0.0.1:8070",
            "http://127.0.0.1:8071",
            "https://shop.example.test",
        })

    def test_shop_configuration_exposes_payment_return_contract(self):
        configuration = self.env["shop.api.configuration"]._ensure_default_configuration()
        configuration.write({
            "shop_base_url": "https://shop.example.test/store",
            "allowed_shop_return_origins": "https://preview.example.test/path",
        })
        self.assertEqual(configuration.payment_return_origins(), {
            "https://shop.example.test",
            "https://preview.example.test",
        })

    def test_api_clients_keep_separate_public_shop_urls(self):
        self.client.shop_base_url = "https://cn-shop.example.test"
        self.assertEqual(self.client.shop_base_url, "https://cn-shop.example.test")

    def test_private_order_events_are_scoped_to_the_originating_shop(self):
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "pricelist_id": self.chinese_pricelist.id,
        })
        self.env["shop.api.external.reference"].set_reference(
            self.client, "order", "private-order-routing", order,
        )

        order.action_confirm()

        event = self.env["shop.api.event"].search([
            ("event_type", "=", "order.confirmed"),
            ("resource_id", "=", order.id),
        ], order="id desc", limit=1)
        self.assertTrue(event)
        self.assertEqual(event.client_id, self.client)

    def test_chinese_console_actions_views_and_key_wizard_are_available(self):
        action_ids = [
            "shop_api.action_shop_api_configurations",
            "shop_api.action_shop_api_clients",
            "shop_api.action_shop_api_endpoints",
            "shop_api.action_shop_api_webhooks",
            "shop_api.action_shop_api_reservations",
            "shop_api.action_shop_api_request_logs",
            "shop_api.action_shop_api_events",
            "shop_api.action_shop_api_deliveries",
            "shop_api.action_shop_api_checkpoints",
            "shop_api.action_shop_api_reconciliations",
        ]
        for xml_id in action_ids:
            action = self.env.ref(xml_id)
            self.assertTrue(action.exists(), xml_id)
            self.assertTrue(action.name)

        self.assertIn(
            self.env.ref("base.user_admin"),
            self.env.ref("shop_api.group_shop_api_admin").all_user_ids,
        )
        wizard_action = self.client.action_open_key_wizard()
        wizard = self.env["shop.api.key.wizard"].browse(wizard_action["res_id"])
        result = wizard.action_generate()
        self.assertTrue(wizard.generated)
        self.assertTrue(wizard.generated_key)
        self.assertEqual(result["res_id"], wizard.id)

    def test_stable_uuid_and_product_payload_are_json_serializable(self):
        template = self.product.product_tmpl_id
        original_uuid = template.shop_api_uuid
        payload = template._shop_api_payload(language="en_US", detail=True)

        self.assertTrue(original_uuid)
        self.assertEqual(payload["id"], original_uuid)
        self.assertEqual(payload["name"], "Shop API Test Product")
        json.dumps(payload, ensure_ascii=False)

        template.name = "Shop API Test Product Updated"
        self.assertEqual(template.shop_api_uuid, original_uuid)

    def test_product_payload_language_is_independent_of_request_context(self):
        template = self.product.product_tmpl_id
        template.flush_recordset(["name"])
        self.env.cr.execute(
            "UPDATE product_template SET name = %s::jsonb WHERE id = %s",
            [json.dumps({"zh_CN": "\u4e2d\u6587\u4ea7\u54c1", "en_US": "English Product"}), template.id],
        )
        template.invalidate_recordset(["name"])
        payload = template.with_context(lang="en_US")._shop_api_payload(
            language="zh_CN", detail=False,
        )
        self.assertEqual(payload["name"], "\u4e2d\u6587\u4ea7\u54c1")
        self.assertEqual(payload["name_zh"], "\u4e2d\u6587\u4ea7\u54c1")

    def test_same_name_group_uses_available_english_website_name(self):
        products = self.env["product.template"].create([
            {
                "name": "\u6d4b\u8bd5\u5355\u677f\u6ed1\u96ea\u978b\u5206\u7ec4",
                "default_code": "012307S1-MA007-H001250",
                "sale_ok": True,
                "website_published": True,
                "x_website_english_name": "Test Snowboard Boots Group",
            },
            {
                "name": "\u6d4b\u8bd5\u5355\u677f\u6ed1\u96ea\u978b\u5206\u7ec4",
                "default_code": "012307S1-MA010-W001250",
                "sale_ok": True,
                "website_published": True,
            },
        ])

        payload = products[1]._shop_api_payload(language="en_US", detail=True)

        self.assertEqual(payload["name"], "Test Snowboard Boots Group")
        self.assertEqual(payload["name_en"], "Test Snowboard Boots Group")
        self.assertEqual(
            {row["name"] for row in payload["group_variants"]},
            {"Test Snowboard Boots Group"},
        )

    def test_product_and_gallery_changes_enter_one_deduplicated_sync_set(self):
        template = self.product.product_tmpl_id
        Pending = self.env["shop.api.product.sync.pending"]
        Pending.search([]).unlink()
        template.write({"list_price": 123.0})
        image = self.env["product.image"].create({
            "name": "Gallery test",
            "product_tmpl_id": template.id,
        })
        image.write({"name": "Gallery test updated"})
        image.unlink()
        pending = Pending.search([("product_tmpl_id", "=", template.id)])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending.product_uuid, template.shop_api_uuid)

    def test_product_sync_set_flush_embeds_current_bilingual_snapshots_and_clears(self):
        template = self.product.product_tmpl_id
        Pending = self.env["shop.api.product.sync.pending"]
        Pending.search([]).unlink()
        template.write({
            "name": "ERP current product",
            "x_website_english_name": "ERP current product EN",
            "list_price": 321.0,
            "x_shop_group_cover": True,
        })
        before = self.env["shop.api.event"].search_count([
            ("resource_uuid", "=", template.shop_api_uuid),
            ("event_type", "=", "product.updated"),
        ])

        result = Pending._flush_pending_products(dispatch=False)

        self.assertEqual(result["products"], 1)
        self.assertFalse(Pending.search_count([("product_tmpl_id", "=", template.id)]))
        event = self.env["shop.api.event"].search([
            ("resource_uuid", "=", template.shop_api_uuid),
            ("event_type", "=", "product.updated"),
        ], order="id desc", limit=1)
        self.assertEqual(self.env["shop.api.event"].search_count([
            ("resource_uuid", "=", template.shop_api_uuid),
            ("event_type", "=", "product.updated"),
        ]), before + 1)
        self.assertTrue(event.payload["authoritative"])
        self.assertTrue(event.payload["replace"])
        self.assertEqual(event.payload["snapshots"]["zh_CN"]["price_cny"], 321.0)
        self.assertEqual(
            event.payload["snapshots"]["en_US"]["name_en"],
            "ERP current product EN",
        )
        self.assertEqual(
            event.payload["snapshots"]["zh_CN"]["available_quantity"],
            5.0,
        )
        self.assertTrue(event.payload["snapshots"]["zh_CN"]["group_cover"])
        self.assertTrue(event.payload["snapshots"]["zh_CN"]["inventory_version"])

    def test_manual_product_push_includes_same_name_group(self):
        Pending = self.env["shop.api.product.sync.pending"]
        products = self.env["product.template"].create([
            {
                "name": "Manual grouped cover push",
                "website_published": True,
                "sale_ok": True,
            },
            {
                "name": "Manual grouped cover push",
                "website_published": True,
                "sale_ok": True,
            },
        ])
        Pending.search([]).unlink()

        products[:1].action_push_updates_to_shop()

        self.assertEqual(self.env["shop.api.event"].search_count([
            ("resource_uuid", "in", products.mapped("shop_api_uuid")),
            ("event_type", "=", "product.updated"),
        ]), 2)

    def test_category_and_attribute_display_changes_queue_related_product(self):
        template = self.product.product_tmpl_id
        Pending = self.env["shop.api.product.sync.pending"]
        attribute = self.env["product.attribute"].create({"name": "同步规格"})
        value = self.env["product.attribute.value"].create({
            "name": "同步值", "attribute_id": attribute.id,
        })
        self.env["product.template.attribute.line"].create({
            "product_tmpl_id": template.id,
            "attribute_id": attribute.id,
            "value_ids": [Command.set(value.ids)],
        })
        Pending.search([("product_tmpl_id", "=", template.id)]).unlink()

        template.categ_id.write({"name": "同步后的类别"})
        attribute.write({"name": "同步后的规格"})
        value.write({"name": "同步后的值"})

        self.assertEqual(Pending.search_count([("product_tmpl_id", "=", template.id)]), 1)

    def test_force_product_sync_button_flushes_the_whole_pending_set(self):
        Pending = self.env["shop.api.product.sync.pending"]
        products = self.env["product.template"].create([
            {"name": "Force sync A", "website_published": True},
            {"name": "Force sync B", "website_published": True},
        ])
        self.assertEqual(Pending.search_count([("product_tmpl_id", "in", products.ids)]), 2)

        action = products.action_push_updates_to_shop()

        self.assertEqual(action["tag"], "display_notification")
        self.assertFalse(Pending.search_count([("product_tmpl_id", "in", products.ids)]))
        self.assertEqual(self.env["shop.api.event"].search_count([
            ("resource_uuid", "in", products.mapped("shop_api_uuid")),
            ("event_type", "=", "product.updated"),
        ]), 2)

    def test_product_push_is_available_as_template_and_variant_group_actions(self):
        for xml_id, model_name in (
            ("shop_api.action_product_template_push_updates_to_shop", "product.template"),
            ("shop_api.action_product_variant_push_updates_to_shop", "product.product"),
        ):
            action = self.env.ref(xml_id)
            self.assertEqual(action.model_id.model, model_name)
            self.assertEqual(action.binding_model_id.model, model_name)
            self.assertEqual(action.binding_view_types, "list")
            self.assertIn("action_push_updates_to_shop", action.code)

    def test_stock_quantity_change_emits_inventory_update_for_finished_product(self):
        Event = self.env["shop.api.event"]
        before = Event.search_count([
            ("event_type", "=", "inventory.updated"),
            ("resource_uuid", "=", self.product.shop_api_uuid),
        ])

        self.env["stock.quant"]._update_available_quantity(
            self.product, self.bin_location, 1.0,
        )

        event = Event.search([
            ("event_type", "=", "inventory.updated"),
            ("resource_uuid", "=", self.product.shop_api_uuid),
        ], order="id desc", limit=1)
        self.assertEqual(Event.search_count([
            ("event_type", "=", "inventory.updated"),
            ("resource_uuid", "=", self.product.shop_api_uuid),
        ]), before + 1)
        self.assertEqual(event.payload["product_id"], self.product.shop_api_uuid)
        self.assertEqual(event.payload["available_quantity"], 6.0)

    def test_unpublished_product_still_has_integration_payload(self):
        template = self.product.product_tmpl_id
        template.website_published = False
        payload = template._shop_api_payload(language="en_US", detail=True)
        self.assertEqual(payload["id"], template.shop_api_uuid)
        self.assertFalse(payload["published"])

    def test_inventory_snapshot_is_compact_and_subtracts_active_api_reservations(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "snapshot-reservation",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 2}],
        })
        snapshot = self.env["product.template"]._shop_api_inventory_snapshot()
        product_row = next(
            row for row in snapshot["products"]
            if row["id"] == self.product.product_tmpl_id.shop_api_uuid
        )
        variant_row = next(
            row for row in product_row["variants"]
            if row["id"] == self.product.shop_api_uuid
        )
        self.assertEqual(product_row["available_quantity"], 3.0)
        self.assertEqual(variant_row["available_quantity"], 3.0)
        self.assertTrue(product_row["available"])
        self.assertNotIn("name", product_row)
        reservation.action_release()

    def test_multi_create_assigns_distinct_public_uuids(self):
        templates = self.env["product.template"].create([
            {"name": "Shop API UUID Batch A"},
            {"name": "Shop API UUID Batch B"},
        ])
        self.assertTrue(all(templates.mapped("shop_api_uuid")))
        self.assertEqual(len(set(templates.mapped("shop_api_uuid"))), 2)

    def test_idempotency_replays_and_rejects_changed_body(self):
        Idempotency = self.env["shop.api.idempotency"]
        record, replay = Idempotency.begin(
            self.client, "same-key", "POST", "/api/v1/reservations", {"quantity": 1},
        )
        self.assertFalse(replay)
        record.complete(201, {"data": {"id": "one"}})

        repeated, replay = Idempotency.begin(
            self.client, "same-key", "POST", "/api/v1/reservations", {"quantity": 1},
        )
        self.assertTrue(replay)
        self.assertEqual(repeated.response_status, 201)

        with self.assertRaises(ValidationError):
            Idempotency.begin(
                self.client, "same-key", "POST", "/api/v1/reservations", {"quantity": 2},
            )

    def test_api_reservation_blocks_existing_odoo_website_checkout(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "cart-hold-1",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 4}],
        })
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "order_line": [Command.create({
                "product_id": self.product.id,
                "product_uom_qty": 2,
                "product_uom_id": self.product.uom_id.id,
            })],
        })
        order.order_line.x_source_location_id = False

        with self.assertRaises(UserError):
            order._prepare_website_stock_for_payment()

        reservation.action_release()
        order._prepare_website_stock_for_payment()
        self.assertEqual(order.order_line.x_source_location_id, self.bin_location)

    def test_reservation_creates_draft_order_and_external_mapping(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "cart-to-order",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 2}],
        })

        order = reservation.create_order(
            self.customer, "SHOP-ORDER-1001", language="en_US",
        )

        self.assertEqual(reservation.state, "confirmed")
        self.assertEqual(reservation.confirmed_order_id, order)
        self.assertEqual(order.state, "draft")
        self.assertEqual(order.x_channel, self.client.code)
        self.assertEqual(order.currency_id.name, "USD")
        self.assertEqual(order.order_line.price_unit, 100.0)
        self.assertEqual(order.order_line.x_source_location_id, self.bin_location)
        reference = self.env["shop.api.external.reference"].search([
            ("client_id", "=", self.client.id),
            ("resource_type", "=", "order"),
            ("external_id", "=", "SHOP-ORDER-1001"),
        ])
        self.assertEqual(reference.resolve(), order)

    def test_remote_order_uses_shipping_address_and_authoritative_carrier_price(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "cart-with-shipping",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 1}],
        })
        address = self.env["res.partner"].create({
            "name": "Shop API Delivery Address",
            "parent_id": self.customer.id,
            "type": "delivery",
            "street": "Test Street",
        })
        delivery_product = self.env["product.product"].create({
            "name": "Shop API Test Delivery",
            "type": "service",
        })
        carrier = self.env["delivery.carrier"].create({
            "name": "Shop API Fixed Delivery",
            "delivery_type": "fixed",
            "fixed_price": 12.0,
            "product_id": delivery_product.id,
        })

        order = reservation.create_order(
            self.customer, "SHOP-ORDER-SHIPPING", language="en_US",
            shipping_address=address, shipping_method=carrier,
        )

        self.assertEqual(order.partner_shipping_id, address)
        self.assertEqual(order.carrier_id, carrier)
        self.assertEqual(order.order_line.filtered("is_delivery").price_unit, 12.0)
        self.assertEqual(order.amount_total, 112.0)

    def test_payment_payload_is_authoritative_and_bound_to_order(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "payment-payload-reservation",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 1}],
        })
        order = reservation.create_order(
            self.customer, "SHOP-ORDER-PAYMENT-PAYLOAD", language="zh_CN",
        )
        provider = self.env.ref("payment.payment_provider_transfer")
        transaction = self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": self.env.ref("payment.payment_method_bank_transfer").id,
            "reference": "SHOP-API-AUTHORITY",
            "amount": order.amount_total,
            "currency_id": order.currency_id.id,
            "partner_id": order.partner_id.id,
            "operation": "online_redirect",
            "sale_order_ids": [Command.set(order.ids)],
        })
        payload = transaction._shop_api_payload()
        self.assertTrue(payload["authoritative"])
        self.assertEqual(payload["order_ids"], [order.shop_api_uuid])
        self.assertFalse(payload["post_processed"])

    def test_alipay_payment_payload_contains_shop_rendering_fields(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "alipay-payload-reservation",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 1}],
        })
        order = reservation.create_order(
            self.customer, "SHOP-ORDER-ALIPAY-PAYLOAD", language="zh_CN",
        )
        provider = self.env.ref("payment_alipay.payment_provider_alipay")
        provider.write({"state": "test", "alipay_simulation_mode": True})
        transaction = self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": provider.payment_method_ids[:1].id,
            "reference": "SHOP-API-ALIPAY",
            "amount": order.amount_total,
            "currency_id": order.currency_id.id,
            "partner_id": order.partner_id.id,
            "operation": "online_redirect",
            "sale_order_ids": [Command.set(order.ids)],
        })
        transaction._get_processing_values()
        payload = transaction._shop_api_payload()
        self.assertEqual(payload["provider"], "alipay")
        self.assertTrue(payload["simulation_mode"])
        self.assertIn("qr_code_data_uri", payload)

    def test_refund_request_payload_is_authoritative_and_enqueues_event(self):
        self.product.default_code = "012307S1-MA007-W001255"
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "x_channel": self.client.code,
            "x_website_checkout_language": "en_US",
            "order_line": [Command.create({
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "product_uom_id": self.product.uom_id.id,
                "price_unit": 100,
            })],
        })
        self.env["shop.api.external.reference"].set_reference(
            self.client, "order", "refund-event-origin", order,
        )
        provider = self.env.ref("payment.payment_provider_transfer")
        transaction = self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": self.env.ref("payment.payment_method_bank_transfer").id,
            "reference": "SHOP-API-REFUND-EVENT",
            "amount": order.amount_total,
            "currency_id": order.currency_id.id,
            "partner_id": order.partner_id.id,
            "operation": "online_redirect",
            "sale_order_ids": [Command.set(order.ids)],
        })

        refund_request = self.env[
            "stock.subwarehouse.website.refund.request"
        ].create({
            "order_id": order.id,
            "source_transaction_id": transaction.id,
            "line_ids": [Command.create({
                "sale_line_id": order.order_line.id,
                "quantity": 1,
            })],
        })

        payload = refund_request._shop_api_payload()
        self.assertTrue(payload["authoritative"])
        self.assertEqual(payload["order_id"], order.shop_api_uuid)
        expected_options = [
            {"key": "type", "label": "Type", "value": "S1"},
            {"key": "size", "label": "Size", "value": "255"},
            {"key": "flex", "label": "Flex", "value": "7 flex"},
        ]
        self.assertEqual(order._shop_api_payload()["items"][0]["selected_options"], expected_options)
        self.assertEqual(payload["items"][0]["selected_options"], expected_options)
        chinese_payload = order._shop_api_payload(language="zh_CN")
        self.assertEqual(
            [option["label"] for option in chinese_payload["items"][0]["selected_options"]],
            ["类型", "尺码", "硬度"],
        )
        self.assertNotIn("color", {option["key"] for option in expected_options})
        event = self.env["shop.api.event"].search([
            ("event_type", "=", "refund.requested"),
            ("resource_uuid", "=", refund_request.shop_api_uuid),
        ], order="id desc", limit=1)
        self.assertTrue(event)
        self.assertEqual(event.client_id, self.client)
        self.assertEqual(event.payload["review_state"], "requested")

    def test_expired_reservation_releases_stock_and_creates_event(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "expiring-cart",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 1}],
        })
        reservation.expires_at = fields.Datetime.now() - timedelta(seconds=1)

        self.env["shop.api.reservation"]._cron_expire_reservations()

        self.assertEqual(reservation.state, "expired")
        event = self.env["shop.api.event"].search([
            ("event_type", "=", "reservation.expired"),
            ("client_id", "=", self.client.id),
        ], order="id desc", limit=1)
        self.assertTrue(event)
        self.assertEqual(event.payload["id"], reservation.name)

    def test_confirmed_unpaid_order_expires_without_renewing_its_hold(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "expiring-confirmed-order",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 1}],
        })
        order = reservation.create_order(
            self.customer, "SHOP-ORDER-EXPIRING", language="zh_CN",
        )
        original_deadline = order.x_website_stock_reserved_until

        order._prepare_website_stock_for_payment()

        self.assertEqual(order.x_website_stock_reserved_until, original_deadline)
        expired_at = fields.Datetime.now() - timedelta(seconds=1)
        reservation.expires_at = expired_at
        order.order_line.filtered("is_storable").write({
            "x_website_stock_reserved_until": expired_at,
        })

        self.env["shop.api.reservation"]._cron_expire_reservations()
        order.invalidate_recordset()

        self.assertEqual(reservation.state, "expired")
        self.assertEqual(order.state, "cancel")
        self.assertEqual(order.x_website_payment_state, "expired")
        payload = order._shop_api_payload()
        self.assertTrue(payload["authoritative"])
        self.assertTrue(payload["payment_expired"])
        self.assertEqual(payload["payment_state"], "expired")
        self.assertEqual(payload["delivery_state"], "")
        self.assertTrue(self.env["shop.api.event"].search_count([
            ("event_type", "=", "order.expired"),
            ("resource_uuid", "=", order.shop_api_uuid),
        ]))

    def test_paid_order_enters_fifo_delivery_queue_and_dispatch_deducts_stock(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "paid-delivery-queue",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 1}],
        })
        order = reservation.create_order(
            self.customer, "SHOP-ORDER-DELIVERY", language="zh_CN",
        )
        provider = self.env.ref("payment.payment_provider_transfer")
        transaction = self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": self.env.ref("payment.payment_method_bank_transfer").id,
            "reference": "SHOP-API-DELIVERY-PAID",
            "amount": order.amount_total,
            "currency_id": order.currency_id.id,
            "partner_id": order.partner_id.id,
            "operation": "online_redirect",
            "sale_order_ids": [Command.set(order.ids)],
        })

        transaction._set_done()

        self.assertEqual(order.x_website_delivery_state, "awaiting_delivery")
        self.assertEqual(order.x_pending_website_delivery_count, 1)
        self.assertTrue(order.activity_ids.filtered(
            lambda activity: activity.summary == "已支付订单待发货"
        ))
        before_dispatch = self.env["stock.quant"]._get_available_quantity(
            self.product, self.bin_location, strict=True,
        )

        order.action_start_website_delivery()

        self.assertEqual(order.x_website_delivery_state, "delivering")
        self.assertEqual(order.x_pending_website_delivery_count, 0)
        self.assertEqual(order.picking_ids.filtered(
            lambda picking: picking.picking_type_code == "outgoing"
        ).state, "done")
        self.assertEqual(
            self.env["stock.quant"]._get_available_quantity(
                self.product, self.bin_location, strict=True,
            ),
            before_dispatch - 1,
        )
        order.action_mark_website_delivery_delivered()
        self.assertEqual(order.x_website_delivery_state, "delivered")
        self.assertTrue(order.x_website_delivered_at)

    def test_stage_three_site_and_checkout_catalog_is_registered(self):
        endpoint_codes = set(self.env["shop.api.endpoint"].search([]).mapped("code"))
        self.assertTrue({
            "site_configuration", "site_navigation", "site_pages", "site_page_detail",
            "site_legacy_routes", "checkout_quote", "customer_orders", "customer_refunds",
        }.issubset(endpoint_codes))
        event_codes = set(self.env["shop.api.event.type"].search([]).mapped("code"))
        self.assertIn("site.page.updated", event_codes)


@tagged("post_install", "-at_install")
class TestShopApiHttp(ShopApiTestMixin, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_shop_api_data()
        cls.api_key = cls.client.generate_api_key(name="HTTP test key")

    def _headers(self, idempotency_key=None):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def test_health_and_authenticated_capabilities(self):
        health = self.url_open("/api/v1/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["data"]["status"], "ok")

        capabilities = self.url_open(
            "/api/v1/capabilities", headers=self._headers(),
        )
        self.assertEqual(capabilities.status_code, 200)
        self.assertIn("reservations", capabilities.json()["data"]["features"])

    def test_reservation_endpoint_requires_and_replays_idempotency_key(self):
        payload = json.dumps({
            "external_id": "http-cart-1",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 1}],
        })
        missing = self.url_open(
            "/api/v1/reservations", data=payload, headers=self._headers(),
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"]["code"], "idempotency_key_required")

        created = self.url_open(
            "/api/v1/reservations", data=payload,
            headers=self._headers("http-reservation-key"),
        )
        repeated = self.url_open(
            "/api/v1/reservations", data=payload,
            headers=self._headers("http-reservation-key"),
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(repeated.status_code, 201)
        self.assertEqual(created.json()["data"]["id"], repeated.json()["data"]["id"])

    def test_checkout_quote_is_authoritative_and_idempotent(self):
        payload = json.dumps({
            "external_id": "http-quote-1",
            "language": "zh_CN",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 1}],
        })
        created = self.url_open(
            "/api/v1/checkout/quote", data=payload,
            headers=self._headers("http-quote-key"),
        )
        replayed = self.url_open(
            "/api/v1/checkout/quote", data=payload,
            headers=self._headers("http-quote-key"),
        )
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["data"]["authoritative"])
        self.assertEqual(created.json()["data"]["amount_total"], 100.0)
        self.assertEqual(created.json()["data"]["id"], replayed.json()["data"]["id"])

    def test_localized_site_configuration_endpoint(self):
        response = self.url_open(
            "/api/v1/site/configuration?language=en_US", headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["language"], "en_US")
        self.assertEqual(
            response.json()["data"]["legacy_redirects"]["/my/orders"],
            "/purchase-history",
        )

    def test_inventory_snapshot_endpoint_returns_positive_stock(self):
        response = self.url_open(
            "/api/v1/inventory/snapshot", headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]["products"]
        product_row = next(
            row for row in rows if row["id"] == self.product.product_tmpl_id.shop_api_uuid
        )
        self.assertEqual(product_row["available_quantity"], 5.0)
        self.assertTrue(product_row["available"])

    def test_internal_order_list_endpoint_returns_client_orders(self):
        reservation = self.env["shop.api.reservation"].create_reservation(self.client, {
            "external_id": "http-order-list-reservation",
            "items": [{"product_id": self.product.shop_api_uuid, "quantity": 1}],
        })
        order = reservation.create_order(
            self.customer, "HTTP-ORDER-LIST", language="zh_CN",
        )
        response = self.url_open(
            "/api/v1/orders?page_size=100", headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(order.shop_api_uuid, {
            item["id"] for item in response.json()["data"]
        })

    def test_payment_methods_are_api_readable_and_unique_by_provider_code(self):
        response = self.url_open(
            "/api/v1/payment-methods?lang=en_US", headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        methods = response.json()["data"]
        codes = [method["code"] for method in methods]
        self.assertEqual(len(codes), len(set(codes)))
        bank_card = next(method for method in methods if method["code"] == "bank_card")
        self.assertEqual(bank_card["name"], "Bank card (coming soon)")
        self.assertFalse(bank_card["available"])
        self.assertTrue(bank_card["shell"])
        self.assertNotIn("custom", codes)
        self.assertNotIn("Cash on Delivery", {method["name"] for method in methods})

    def test_customer_registration_is_authoritative_and_idempotent(self):
        payload = json.dumps({
            "name": "API Signup Customer",
            "email": "api-signup-customer@example.test",
            "password": "safe-test-password",
            "language": "en_US",
        })
        created = self.url_open(
            "/api/v1/customers/register", data=payload,
            headers=self._headers("http-customer-register-key"),
        )
        replayed = self.url_open(
            "/api/v1/customers/register", data=payload,
            headers=self._headers("http-customer-register-key"),
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(replayed.status_code, 201)
        self.assertTrue(created.json()["data"]["authoritative"])
        self.assertTrue(created.json()["data"]["registered"])
        self.assertEqual(created.json()["data"]["id"], replayed.json()["data"]["id"])
        user = self.env["res.users"].sudo().search([
            ("login", "=", "api-signup-customer@example.test"),
        ], limit=1)
        self.assertTrue(user)
        self.assertTrue(user.share)
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [user.id])
        stored_password = self.env.cr.fetchone()[0]
        self.assertNotEqual(stored_password, "safe-test-password")
        self.assertTrue(user._crypt_context().verify("safe-test-password", stored_password))

    def test_customer_password_change_rejects_encoded_new_password(self):
        response = self.url_open(
            "/api/v1/customers/password/change",
            data=json.dumps({
                "login": "admin",
                "current_password": "irrelevant",
                "new_password": "$pbkdf2-sha512$600000$copied$credential",
            }),
            headers=self._headers("http-password-change-encoded"),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "business_rule_failed")

    def test_customer_password_reset_is_authoritative(self):
        reset_partner = self.env["res.partner"].create({
            "name": "API Reset Customer",
            "email": "api-reset-customer@example.test",
        })
        self.env["res.users"].with_context(no_reset_password=True).create({
            "name": reset_partner.name,
            "login": reset_partner.email,
            "password": "old-safe-test-password",
            "partner_id": reset_partner.id,
            "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
        })
        user_model_class = type(self.env["res.users"])
        with patch.object(
            user_model_class,
            "action_reset_password",
            autospec=True,
            return_value={},
        ) as reset_mock:
            existing = self.url_open(
                "/api/v1/customers/password-reset/request",
                data=json.dumps({"login": reset_partner.email}),
                headers=self._headers("http-password-reset-existing"),
            )

        self.assertEqual(existing.status_code, 202)
        self.assertEqual(existing.json()["data"], {
            "authoritative": True,
            "accepted": True,
        })
        reset_mock.assert_called_once()

    def test_customer_password_reset_does_not_enumerate_unknown_account(self):
        unknown = self.url_open(
            "/api/v1/customers/password-reset/request",
            data=json.dumps({"login": "unknown-account@example.test"}),
            headers=self._headers("http-password-reset-unknown"),
        )

        self.assertEqual(unknown.status_code, 202)
        self.assertEqual(unknown.json()["data"], {
            "authoritative": True,
            "accepted": True,
        })
