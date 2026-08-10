import json
import hashlib
import hmac
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessDenied, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.storefront_api_bridge.models.api_client import StorefrontApiError
from odoo.addons.storefront_api_bridge.models import api_client as api_client_module
from odoo.addons.storefront_api_bridge.controllers.webhook import StorefrontWebhookController
from odoo.addons.storefront_api_bridge.controllers.customer_portal import StorefrontCustomerPortal
from odoo.addons.storefront_api_bridge.controllers import customer_portal as customer_portal_module


class _Response:
    def __init__(self, document):
        self.document = document

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.document).encode()


@tagged("post_install", "-at_install")
class TestStorefrontApiClient(TransactionCase):
    def test_internal_editor_purchase_history_renders_without_login_redirect(self):
        rendered = object()
        fake_request = SimpleNamespace(
            env=SimpleNamespace(user=self.env.user),
            lang=SimpleNamespace(code="zh_CN"),
            render=lambda template, values: (rendered, template, values),
        )
        with patch.object(customer_portal_module, "request", fake_request):
            result = StorefrontCustomerPortal.purchase_history.__wrapped__(
                StorefrontCustomerPortal()
            )
        self.assertIs(result[0], rendered)
        self.assertEqual(
            result[1], "storefront_api_bridge.remote_purchase_history_page",
        )
        self.assertEqual(result[2]["orders"], [])
        self.assertFalse(result[2]["portal_error"])

    def test_inventory_snapshot_is_cached_and_indexes_templates_and_variants(self):
        client = self.env["storefront.erp.client"]
        client.clear_inventory_snapshot_cache()
        self.env["storefront.cache.entry"].search([
            ("namespace", "=", "inventory"),
        ]).unlink()
        document = {
            "generated_at": "2026-08-08 00:00:00",
            "products": [{
                "id": "template-id",
                "available": True,
                "available_quantity": 4,
                "variants": [{
                    "id": "variant-id",
                    "available": True,
                    "available_quantity": 4,
                }],
            }],
        }
        with patch.dict("os.environ", {
            "STOREFRONT_ERP_BASE_URL": "https://erp.example.test",
            "STOREFRONT_ERP_API_KEY": "server-only-secret",
            "STOREFRONT_INVENTORY_CACHE_SECONDS": "30",
        }), patch.object(type(client), "get", return_value=document) as mocked:
            first = client.inventory_snapshot()
            second = client.inventory_snapshot()
        self.assertEqual(first["template-id"]["available_quantity"], 4.0)
        self.assertEqual(first["variant-id"]["available_quantity"], 4.0)
        self.assertIs(first, second)
        mocked.assert_called_once_with("/api/v1/inventory/snapshot")
        client.clear_inventory_snapshot_cache()

    def test_inventory_snapshot_uses_postgresql_cache_after_memory_expiry(self):
        client = self.env["storefront.erp.client"]
        client.clear_inventory_snapshot_cache()
        self.env["storefront.cache.entry"].search([
            ("namespace", "=", "inventory"),
        ]).unlink()
        document = {
            "products": [{
                "id": "template-stale",
                "available": True,
                "available_quantity": 2,
                "variants": [],
            }],
        }
        with patch.dict("os.environ", {
            "STOREFRONT_ERP_BASE_URL": "https://erp.example.test",
            "STOREFRONT_ERP_API_KEY": "server-only-secret",
            "STOREFRONT_INVENTORY_CACHE_SECONDS": "1",
            "STOREFRONT_INVENTORY_STALE_SECONDS": "300",
        }), patch.object(
            type(client), "get",
            side_effect=[document, StorefrontApiError("unexpected remote call", status=429)],
        ) as mocked:
            first = client.inventory_snapshot()
            for cached in api_client_module._INVENTORY_SNAPSHOT_CACHE.values():
                cached["fetched_at"] -= 2
            second = client.inventory_snapshot()
        self.assertEqual(first, second)
        self.assertEqual(second["template-stale"]["available_quantity"], 2.0)
        self.assertEqual(mocked.call_count, 1)
        client.clear_inventory_snapshot_cache()

    def test_webhook_signature_validation(self):
        raw = b'{"event_id":"event-1"}'
        secret = "webhook-test-secret"
        timestamp = "1000"
        signature = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256,
        ).hexdigest()
        self.assertTrue(StorefrontWebhookController._valid_signature(
            raw, timestamp, f"sha256={signature}", secret, now=1001,
        ))
        self.assertFalse(StorefrontWebhookController._valid_signature(
            raw, timestamp, f"sha256={signature}", secret, now=1401,
        ))
        self.assertFalse(StorefrontWebhookController._valid_signature(
            raw + b" ", timestamp, f"sha256={signature}", secret, now=1001,
        ))

    def test_outbox_event_queue_is_idempotent(self):
        document = {
            "event_id": "queue-event-1",
            "event_type": "order.created",
            "occurred_at": "2026-08-09 10:00:00",
            "resource_id": "order-1",
            "resource_version": "v1",
            "data": {"number": "SO001"},
        }
        queue = self.env["storefront.webhook.event"]
        first, first_created = queue.enqueue_document(document)
        second, second_created = queue.enqueue_document(document)
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first, second)
        self.assertEqual(first.state, "pending")

    def test_inventory_event_refreshes_postgresql_cache_asynchronously(self):
        queue = self.env["storefront.webhook.event"]
        event, _created = queue.enqueue_document({
            "event_id": "inventory-event-1",
            "event_type": "inventory.updated",
            "resource_id": "variant-1",
            "data": {"product_id": "variant-1"},
        })
        snapshot_map = {
            "variant-1": {"available": True, "available_quantity": 3.0},
        }
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.refresh_inventory_snapshot",
            return_value=snapshot_map,
        ) as mocked:
            queue._cron_process_pending()
        self.assertEqual(event.state, "done")
        mocked.assert_called_once()

    def test_full_catalog_refresh_replaces_cache_and_unpublishes_stale_rows(self):
        current = self.env["product.template"].create({
            "name": "Old cached name",
            "website_published": True,
            "sale_ok": True,
            "shop_api_uuid": "erp-product-id",
        })
        current.product_variant_id.shop_api_uuid = "erp-variant-id"
        stale = self.env["product.template"].create({
            "name": "Stale cached product",
            "website_published": True,
            "sale_ok": True,
            "shop_api_uuid": "stale-product-id",
        })
        self.env["storefront.cache.entry"].upsert(
            "product", "stale-product-id", {"name": "stale"}, language="zh_CN",
        )
        payload = {
            "id": "erp-product-id",
            "version": "v2",
            "name": "ERP product",
            "name_zh": "ERP 产品",
            "name_en": "ERP product",
            "description_zh": "中文描述",
            "description_en": "English description",
            "published": True,
            "sale_ok": True,
            "price_cny": 99,
            "price_usd": 15,
            "variants": [{
                "id": "erp-variant-id", "sku": "ERP-SKU", "name": "ERP product",
            }],
            "images": [],
        }

        def catalog_response(_client, method, path, payload=None, params=None, **_kwargs):
            self.assertEqual((method, path), ("GET", "/api/v1/products"))
            row = dict(payload_template)
            if params["language"] == "en_US":
                row["name"] = "ERP product"
            return [row], {"page": 1, "page_size": 100, "total": 1}

        payload_template = payload
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.call",
            autospec=True, side_effect=catalog_response,
        ), patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.refresh_inventory_snapshot",
            return_value={"erp-product-id": {"available": True}},
        ):
            result = self.env["storefront.catalog.sync"].full_refresh_from_erp()

        current.invalidate_recordset()
        stale.invalidate_recordset()
        self.assertEqual(result["products"], 1)
        self.assertEqual(current.with_context(lang="zh_CN").name, "ERP 产品")
        self.assertEqual(current.with_context(lang="en_US").name, "ERP product")
        self.assertEqual(current.default_code, "ERP-SKU")
        self.assertTrue(current.website_published)
        self.assertFalse(stale.website_published)
        self.assertFalse(self.env["storefront.cache.entry"].search_count([
            ("external_id", "=", "stale-product-id"),
        ]))

    def test_failed_event_is_not_automatically_retried(self):
        queue = self.env["storefront.webhook.event"]
        event, _created = queue.enqueue_document({
            "event_id": "inventory-event-error",
            "event_type": "inventory.updated",
            "resource_id": "variant-error",
            "data": {},
        })
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.refresh_inventory_snapshot",
            side_effect=StorefrontApiError("temporary", status=503),
        ) as mocked:
            queue._cron_process_pending()
            queue._cron_process_pending()
        self.assertEqual(event.state, "error")
        self.assertEqual(mocked.call_count, 1)

    def test_backend_routes_are_available_only_to_internal_users(self):
        ir_http = self.env["ir.http"]
        for path in (
            "/web/login", "/web/signup", "/web/reset_password",
            "/web/session/authenticate", "/web/session/change_password",
            "/web/session/logout",
        ):
            self.assertFalse(ir_http._is_blocked_storefront_path(path), path)

        for path in (
            "/web", "/web/", "/odoo", "/odoo/sales",
            "/web/dataset/call_kw", "/web/action/load",
            "/my/orders/123", "/my/invoices", "/my/invoices/123",
            "/my/quotes", "/shop/payment/receipt/123",
        ):
            self.assertTrue(ir_http._is_blocked_storefront_path(path), path)
            self.assertFalse(
                ir_http._is_blocked_storefront_path(path, internal_user=True), path,
            )

        for path in ("/web/database/manager", "/web/database/selector"):
            self.assertTrue(ir_http._is_blocked_storefront_path(path), path)
            self.assertTrue(
                ir_http._is_blocked_storefront_path(path, internal_user=True), path,
            )

    def test_bearer_key_remains_in_server_request_header(self):
        with patch.dict("os.environ", {
            "STOREFRONT_ERP_BASE_URL": "https://erp.example.test",
            "STOREFRONT_ERP_API_KEY": "server-only-secret",
        }), patch(
            "odoo.addons.storefront_api_bridge.models.api_client.urlrequest.urlopen",
            return_value=_Response({"data": {"status": "ok"}}),
        ) as mocked:
            result = self.env["storefront.erp.client"].get("/api/v1/health")
        self.assertEqual(result, {"status": "ok"})
        sent = mocked.call_args.args[0]
        self.assertEqual(sent.get_header("Authorization"), "Bearer server-only-secret")
        self.assertNotIn("server-only-secret", sent.full_url)

    def test_order_shortage_lines_use_remote_variant_identifiers(self):
        partner = self.env["res.partner"].create({"name": "Storefront test"})
        product = self.env["product.product"].create({
            "name": "Remote inventory product", "list_price": 10,
        })
        product.shop_api_uuid = "variant-remote-id"
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 2})],
        })
        order.x_storefront_shortage_product_uuids = ["variant-remote-id"]
        self.assertEqual(order._get_source_inventory_shortage_lines(), order.order_line)

    def _checkout_order(self):
        partner = self.env["res.partner"].create({
            "name": "Storefront checkout test",
            "email": "checkout@example.test",
            "street": "Test Street",
        })
        product = self.env["product.product"].create({
            "name": "Storefront checkout product", "list_price": 100,
        })
        product.shop_api_uuid = "variant-checkout-id"
        carrier_product = self.env["product.product"].create({
            "name": "Storefront delivery", "type": "service",
        })
        carrier = self.env["delivery.carrier"].create({
            "name": "Storefront fixed delivery",
            "delivery_type": "fixed",
            "fixed_price": 12,
            "product_id": carrier_product.id,
        })
        carrier.shop_api_uuid = "carrier-checkout-id"
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "partner_shipping_id": partner.id,
            "x_website_checkout_language": "en_US",
            "order_line": [(0, 0, {
                "product_id": product.id, "product_uom_qty": 1, "price_unit": 100,
            })],
        })
        order.set_delivery_line(carrier, 12)
        order.write({
            "x_storefront_reservation_id": "reservation-checkout-id",
            "x_storefront_reservation_expires_at": fields.Datetime.now() + timedelta(minutes=10),
            "x_storefront_quote_fingerprint": order._storefront_fingerprint(),
        })
        return order

    @staticmethod
    def _reservation_confirmation(order):
        return {
            "id": order.x_storefront_reservation_id,
            "authoritative": True,
            "state": "active",
            "expires_at": fields.Datetime.to_string(
                fields.Datetime.now() + timedelta(minutes=10)
            ),
            "items": order._storefront_api_items(),
        }

    def test_remote_order_handoff_includes_address_carrier_and_matching_total(self):
        order = self._checkout_order()
        responses = [
            {"id": "customer-id"},
            {"id": "address-id"},
            {
                "id": "order-id", "state": "draft",
                "currency": order.currency_id.name, "amount_total": order.amount_total,
            },
        ]
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            side_effect=responses,
        ) as mocked, patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value=self._reservation_confirmation(order),
        ):
            order._storefront_sync_remote_order()

        order_payload = mocked.call_args_list[2].args[1]
        self.assertEqual(order_payload["shipping_address_id"], "address-id")
        self.assertEqual(order_payload["shipping_method_id"], "carrier-checkout-id")
        self.assertEqual(order.x_storefront_remote_order_id, "order-id")

    def test_remote_order_total_mismatch_blocks_payment_handoff(self):
        order = self._checkout_order()
        responses = [
            {"id": "customer-id"},
            {"id": "address-id"},
            {
                "id": "order-id", "state": "draft",
                "currency": order.currency_id.name, "amount_total": order.amount_total + 1,
            },
        ]
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            side_effect=responses,
        ), patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value=self._reservation_confirmation(order),
        ), self.assertRaises(ValidationError):
            order._storefront_sync_remote_order()

    def test_existing_reservation_requires_current_authoritative_erp_confirmation(self):
        order = self._checkout_order()
        invalid = self._reservation_confirmation(order)
        invalid["authoritative"] = False
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value=invalid,
        ), self.assertRaises(StorefrontApiError):
            order._storefront_ensure_quote()

    def test_payment_initiation_rejects_non_authoritative_response(self):
        order = self._checkout_order()
        order.x_storefront_remote_order_id = "remote-order-id"
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            return_value={"payment": {"id": "payment-id", "state": "pending"}},
        ), patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value={"shop_base_url": "https://shop.example.test"},
        ), self.assertRaises(StorefrontApiError):
            order._storefront_create_payment("wechatpay")

    def test_payment_methods_use_checkout_language(self):
        order = self._checkout_order()
        methods = [{"code": "wechatpay", "currencies": [order.currency_id.name]}]
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value=methods,
        ) as mocked:
            self.assertEqual(order._storefront_payment_methods(), methods)
        mocked.assert_called_once_with(
            "/api/v1/payment-methods", params={"lang": "en_US"}
        )

    def test_payment_return_url_uses_erp_shop_configuration(self):
        order = self._checkout_order()
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value={"shop_base_url": "https://shop.example.test/"},
        ):
            self.assertEqual(
                order._storefront_payment_return_url(),
                "https://shop.example.test/shop/payment/status",
            )

    def test_product_grouping_uses_code_family_not_translated_name(self):
        first = self.env["product.template"].create({
            "name": "双板滑雪鞋",
            "default_code": "012307S2-MA100-H001260",
            "sale_ok": True,
        })
        second = self.env["product.template"].create({
            "name": "Ski Boots 100 flex",
            "default_code": "012307S2-MA100-BG01260",
            "sale_ok": True,
        })
        self.assertEqual(first._normalize_shop_group_name(), second._normalize_shop_group_name())
        self.assertEqual(len((first | second)._get_shop_grouped_products()), 1)

    def test_existing_internal_editor_can_use_erp_verified_password(self):
        editor_uuid = "erp-editor-id"
        editor_partner = self.env["res.partner"].create({
            "name": "Storefront Editor",
            "shop_api_uuid": editor_uuid,
        })
        editor = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Storefront Editor",
            "login": "storefront-disabled-editor",
            "partner_id": editor_partner.id,
            "group_ids": [Command.set([
                self.env.ref("base.group_user").id,
                self.env.ref("website.group_website_designer").id,
            ])],
        })
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            return_value={
                "id": editor_uuid,
                "authoritative": True,
                "login": "erp-editor@example.test",
                "website_editor": True,
                "is_internal": True,
            },
        ):
            auth_info = self.env["res.users"].authenticate({
                "type": "password",
                "login": "erp-editor@example.test",
                "password": "erp-only-password",
            }, {"interactive": True})
        self.assertEqual(auth_info["uid"], editor.id)
        self.assertTrue(editor._is_internal())

    def test_existing_portal_is_promoted_only_after_erp_editor_authorization(self):
        partner = self.env["res.partner"].create({
            "name": "Future Website Editor",
            "shop_api_uuid": "old-customer-id",
        })
        editor = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Future Website Editor",
            "login": "future-editor@example.test",
            "partner_id": partner.id,
            "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
        })
        self.assertFalse(editor._is_internal())

        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            return_value={
                "id": "erp-editor-id",
                "authoritative": True,
                "login": "future-editor@example.test",
                "website_editor": True,
                "is_internal": True,
            },
        ):
            auth_info = self.env["res.users"].authenticate({
                "type": "password",
                "login": "future-editor@example.test",
                "password": "erp-only-password",
            }, {"interactive": True})

        editor.invalidate_recordset()
        self.assertEqual(auth_info["uid"], editor.id)
        self.assertTrue(editor._is_internal())
        self.assertTrue(editor.has_group("website.group_website_designer"))
        self.assertEqual(editor.partner_id.shop_api_uuid, "erp-editor-id")
        self.assertEqual(editor.x_storefront_remote_customer_id, "erp-editor-id")

    def test_uuid_mapped_editor_takes_priority_over_existing_login_portal(self):
        remote_id = "mapped-erp-editor-id"
        editor_partner = self.env["res.partner"].create({
            "name": "Mapped Website Editor",
            "shop_api_uuid": remote_id,
        })
        mapped_editor = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Mapped Website Editor",
            "login": "storefront-disabled-mapped-editor",
            "partner_id": editor_partner.id,
            "group_ids": [Command.set([
                self.env.ref("base.group_user").id,
                self.env.ref("website.group_website_restricted_editor").id,
            ])],
        })
        portal_partner = self.env["res.partner"].create({"name": "Old Portal Copy"})
        portal = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Old Portal Copy",
            "login": "mapped-editor@example.test",
            "partner_id": portal_partner.id,
            "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
        })

        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            return_value={
                "id": remote_id,
                "authoritative": True,
                "login": "mapped-editor@example.test",
                "website_editor": True,
                "is_internal": True,
            },
        ):
            auth_info = self.env["res.users"].authenticate({
                "type": "password",
                "login": "mapped-editor@example.test",
                "password": "erp-only-password",
            }, {"interactive": True})

        self.assertEqual(auth_info["uid"], mapped_editor.id)
        self.assertTrue(mapped_editor._is_internal())
        self.assertFalse(portal._is_internal())

    def test_erp_customer_login_provisions_portal_without_copying_password(self):
        profile = {
            "id": "erp-customer-id",
            "authoritative": True,
            "login": "customer@example.test",
            "name": "ERP Customer",
            "email": "customer@example.test",
            "phone": "123",
            "language": "en_US",
            "addresses": [{
                "id": "erp-address-id",
                "type": "delivery",
                "name": "ERP Customer",
                "street": "Remote Street",
                "city": "Remote City",
                "zip": "100000",
                "country": "CN",
                "active": True,
            }],
        }
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            return_value=profile,
        ) as mocked:
            auth_info = self.env["res.users"].authenticate({
                "type": "password",
                "login": "customer@example.test",
                "password": "remote-password-only",
            }, {"interactive": True})

        user = self.env["res.users"].browse(auth_info["uid"])
        self.assertEqual(user.x_storefront_remote_customer_id, "erp-customer-id")
        self.assertTrue(user.share)
        self.assertFalse(user._is_internal())
        self.assertEqual(user.partner_id.shop_api_uuid, "erp-customer-id")
        self.assertEqual(user.partner_id.child_ids.shop_api_uuid, "erp-address-id")
        self.assertEqual(mocked.call_args.args[0], "/api/v1/customers/authenticate")

    def test_local_internal_password_is_not_an_erp_outage_fallback(self):
        partner = self.env["res.partner"].create({"name": "Local-only editor"})
        self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Local-only editor",
            "login": "local-only-editor@example.test",
            "password": "local-password",
            "partner_id": partner.id,
            "group_ids": [Command.set([
                self.env.ref("base.group_user").id,
                self.env.ref("website.group_website_designer").id,
            ])],
        })
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            side_effect=StorefrontApiError("ERP unavailable", status=503),
        ), self.assertRaises(AccessDenied):
            self.env["res.users"].authenticate({
                "type": "password",
                "login": "local-only-editor@example.test",
                "password": "local-password",
            }, {"interactive": True})

    def test_authenticated_customer_order_reuses_erp_customer_and_address(self):
        order = self._checkout_order()
        order.partner_id.shop_api_uuid = "erp-customer-id"
        portal = self.env.ref("base.group_portal")
        self.env["res.users"].with_context(no_reset_password=True).create({
            "login": "remote-checkout@example.test",
            "partner_id": order.partner_id.id,
            "x_storefront_remote_customer_id": "erp-customer-id",
            "group_ids": [Command.set([portal.id])],
        })
        remote_order = {
            "id": "order-id", "state": "draft",
            "currency": order.currency_id.name, "amount_total": order.amount_total,
        }
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            return_value=remote_order,
        ) as mocked, patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value=self._reservation_confirmation(order),
        ):
            order._storefront_sync_remote_order()

        self.assertEqual(mocked.call_count, 1)
        path, payload = mocked.call_args.args[:2]
        self.assertEqual(path, "/api/v1/orders")
        self.assertEqual(payload["customer_id"], "erp-customer-id")
        self.assertEqual(payload["shipping_address_id"], "erp-customer-id")

    def test_login_replaces_anonymous_remote_draft_with_customer_order(self):
        order = self._checkout_order()
        order.partner_id.shop_api_uuid = "erp-customer-id"
        portal = self.env.ref("base.group_portal")
        self.env["res.users"].with_context(no_reset_password=True).create({
            "login": "reassigned-checkout@example.test",
            "partner_id": order.partner_id.id,
            "x_storefront_remote_customer_id": "erp-customer-id",
            "group_ids": [Command.set([portal.id])],
        })
        order.write({
            "x_storefront_remote_customer_id": "anonymous-customer-id",
            "x_storefront_remote_order_id": "anonymous-order-id",
        })
        responses = [
            {"id": "anonymous-order-id", "state": "cancel"},
            {
                "reservation_id": "new-reservation-id",
                "authoritative": True,
                "expires_at": fields.Datetime.to_string(
                    fields.Datetime.now() + timedelta(minutes=10)
                ),
            },
            {
                "id": "customer-order-id", "state": "draft",
                "currency": order.currency_id.name, "amount_total": order.amount_total,
            },
        ]
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            side_effect=responses,
        ) as mocked:
            order._storefront_sync_remote_order()

        self.assertEqual(
            [call.args[0] for call in mocked.call_args_list],
            [
                "/api/v1/orders/anonymous-order-id/cancel",
                "/api/v1/checkout/quote",
                "/api/v1/orders",
            ],
        )
        self.assertEqual(order.x_storefront_remote_customer_id, "erp-customer-id")
        self.assertEqual(order.x_storefront_remote_order_id, "customer-order-id")
