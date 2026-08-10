import base64
import json
from datetime import timedelta

from odoo import api, fields, models


class StorefrontCacheEntry(models.Model):
    _name = "storefront.cache.entry"
    _description = "Storefront PostgreSQL Cache Entry"
    _order = "namespace, external_id, language"

    namespace = fields.Char(required=True, index=True)
    external_id = fields.Char(required=True, index=True)
    language = fields.Char(required=True, default="und", index=True)
    version = fields.Char(index=True)
    payload = fields.Json(required=True, default=dict)
    synchronized_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)

    _unique_cache_key = models.Constraint(
        "UNIQUE(namespace, external_id, language)",
        "A storefront cache key must be unique.",
    )

    @api.model
    def upsert(self, namespace, external_id, payload, language="", version=False):
        language_key = language or "und"
        cache = self.sudo().search([
            ("namespace", "=", namespace),
            ("external_id", "=", external_id),
            ("language", "=", language_key),
        ], limit=1)
        values = {
            "version": version or False,
            "payload": payload if payload else {"_empty": True},
            "synchronized_at": fields.Datetime.now(),
        }
        if cache:
            cache.write(values)
        else:
            cache = self.sudo().create({
                "namespace": namespace,
                "external_id": external_id,
                "language": language_key,
                **values,
            })
        return cache

    @api.model
    def inventory_map(self):
        return {
            entry.external_id: entry.payload
            for entry in self.sudo().search([("namespace", "=", "inventory")])
        }

    @api.model
    def replace_inventory_snapshot(self, snapshot):
        rows = {}
        for product in (snapshot or {}).get("products") or []:
            product_id = product.get("id")
            if product_id:
                rows[product_id] = {
                    "available": bool(product.get("available")),
                    "available_quantity": float(product.get("available_quantity") or 0.0),
                    "record_type": "template",
                }
            for variant in product.get("variants") or []:
                variant_id = variant.get("id")
                if variant_id:
                    rows[variant_id] = {
                        "available": bool(variant.get("available")),
                        "available_quantity": float(variant.get("available_quantity") or 0.0),
                        "record_type": "variant",
                        "template_id": product_id,
                    }

        existing = {
            entry.external_id: entry
            for entry in self.sudo().search([("namespace", "=", "inventory")])
        }
        generated_at = (snapshot or {}).get("generated_at") or False
        for external_id, payload in rows.items():
            entry = existing.pop(external_id, self.browse())
            values = {
                "version": generated_at,
                "payload": payload,
                "synchronized_at": fields.Datetime.now(),
            }
            if entry:
                entry.write(values)
            else:
                self.sudo().create({
                    "namespace": "inventory",
                    "external_id": external_id,
                    "language": "und",
                    **values,
                })
        if existing:
            self.sudo().browse([entry.id for entry in existing.values()]).unlink()
        return rows


class StorefrontWebhookEvent(models.Model):
    _name = "storefront.webhook.event"
    _description = "Storefront ERP Outbox Event Queue"
    _order = "received_at, id"

    event_id = fields.Char(required=True, index=True)
    event_type = fields.Char(required=True, index=True)
    occurred_at = fields.Datetime(index=True)
    resource_id = fields.Char(index=True)
    resource_version = fields.Char()
    payload = fields.Json(required=True, default=dict)
    state = fields.Selection([
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("done", "Done"),
        ("error", "Error"),
        ("dead", "Stopped"),
    ], required=True, default="pending", index=True)
    received_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    processed_at = fields.Datetime(index=True)
    last_error = fields.Text()
    attempt_count = fields.Integer(default=0)
    next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True)

    _unique_event_id = models.Constraint(
        "UNIQUE(event_id)",
        "An ERP outbox event can be queued only once.",
    )

    @api.model
    def enqueue_document(self, document):
        event_id = str(document.get("event_id") or "").strip()
        # Serialize duplicate deliveries for the same outbox identifier before
        # checking the unique key. This keeps concurrent webhook deliveries
        # idempotent without leaving the request transaction aborted.
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [event_id])
        existing = self.sudo().search([("event_id", "=", event_id)], limit=1)
        if existing:
            return existing, False
        occurred_at = document.get("occurred_at") or False
        return self.sudo().create({
            "event_id": event_id,
            "event_type": str(document.get("event_type") or "").strip(),
            "occurred_at": fields.Datetime.to_datetime(occurred_at) if occurred_at else False,
            "resource_id": document.get("resource_id") or False,
            "resource_version": document.get("resource_version") or False,
            "payload": document.get("data") if document.get("data") else {"_empty": True},
        }), True

    @api.model
    def _cron_process_pending(self, limit=40):
        # A bilingual product refresh makes two ERP API calls. Keep the cron
        # batch below the 120 requests/minute endpoint limit and reserve room
        # for inventory checks and ordinary storefront traffic.
        events = self.sudo().search([
            ("state", "in", ("pending", "error")),
            "|",
            ("next_attempt_at", "=", False),
            ("next_attempt_at", "<=", fields.Datetime.now()),
        ], limit=limit, order="received_at, id")
        product_event_types = {
            "product.created", "product.updated", "product.image.updated",
        }
        for event in events.filtered(lambda row: row.event_type in product_event_types):
            newer = self.sudo().search_count([
                ("id", ">", event.id),
                ("resource_id", "=", event.resource_id),
                ("event_type", "in", tuple(product_event_types)),
            ])
            if newer:
                event.write({
                    "state": "done",
                    "processed_at": fields.Datetime.now(),
                    "last_error": False,
                })
        events = events.filtered(lambda row: row.state in ("pending", "error"))
        for event in events:
            event.write({
                "state": "processing",
                "attempt_count": event.attempt_count + 1,
            })
        inventory_events = events.filtered(lambda event: event.event_type == "inventory.updated")
        if inventory_events:
            try:
                self.env["storefront.erp.client"].refresh_inventory_snapshot()
            except Exception as error:
                for event in inventory_events:
                    event._record_processing_error(error)
            else:
                inventory_events.write({
                    "state": "done",
                    "processed_at": fields.Datetime.now(),
                    "last_error": False,
                    "next_attempt_at": False,
                })

        for event in events - inventory_events:
            try:
                with self.env.cr.savepoint():
                    event._process_event()
            except Exception as error:
                event._record_processing_error(error)
            else:
                event.write({
                    "state": "done",
                    "processed_at": fields.Datetime.now(),
                    "last_error": False,
                    "next_attempt_at": False,
                })

    def _record_processing_error(self, error):
        self.ensure_one()
        stopped = self.attempt_count >= 8
        delay = min(30 * (2 ** max(self.attempt_count - 1, 0)), 3600)
        self.write({
            "state": "dead" if stopped else "error",
            "processed_at": fields.Datetime.now(),
            "next_attempt_at": False if stopped else fields.Datetime.now() + timedelta(seconds=delay),
            "last_error": f"{type(error).__name__}: {error}"[:4000],
        })

    def _process_event(self):
        self.ensure_one()
        if self.event_type in {
            "product.created", "product.updated", "product.image.updated",
        }:
            return self._sync_product()
        if self.event_type == "product.archived":
            product = self.env["product.template"].sudo().with_context(active_test=False).search([
                ("shop_api_uuid", "=", self.resource_id),
            ], limit=1)
            if product:
                product.with_context(shop_api_skip_event=True, tracking_disable=True).write({
                    "website_published": False,
                    "active": False,
                })
            cache = self.env["storefront.cache.entry"]
            cache.sudo().search([
                ("namespace", "=", "product"),
                ("external_id", "=", self.resource_id or self.event_id),
            ]).unlink()
            cache.upsert(
                "product", self.resource_id or self.event_id,
                {"archived": True, "event": self.payload},
                version=self.resource_version,
            )
            return True

        self.env["storefront.cache.entry"].upsert(
            "outbox_event", self.resource_id or self.event_id,
            {"event_type": self.event_type, "data": self.payload},
            version=self.resource_version,
        )
        return True

    def _sync_product(self):
        self.ensure_one()
        product_id = self.resource_id or self.payload.get("product_id")
        if not product_id:
            raise ValueError("Product event has no resource identifier")
        client = self.env["storefront.erp.client"]
        payload_zh = client.get(f"/api/v1/products/{product_id}", params={"language": "zh_CN"})
        payload_en = client.get(f"/api/v1/products/{product_id}", params={"language": "en_US"})
        cache = self.env["storefront.cache.entry"]
        product = self.env["product.template"].sudo().with_context(active_test=False).search([
            ("shop_api_uuid", "=", product_id),
        ], limit=1)

        # Product update events are also emitted for ERP-only or unpublished
        # records.  They must invalidate, rather than recreate, the public
        # catalogue cache.  Otherwise an incremental event immediately after a
        # full refresh can resurrect stale product rows in both languages.
        if not payload_zh.get("published"):
            cache.sudo().search([
                ("namespace", "=", "product"),
                ("external_id", "=", product_id),
            ]).unlink()
            if product:
                product.with_context(
                    shop_api_skip_event=True, tracking_disable=True,
                ).write({"website_published": False})
            return True

        cache.upsert(
            "product", product_id, payload_zh, language="zh_CN",
            version=payload_zh.get("version") or self.resource_version,
        )
        cache.upsert(
            "product", product_id, payload_en, language="en_US",
            version=payload_en.get("version") or self.resource_version,
        )

        if not product:
            # New catalogue rows remain cached until the later full-reconciliation
            # phase creates complete templates, variants and attribute relations.
            return True

        field_values = {
            "name": payload_zh.get("name_zh") or payload_zh.get("name") or product.name,
            "x_website_english_name": payload_en.get("name_en") or payload_en.get("name") or "",
            "x_website_description_zh": payload_zh.get("description_zh") or "",
            "x_website_description_en": payload_en.get("description_en") or "",
            "list_price": float(payload_zh.get("price_cny") or 0.0),
            "x_website_usd_price": float(payload_en.get("price_usd") or 0.0),
            "website_published": bool(payload_zh.get("published")),
            "sale_ok": bool(payload_zh.get("sale_ok")),
            "active": True,
        }
        material_type = payload_zh.get("material_type")
        if material_type in dict(product._fields["x_material_type"].selection):
            field_values["x_material_type"] = material_type
        product.with_context(
            lang="zh_CN", shop_api_skip_event=True, tracking_disable=True,
        ).write(field_values)
        self._sync_product_images(product, payload_zh.get("images") or [])
        return True

    def _sync_product_images(self, product, images):
        client = self.env["storefront.erp.client"]
        Image = self.env["product.image"].sudo().with_context(shop_api_skip_event=True)
        cover = False
        gallery_rows = []
        seen_ids = set()
        for image in images:
            image_id = image.get("id")
            image_url = image.get("url")
            if not image_id or not image_url:
                raise ValueError("ERP returned invalid product image metadata")
            if image_id in seen_ids:
                raise ValueError(f"ERP returned duplicate product image {image_id}")
            seen_ids.add(image_id)
            binary = base64.b64encode(client.get_binary(image_url))
            if image.get("kind") == "cover":
                cover = binary
                continue
            gallery_rows.append((image_id, {
                "name": image.get("name") or f"ERP image {image_id}",
                "product_tmpl_id": product.id,
                "image_1920": binary,
                "sequence": int(image.get("sequence") or 0),
            }))

        product.with_context(
            shop_api_skip_event=True, tracking_disable=True,
        ).image_1920 = cover
        existing = Image.with_context(active_test=False).search([
            ("product_tmpl_id", "=", product.id),
        ])
        retained = Image.browse()
        for image_id, values in gallery_rows:
            gallery = existing.filtered(lambda row: row.shop_api_uuid == image_id)[:1]
            if gallery:
                gallery.write(values)
                retained |= gallery
            else:
                retained |= Image.create({"shop_api_uuid": image_id, **values})
        obsolete = existing - retained
        if obsolete:
            obsolete.unlink()
