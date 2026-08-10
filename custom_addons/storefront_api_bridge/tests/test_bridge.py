import json
import hashlib
import hmac
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.storefront_api_bridge.models.api_client import StorefrontApiError
from odoo.addons.storefront_api_bridge.models import api_client as api_client_module
from odoo.addons.storefront_api_bridge.controllers.webhook import StorefrontWebhookController


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
    def test_inventory_snapshot_is_cached_and_indexes_templates_and_variants(self):
        client = self.env["storefront.erp.client"]
        client.clear_inventory_snapshot_cache()
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
        ) as mocked:
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
        ), self.assertRaises(ValidationError):
            order._storefront_sync_remote_order()

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
        ) as mocked:
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
