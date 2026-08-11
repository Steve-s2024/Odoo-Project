import json
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.shop_api.models.api_catalog import BUILTIN_ENDPOINTS, BUILTIN_SCOPES


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

    def test_gallery_image_changes_enqueue_product_media_event(self):
        template = self.product.product_tmpl_id
        before = self.env["shop.api.event"].search_count([
            ("event_type", "=", "product.image.updated"),
            ("resource_uuid", "=", template.shop_api_uuid),
        ])
        image = self.env["product.image"].create({
            "name": "Gallery test",
            "product_tmpl_id": template.id,
        })
        self.assertEqual(self.env["shop.api.event"].search_count([
            ("event_type", "=", "product.image.updated"),
            ("resource_uuid", "=", template.shop_api_uuid),
        ]), before + 1)
        image.write({"name": "Gallery test updated"})
        self.assertEqual(self.env["shop.api.event"].search_count([
            ("event_type", "=", "product.image.updated"),
            ("resource_uuid", "=", template.shop_api_uuid),
        ]), before + 2)
        image.unlink()
        self.assertEqual(self.env["shop.api.event"].search_count([
            ("event_type", "=", "product.image.updated"),
            ("resource_uuid", "=", template.shop_api_uuid),
        ]), before + 3)

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
            "payment_method_id": provider.payment_method_ids[:1].id,
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
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "x_channel": self.client.code,
            "order_line": [Command.create({
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "product_uom_id": self.product.uom_id.id,
                "price_unit": 100,
            })],
        })
        provider = self.env.ref("payment.payment_provider_transfer")
        transaction = self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": provider.payment_method_ids[:1].id,
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
        event = self.env["shop.api.event"].search([
            ("event_type", "=", "refund.requested"),
            ("resource_uuid", "=", refund_request.shop_api_uuid),
        ], order="id desc", limit=1)
        self.assertTrue(event)
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
