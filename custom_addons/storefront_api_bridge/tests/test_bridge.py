import base64
import json
import hashlib
import hmac
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo import Command, fields
from odoo.exceptions import AccessDenied, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.storefront_api_bridge.models.api_client import StorefrontApiError
from odoo.addons.storefront_api_bridge.models import api_client as api_client_module
from odoo.addons.storefront_api_bridge.controllers.webhook import StorefrontWebhookController
from odoo.addons.storefront_api_bridge.controllers.customer_portal import StorefrontCustomerPortal
from odoo.addons.storefront_api_bridge.controllers import customer_portal as customer_portal_module
from odoo.addons.storefront_api_bridge.controllers.website_sale import StorefrontWebsiteSale
from odoo.addons.storefront_api_bridge.controllers import website_sale as website_sale_module
from odoo.addons.storefront_api_bridge.controllers.signup import StorefrontAuthSignup
from odoo.addons.storefront_api_bridge.controllers import signup as signup_module


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
        fake_client = MagicMock()
        fake_client.call.return_value = ([{
            "id": "erp-order-id", "number": "SO001",
        }], {"page": 1, "page_size": 100, "total": 1})
        fake_env = MagicMock()
        fake_env.user = self.env.user
        fake_env.__getitem__.return_value = fake_client
        fake_request = SimpleNamespace(
            env=fake_env,
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
        self.assertEqual(result[2]["orders"][0]["id"], "erp-order-id")
        self.assertFalse(result[2]["portal_error"])
        fake_client.call.assert_called_once_with(
            "GET", "/api/v1/orders", params={
                "page": 1, "page_size": 100, "language": "zh_CN",
            },
        )

    def test_internal_editor_can_render_purchase_detail_and_refund_items(self):
        order = {
            "id": "erp-order-id",
            "number": "SO001",
            "customer_id": "another-customer-id",
            "payment_state": "paid",
            "state": "sale",
            "items": [],
        }
        fake_client = MagicMock()
        fake_client.get.side_effect = [order, [], {}, order, []]
        fake_env = MagicMock()
        fake_env.user = self.env.user
        fake_env.__getitem__.return_value = fake_client
        rendered = []
        fake_request = SimpleNamespace(
            env=fake_env,
            lang=SimpleNamespace(code="en_US"),
            render=lambda template, values: rendered.append((template, values)) or template,
            redirect=lambda target: ("redirect", target),
        )
        controller = StorefrontCustomerPortal()
        with patch.object(customer_portal_module, "request", fake_request):
            detail = controller._render_remote_detail("erp-order-id")
            refund = controller._render_remote_refund("erp-order-id")

        self.assertEqual(
            detail, "storefront_api_bridge.remote_purchase_detail_page",
        )
        self.assertEqual(
            refund, "storefront_api_bridge.remote_refund_item_page",
        )
        self.assertEqual([row[0] for row in rendered], [
            "storefront_api_bridge.remote_purchase_detail_page",
            "storefront_api_bridge.remote_refund_item_page",
        ])
        self.assertEqual([entry.args[0] for entry in fake_client.get.call_args_list], [
            "/api/v1/orders/erp-order-id",
            "/api/v1/orders/erp-order-id/refund-requests",
            "/api/v1/orders/erp-order-id/documents",
            "/api/v1/orders/erp-order-id",
            "/api/v1/orders/erp-order-id/refund-requests",
        ])

    def test_external_customer_can_render_owned_purchase_detail(self):
        order = {
            "id": "erp-order-id",
            "number": "SO001",
            "customer_id": "erp-customer-id",
            "payment_state": "paid",
            "state": "sale",
            "items": [],
        }
        fake_client = MagicMock()
        fake_client.get.side_effect = [order, [], {}]
        fake_user = MagicMock()
        fake_user._is_public.return_value = False
        fake_user._is_internal.return_value = False
        fake_user.sudo.return_value.x_storefront_remote_customer_id = "erp-customer-id"
        fake_env = MagicMock()
        fake_env.user = fake_user
        fake_env.__getitem__.return_value = fake_client
        fake_request = SimpleNamespace(
            env=fake_env,
            lang=SimpleNamespace(code="en_US"),
            render=lambda template, values: (template, values),
            redirect=lambda target: ("redirect", target),
        )
        with patch.object(customer_portal_module, "request", fake_request):
            result = StorefrontCustomerPortal()._render_remote_detail("erp-order-id")

        self.assertEqual(
            result[0], "storefront_api_bridge.remote_purchase_detail_page",
        )
        self.assertEqual(result[1]["order"]["id"], "erp-order-id")

    def _signup_request(self, client):
        class Session(dict):
            def __init__(self):
                super().__init__()
                self.authenticate = MagicMock()

        session = Session()
        attempt_id = str(uuid.uuid4())
        session["storefront_signup_attempt_id"] = attempt_id
        user_model = MagicMock()
        fake_env = MagicMock()
        fake_env.__getitem__.side_effect = lambda model: {
            "storefront.erp.client": client,
            "res.users": user_model,
        }[model]
        return SimpleNamespace(env=fake_env, session=session), user_model, attempt_id

    def test_signup_waits_for_authoritative_create_and_readback(self):
        client = MagicMock()
        client.post.return_value = {
            "id": "erp-customer-id", "email": "new@example.com",
            "login": "new@example.com", "authoritative": True, "registered": True,
        }
        client.get.return_value = {
            "id": "erp-customer-id", "email": "new@example.com", "authoritative": True,
        }
        fake_request, user_model, attempt_id = self._signup_request(client)
        controller = StorefrontAuthSignup()
        controller._prepare_signup_values = MagicMock(return_value={
            "name": "New Customer", "login": "new@example.com",
            "password": "safe-password", "lang": "en_US",
        })
        with (
            patch.object(signup_module, "request", fake_request),
            patch.object(signup_module, "_erp_login_enabled", return_value=False),
        ):
            controller.do_signup({"signup_attempt_id": attempt_id})
        self.assertEqual(
            client.post.call_args.kwargs["idempotency_key"],
            f"storefront-signup-{attempt_id}",
        )
        client.get.assert_called_once_with("/api/v1/customers/erp-customer-id")
        user_model._storefront_provision_portal_user.assert_called_once()
        fake_request.session.authenticate.assert_called_once()
        self.assertNotIn("storefront_signup_attempt_id", fake_request.session)

    def test_signup_fails_closed_when_erp_times_out(self):
        client = MagicMock()
        client.post.side_effect = StorefrontApiError(
            "ERP unavailable", code="erp_unavailable", status=503,
        )
        fake_request, user_model, attempt_id = self._signup_request(client)
        controller = StorefrontAuthSignup()
        controller._prepare_signup_values = MagicMock(return_value={
            "name": "New Customer", "login": "new@example.com",
            "password": "safe-password", "lang": "en_US",
        })
        with (
            patch.object(signup_module, "request", fake_request),
            patch.object(signup_module, "_erp_login_enabled", return_value=False),
            patch.object(signup_module, "_", side_effect=lambda message: message),
            self.assertRaises(UserError),
        ):
            controller.do_signup({"signup_attempt_id": attempt_id})
        user_model._storefront_provision_portal_user.assert_not_called()
        self.assertEqual(fake_request.session["storefront_signup_attempt_id"], attempt_id)

    def test_signup_fails_closed_on_readback_mismatch(self):
        client = MagicMock()
        client.post.return_value = {
            "id": "erp-customer-id", "email": "new@example.com",
            "login": "new@example.com", "authoritative": True, "registered": True,
        }
        client.get.return_value = {
            "id": "different-id", "email": "new@example.com", "authoritative": True,
        }
        fake_request, user_model, attempt_id = self._signup_request(client)
        controller = StorefrontAuthSignup()
        controller._prepare_signup_values = MagicMock(return_value={
            "name": "New Customer", "login": "new@example.com",
            "password": "safe-password", "lang": "en_US",
        })
        with (
            patch.object(signup_module, "request", fake_request),
            patch.object(signup_module, "_erp_login_enabled", return_value=False),
            patch.object(signup_module, "_", side_effect=lambda message: message),
            self.assertRaises(UserError),
        ):
            controller.do_signup({"signup_attempt_id": attempt_id})
        user_model._storefront_provision_portal_user.assert_not_called()

    def test_signup_normalizes_email_and_derives_missing_display_name(self):
        client = MagicMock()
        client.post.return_value = {
            "id": "erp-customer-id", "email": "new@example.com",
            "login": "new@example.com", "authoritative": True, "registered": True,
        }
        client.get.return_value = {
            "id": "erp-customer-id", "email": "new@example.com", "authoritative": True,
        }
        fake_request, _user_model, attempt_id = self._signup_request(client)
        controller = StorefrontAuthSignup()
        controller._prepare_signup_values = MagicMock(return_value={
            "name": "   ", "login": " New@Example.COM ",
            "password": "safe-password", "lang": "en_US",
        })
        with (
            patch.object(signup_module, "request", fake_request),
            patch.object(signup_module, "_erp_login_enabled", return_value=False),
        ):
            controller.do_signup({"signup_attempt_id": attempt_id})
        payload = client.post.call_args.args[1]
        self.assertEqual(payload["login"], "new@example.com")
        self.assertEqual(payload["email"], "new@example.com")
        self.assertEqual(payload["name"], "new")

    def test_signup_rotates_attempt_after_definitive_erp_rejection(self):
        client = MagicMock()
        client.post.side_effect = StorefrontApiError(
            "Invalid registration", code="invalid_registration", status=400,
        )
        fake_request, _user_model, attempt_id = self._signup_request(client)
        controller = StorefrontAuthSignup()
        controller._prepare_signup_values = MagicMock(return_value={
            "name": "New Customer", "login": "new@example.com",
            "password": "safe-password", "lang": "en_US",
        })
        qcontext = {"signup_attempt_id": attempt_id}
        with (
            patch.object(signup_module, "request", fake_request),
            patch.object(signup_module, "_erp_login_enabled", return_value=False),
            patch.object(signup_module, "_", side_effect=lambda message: message),
            self.assertRaises(UserError),
        ):
            controller.do_signup(qcontext)
        self.assertNotEqual(qcontext["signup_attempt_id"], attempt_id)
        self.assertEqual(
            fake_request.session["storefront_signup_attempt_id"],
            qcontext["signup_attempt_id"],
        )

    def _refund_submission_request(self, client):
        fake_env = MagicMock()
        fake_env.user = self.env.user
        fake_env.__getitem__.return_value = client
        return SimpleNamespace(
            env=fake_env,
            session={},
            redirect=lambda target: ("redirect", target),
        )

    @staticmethod
    def _refund_remote_order():
        return {
            "id": "erp-order-id",
            "number": "SO001",
            "customer_id": "another-customer-id",
            "items": [{
                "product_id": "erp-product-id",
                "name": "Refund product",
                "quantity": 2.0,
                "refundable": True,
            }],
        }

    def test_refund_submission_requires_authoritative_erp_readback(self):
        attempt_id = str(uuid.uuid4())
        client = MagicMock()
        client.get.side_effect = [
            self._refund_remote_order(),
            {
                "id": "erp-refund-id",
                "order_id": "erp-order-id",
                "authoritative": True,
                "review_state": "requested",
                "items": [{"product_id": "erp-product-id", "quantity": 1.0}],
            },
        ]
        client.post.return_value = {
            "id": "erp-refund-id",
            "order_id": "erp-order-id",
            "authoritative": True,
        }
        fake_request = self._refund_submission_request(client)

        with patch.object(customer_portal_module, "request", fake_request):
            result = StorefrontCustomerPortal()._submit_remote_refund(
                "erp-order-id",
                {"quantity_erp-product-id": "1", "refund_attempt_id": attempt_id},
            )

        self.assertEqual(result, ("redirect", "/refund-item/erp-order-id"))
        self.assertEqual(fake_request.session["x_storefront_refund_flash"], "success")
        self.assertEqual(
            client.post.call_args.kwargs["idempotency_key"],
            f"portal-refund-erp-order-id-{attempt_id}",
        )
        client.get.assert_any_call("/api/v1/refund-requests/erp-refund-id")

    def test_refund_submission_fails_closed_when_erp_rejects(self):
        client = MagicMock()
        client.get.return_value = self._refund_remote_order()
        client.post.side_effect = StorefrontApiError(
            "ERP unavailable", code="erp_unavailable", status=503,
        )
        fake_request = self._refund_submission_request(client)

        with patch.object(customer_portal_module, "request", fake_request):
            result = StorefrontCustomerPortal()._submit_remote_refund(
                "erp-order-id",
                {
                    "quantity_erp-product-id": "1",
                    "refund_attempt_id": str(uuid.uuid4()),
                },
            )

        self.assertEqual(result, ("redirect", "/refund-item/erp-order-id"))
        self.assertEqual(fake_request.session["x_storefront_refund_flash"], "failure")

    def test_refund_submission_fails_closed_on_readback_mismatch(self):
        client = MagicMock()
        client.get.side_effect = [
            self._refund_remote_order(),
            {
                "id": "erp-refund-id",
                "order_id": "erp-order-id",
                "authoritative": True,
                "review_state": "requested",
                "items": [{"product_id": "erp-product-id", "quantity": "invalid"}],
            },
        ]
        client.post.return_value = {
            "id": "erp-refund-id",
            "order_id": "erp-order-id",
            "authoritative": True,
        }
        fake_request = self._refund_submission_request(client)

        with patch.object(customer_portal_module, "request", fake_request):
            StorefrontCustomerPortal()._submit_remote_refund(
                "erp-order-id",
                {
                    "quantity_erp-product-id": "1",
                    "refund_attempt_id": str(uuid.uuid4()),
                },
            )

        self.assertEqual(fake_request.session["x_storefront_refund_flash"], "failure")

    def test_remote_currency_symbols_are_customer_facing(self):
        controller = StorefrontCustomerPortal()
        self.assertEqual(controller._currency_symbol("CNY"), "￥")
        self.assertEqual(controller._currency_symbol("USD"), "$")
        self.assertEqual(controller._currency_symbol("EUR"), "EUR")

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
        self.assertEqual(event.state, "done", event.last_error)
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

    def test_failed_event_is_retried_with_backoff(self):
        queue = self.env["storefront.webhook.event"]
        event, _created = queue.enqueue_document({
            "event_id": "inventory-event-error",
            "event_type": "inventory.updated",
            "resource_id": "variant-error",
            "data": {},
        })
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.refresh_inventory_snapshot",
            side_effect=[StorefrontApiError("temporary", status=503), {"variant-error": {}}],
        ) as mocked:
            queue._cron_process_pending()
            self.assertEqual(event.state, "error")
            self.assertEqual(event.attempt_count, 1)
            event.next_attempt_at = fields.Datetime.now() - timedelta(seconds=1)
            queue._cron_process_pending()
        self.assertEqual(event.state, "done")
        self.assertEqual(event.attempt_count, 2)
        self.assertEqual(mocked.call_count, 2)

    def test_product_update_event_refreshes_cover_media(self):
        product = self.env["product.template"].create({
            "name": "Old product name",
            "website_published": True,
            "sale_ok": True,
            "shop_api_uuid": "media-product-id",
        })
        payload_zh = {
            "id": "media-product-id", "version": "v2",
            "name": "\u65b0\u4ea7\u54c1", "name_zh": "\u65b0\u4ea7\u54c1", "name_en": "New product",
            "description_zh": "", "description_en": "",
            "published": True, "sale_ok": True,
            "price_cny": 10, "price_usd": 2,
            "images": [{
                "id": "media-product-id", "kind": "cover", "sequence": 0,
                "url": "/api/v1/media/media-product-id",
            }],
        }
        payload_en = dict(payload_zh, name="New product")
        event, _created = self.env["storefront.webhook.event"].enqueue_document({
            "event_id": "product-media-refresh-event",
            "event_type": "product.updated",
            "resource_id": "media-product-id",
            "data": {"product_id": "media-product-id"},
        })
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            side_effect=[payload_zh, payload_en],
        ), patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get_binary",
            return_value=tiny_png,
        ) as media_get:
            event._process_event()
        product.invalidate_recordset()
        self.assertTrue(product.image_1920)
        media_get.assert_called_once_with("/api/v1/media/media-product-id")

    def test_unpublished_product_event_removes_bilingual_catalogue_cache(self):
        product = self.env["product.template"].create({
            "name": "No longer public",
            "website_published": True,
            "sale_ok": True,
            "shop_api_uuid": "unpublished-product-id",
        })
        cache = self.env["storefront.cache.entry"]
        for language in ("zh_CN", "en_US"):
            cache.upsert(
                "product", "unpublished-product-id", {"published": True},
                language=language,
            )
        event, _created = self.env["storefront.webhook.event"].enqueue_document({
            "event_id": "product-unpublished-event",
            "event_type": "product.updated",
            "resource_id": "unpublished-product-id",
            "data": {"product_id": "unpublished-product-id"},
        })
        payload = {
            "id": "unpublished-product-id",
            "published": False,
            "sale_ok": True,
            "images": [],
        }
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            side_effect=[dict(payload), dict(payload)],
        ):
            event._process_event()

        product.invalidate_recordset()
        self.assertFalse(product.website_published)
        self.assertFalse(cache.search_count([
            ("namespace", "=", "product"),
            ("external_id", "=", "unpublished-product-id"),
        ]))

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

    def test_payment_command_uses_dedicated_bounded_timeout(self):
        with patch.dict("os.environ", {
            "STOREFRONT_ERP_BASE_URL": "https://erp.example.test",
            "STOREFRONT_ERP_API_KEY": "server-only-secret",
        }), patch(
            "odoo.addons.storefront_api_bridge.models.api_client.urlrequest.urlopen",
            return_value=_Response({"data": {"authoritative": True}}),
        ) as mocked:
            self.env["storefront.erp.client"].post(
                "/api/v1/orders/order-id/payments", {}, timeout_seconds=27,
            )
        self.assertEqual(mocked.call_args.kwargs["timeout"], 27)

    def test_remote_portal_pages_expose_full_width_editor_top_blocks(self):
        purchase_arch = self.env.ref(
            "storefront_api_bridge.remote_purchase_detail_page"
        ).arch_db
        refund_arch = self.env.ref(
            "storefront_api_bridge.remote_refund_item_page"
        ).arch_db
        self.assertIn("oe_structure_remote_purchase_detail_page_top", purchase_arch)
        self.assertIn("oe_structure_remote_refund_item_page_top", refund_arch)
        self.assertIn("remote_product_option_subtitle", purchase_arch)
        self.assertIn("remote_product_option_subtitle", refund_arch)
        subtitle_arch = self.env.ref(
            "storefront_api_bridge.remote_product_option_subtitle"
        ).arch_db
        self.assertIn("selected_options", subtitle_arch)

    def test_cart_checkout_and_payment_share_localized_product_option_subtitles(self):
        arch = self.env.ref(
            "storefront_api_bridge.cart_product_option_subtitle"
        ).arch_db
        self.assertIn("_storefront_selected_options", arch)
        self.assertIn("x_cart_product_option_subtitle", arch)
        self.assertIn("option.get('label')", arch)
        self.assertEqual(
            self.env["stock.subwarehouse.website.refund.request"]._description,
            "网站退款申请",
        )

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
        order._storefront_clear_shortage_for_product(product)
        self.assertFalse(order.x_storefront_shortage_product_uuids)
        self.assertFalse(order._get_source_inventory_shortage_lines())

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

    @staticmethod
    def _remote_order(order, remote_id="remote-order-id", **values):
        payload = {
            "id": remote_id,
            "state": "draft",
            "customer_id": values.pop("customer_id", "customer-id"),
            "currency": order.currency_id.name,
            "amount_total": order.amount_total,
            "items": order._storefront_api_items(),
            "payments": [],
        }
        payload.update(values)
        return payload

    @staticmethod
    def _completed_payment(order, payment_id="payment-id", remote_order_id="remote-order-id"):
        return {
            "id": payment_id,
            "authoritative": True,
            "order_ids": [remote_order_id],
            "provider": "wechatpay",
            "currency": order.currency_id.name,
            "amount": order.amount_total,
            "state": "done",
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
            side_effect=[
                self._remote_order(order),
                {"shop_base_url": "https://shop.example.test"},
                {},
            ],
        ), self.assertRaises(StorefrontApiError):
            order._storefront_create_payment("wechatpay")

    def test_payment_initiation_accepts_legacy_replay_after_erp_read_confirmation(self):
        order = self._checkout_order()
        order.x_storefront_remote_order_id = "remote-order-id"
        legacy_response = {
            "payment": {"id": "payment-id", "state": "pending"},
            "processing": {"api_url": "/payment/wechatpay/qr/payment-id"},
        }
        confirmed = {
            "id": "payment-id",
            "authoritative": True,
            "order_ids": ["remote-order-id"],
            "provider": "wechatpay",
            "currency": order.currency_id.name,
            "amount": order.amount_total,
            "state": "pending",
        }
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            return_value=legacy_response,
        ), patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            side_effect=[
                self._remote_order(order),
                {"shop_base_url": "https://shop.example.test"},
                confirmed,
            ],
        ):
            result = order._storefront_create_payment("wechatpay")
        self.assertTrue(result["authoritative"])
        self.assertEqual(result["payment"], confirmed)
        self.assertEqual(order.x_storefront_remote_payment_id, "payment-id")

    def test_payment_timeout_recovers_only_from_authoritative_erp_read(self):
        order = self._checkout_order()
        order.x_storefront_remote_order_id = "remote-order-id"
        confirmed = {
            "id": "payment-id",
            "authoritative": True,
            "order_ids": ["remote-order-id"],
            "provider": "wechatpay",
            "currency": order.currency_id.name,
            "amount": order.amount_total,
            "state": "pending",
            "qr_code_data_uri": "data:image/png;base64,AA==",
        }
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            side_effect=StorefrontApiError(
                "timeout", code="erp_unavailable", status=503,
            ),
        ), patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            side_effect=[
                self._remote_order(order),
                {"shop_base_url": "https://shop.example.test"},
                [confirmed],
                confirmed,
            ],
        ), patch("odoo.addons.storefront_api_bridge.models.sale_order.time.sleep"):
            result = order._storefront_create_payment("wechatpay")
        self.assertTrue(result["authoritative"])
        self.assertEqual(result["payment"], confirmed)
        self.assertEqual(order.x_storefront_remote_payment_id, "payment-id")

    def test_changed_cart_detaches_completed_erp_order_and_creates_new_order(self):
        order = self._checkout_order()
        order.write({
            "x_storefront_remote_order_id": "paid-order-id",
            "x_storefront_remote_payment_id": "paid-payment-id",
        })
        old_remote = self._remote_order(
            order,
            remote_id="paid-order-id",
            state="sale",
            amount_total=order.amount_total - 1,
            payments=[{"id": "paid-payment-id", "state": "done"}],
        )
        responses = [
            {
                "reservation_id": "new-reservation-id",
                "authoritative": True,
                "expires_at": fields.Datetime.to_string(
                    fields.Datetime.now() + timedelta(minutes=10)
                ),
            },
            {"id": "new-customer-id"},
            {"id": "new-address-id"},
            self._remote_order(order, remote_id="new-order-id", customer_id="new-customer-id"),
        ]
        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value=old_remote,
        ), patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.post",
            side_effect=responses,
        ) as mocked:
            self.assertEqual(order._storefront_sync_remote_order(), "new-order-id")

        paths = [call.args[0] for call in mocked.call_args_list]
        self.assertNotIn("/api/v1/orders/paid-order-id/cancel", paths)
        self.assertEqual(paths[-1], "/api/v1/orders")
        self.assertEqual(order.x_storefront_remote_order_id, "new-order-id")
        order_call = mocked.call_args_list[-1]
        self.assertIn("storefront-", order_call.args[1]["external_id"])
        self.assertNotEqual(order.x_storefront_remote_payment_id, "paid-payment-id")

    def test_completed_payment_rotates_attempt_and_retires_local_cart(self):
        order = self._checkout_order()
        original_attempt = order.x_storefront_attempt_id
        payment = self._completed_payment(order)
        order.write({
            "x_storefront_remote_order_id": "remote-order-id",
            "x_storefront_remote_payment_id": "payment-id",
            "x_storefront_payment_provider": payment["provider"],
            "x_storefront_payment_currency": payment["currency"],
            "x_storefront_payment_amount": payment["amount"],
            "x_storefront_shortage_product_uuids": ["variant-checkout-id"],
        })

        self.assertTrue(order._storefront_finalize_completed_attempt(payment))

        self.assertEqual(order.state, "cancel")
        self.assertEqual(order.x_storefront_remote_state, "done")
        self.assertEqual(order.x_storefront_completed_attempt_id, original_attempt)
        self.assertNotEqual(order.x_storefront_attempt_id, original_attempt)
        self.assertTrue(order.x_storefront_completed_at)
        self.assertFalse(order.x_storefront_shortage_product_uuids)

    def test_completion_requires_current_authoritative_erp_payment(self):
        order = self._checkout_order()
        original_attempt = order.x_storefront_attempt_id
        order.write({
            "x_storefront_remote_order_id": "remote-order-id",
            "x_storefront_remote_payment_id": "payment-id",
        })
        payment = self._completed_payment(order)
        payment["authoritative"] = False

        with self.assertRaises(StorefrontApiError):
            order._storefront_finalize_completed_attempt(payment)

        self.assertEqual(order.state, "draft")
        self.assertEqual(order.x_storefront_attempt_id, original_attempt)
        self.assertFalse(order.x_storefront_completed_at)

    def test_completion_uses_recorded_payment_amount_after_cart_changes(self):
        order = self._checkout_order()
        payment = self._completed_payment(order)
        order.write({
            "x_storefront_remote_order_id": "remote-order-id",
            "x_storefront_remote_payment_id": "payment-id",
            "x_storefront_payment_provider": payment["provider"],
            "x_storefront_payment_currency": payment["currency"],
            "x_storefront_payment_amount": payment["amount"],
        })
        order.order_line.filtered(lambda line: not line.is_delivery)[:1].product_uom_qty = 2

        self.assertTrue(order._storefront_finalize_completed_attempt(payment))
        self.assertEqual(order.state, "cancel")

    def test_payment_completed_webhook_reads_erp_before_finalizing(self):
        order = self._checkout_order()
        payment = self._completed_payment(order)
        order.write({
            "x_storefront_remote_order_id": "remote-order-id",
            "x_storefront_remote_payment_id": "payment-id",
            "x_storefront_payment_provider": payment["provider"],
            "x_storefront_payment_currency": payment["currency"],
            "x_storefront_payment_amount": payment["amount"],
        })
        event, _created = self.env["storefront.webhook.event"].enqueue_document({
            "event_id": "payment-completed-event",
            "event_type": "payment.completed",
            "resource_id": "payment-id",
            "data": dict(payment),
        })

        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value=payment,
        ) as erp_get:
            event._process_event()

        erp_get.assert_called_once_with("/api/v1/payments/payment-id")
        self.assertEqual(order.state, "cancel")

    def test_payment_completed_webhook_payload_alone_cannot_finalize(self):
        order = self._checkout_order()
        payment = self._completed_payment(order)
        order.write({
            "x_storefront_remote_order_id": "remote-order-id",
            "x_storefront_remote_payment_id": "payment-id",
        })
        event, _created = self.env["storefront.webhook.event"].enqueue_document({
            "event_id": "payment-completed-no-read-event",
            "event_type": "payment.completed",
            "resource_id": "payment-id",
            "data": dict(payment),
        })

        with patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            side_effect=StorefrontApiError("ERP unavailable", status=503),
        ), self.assertRaises(StorefrontApiError):
            event._process_event()

        self.assertEqual(order.state, "draft")

    def test_remote_order_idempotency_uses_checkout_attempt(self):
        order = self._checkout_order()
        attempt_id = order.x_storefront_attempt_id
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

        order_call = mocked.call_args_list[-1]
        self.assertIn(attempt_id, order_call.kwargs["idempotency_key"])
        self.assertIn(attempt_id, order_call.args[1]["external_id"])

    def test_checkout_does_not_call_erp_inventory_or_reservation(self):
        response = object()
        order = MagicMock()
        order_sudo = order.sudo.return_value
        order_sudo._get_source_inventory_shortage_lines.return_value = False
        fake_request = SimpleNamespace(cart=order, session={})

        with patch.object(
            website_sale_module, "request", fake_request,
        ), patch.object(
            StorefrontWebsiteSale, "_sync_website_checkout_language",
        ), patch(
            "odoo.addons.website_sale.controllers.main.WebsiteSale.shop_checkout",
            return_value=response,
        ) as native_checkout:
            result = StorefrontWebsiteSale.shop_checkout.__wrapped__(
                StorefrontWebsiteSale()
            )

        self.assertIs(result, response)
        order_sudo._storefront_check_inventory.assert_not_called()
        order_sudo._storefront_ensure_quote.assert_not_called()
        native_checkout.assert_called_once()

    def test_checkout_blocks_only_existing_erp_rejection_markers(self):
        redirect_result = object()
        order = MagicMock()
        order_sudo = order.sudo.return_value
        order_sudo._get_source_inventory_shortage_lines.return_value = MagicMock()
        fake_request = SimpleNamespace(
            cart=order,
            session={},
            redirect=MagicMock(return_value=redirect_result),
        )

        with patch.object(
            website_sale_module, "request", fake_request,
        ), patch.object(
            StorefrontWebsiteSale, "_sync_website_checkout_language",
        ):
            result = StorefrontWebsiteSale.shop_checkout.__wrapped__(
                StorefrontWebsiteSale()
            )

        self.assertIs(result, redirect_result)
        self.assertTrue(fake_request.session["x_stock_quantity_warning"])
        order_sudo._storefront_check_inventory.assert_not_called()

    def test_payment_inventory_rejection_marks_rows_and_returns_to_cart(self):
        redirect_result = object()
        order = MagicMock(x_storefront_remote_payment_id=False)
        order_sudo = order.sudo.return_value
        order_sudo._get_source_inventory_shortage_lines.return_value = False
        order_sudo._storefront_check_inventory.return_value = [{
            "product_id": "variant-checkout-id", "available": False,
        }]
        fake_request = SimpleNamespace(
            cart=order,
            session={},
            redirect=MagicMock(return_value=redirect_result),
        )

        with patch.object(
            website_sale_module, "request", fake_request,
        ), patch.object(
            StorefrontWebsiteSale, "_sync_website_checkout_language",
        ):
            result = StorefrontWebsiteSale.shop_payment.__wrapped__(
                StorefrontWebsiteSale()
            )

        self.assertIs(result, redirect_result)
        self.assertTrue(fake_request.session["x_stock_quantity_warning"])
        order_sudo._storefront_sync_remote_order.assert_not_called()

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

    def test_product_grouping_uses_name_not_flex_or_style_code(self):
        first = self.env["product.template"].create({
            "name": "双板滑雪鞋",
            "default_code": "012307S1-MA007-H001260",
            "sale_ok": True,
        })
        second = self.env["product.template"].create({
            "name": "Ski Boots 100 flex",
            "default_code": "012307S1-MA010-BG01260",
            "sale_ok": True,
        })
        second.with_context(lang="zh_CN").name = first.with_context(lang="zh_CN").name
        different_name = self.env["product.template"].create({
            "name": "Different boots",
            "default_code": "012307S1-MA007-W001260",
            "sale_ok": True,
        })
        self.assertEqual(first._normalize_shop_group_name(), second._normalize_shop_group_name())
        self.assertEqual(len((first | second)._get_shop_grouped_products()), 1)
        self.assertNotEqual(
            first._normalize_shop_group_name(),
            different_name._normalize_shop_group_name(),
        )
        self.assertEqual(
            len((first | second | different_name)._get_shop_grouped_products()), 2,
        )

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
        ) as post_mock, patch(
            "odoo.addons.storefront_api_bridge.models.api_client.StorefrontErpClient.get",
            return_value=self._remote_order(
                order,
                remote_id="anonymous-order-id",
                customer_id="anonymous-customer-id",
            ),
        ):
            order._storefront_sync_remote_order()

        self.assertEqual(
            [call.args[0] for call in post_mock.call_args_list],
            [
                "/api/v1/orders/anonymous-order-id/cancel",
                "/api/v1/checkout/quote",
                "/api/v1/orders",
            ],
        )
        self.assertEqual(order.x_storefront_remote_customer_id, "erp-customer-id")
        self.assertEqual(order.x_storefront_remote_order_id, "customer-order-id")
