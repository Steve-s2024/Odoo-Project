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
        if generated_at:
            self.upsert(
                "inventory_authority", "erp",
                {"generated_at": generated_at},
                version=generated_at,
            )
        return rows

    @api.model
    def apply_authoritative_product_inventory(self, payload):
        """Immediately apply a signed ERP product snapshot to Shop inventory.

        Product/media synchronization remains asynchronous, but availability
        must not wait behind image downloads.  A global ERP watermark allows
        every product event from the same snapshot while rejecting delayed
        events from an older snapshot.  The first authoritative event repairs
        any legacy/future-dated local cache by deliberately forcing replacement.
        """
        incoming_version = payload.get("inventory_version")
        if not incoming_version:
            return {}
        watermark = self.sudo().search([
            ("namespace", "=", "inventory_authority"),
            ("external_id", "=", "erp"),
        ], limit=1)
        if watermark.version and watermark.version > incoming_version:
            return {}
        rows = self.upsert_product_inventory(payload, force=True)
        self.upsert(
            "inventory_authority", "erp",
            {"generated_at": incoming_version},
            version=incoming_version,
        )
        return rows

    @api.model
    def upsert_product_inventory(self, payload, force=False):
        """Apply one product event to the persistent availability cache.

        ``force`` is reserved for ERP-authored replacement snapshots.  It
        deliberately bypasses a Shop-local version value: the ERP is the
        inventory authority and a manual push must repair, rather than defer
        to, a stale or accidentally future-dated local cache row.
        """
        version = payload.get("inventory_version") or payload.get("version") or False
        rows = {}
        product_id = payload.get("id")
        if product_id:
            rows[product_id] = {
                "available": bool(payload.get("available")),
                "available_quantity": float(payload.get("available_quantity") or 0.0),
                "record_type": "template",
            }
        for variant in payload.get("variants") or []:
            variant_id = variant.get("id")
            if variant_id:
                rows[variant_id] = {
                    "available": bool(variant.get("available")),
                    "available_quantity": float(variant.get("available_quantity") or 0.0),
                    "record_type": "variant",
                    "template_id": product_id,
                }

        grouped_templates = {}
        for variant in payload.get("group_variants") or []:
            variant_id = variant.get("id")
            template_id = variant.get("product_id")
            quantity = float(variant.get("available_quantity") or 0.0)
            if variant_id:
                rows[variant_id] = {
                    "available": bool(variant.get("available")),
                    "available_quantity": quantity,
                    "record_type": "variant",
                    "template_id": template_id,
                }
            if template_id:
                grouped = grouped_templates.setdefault(template_id, {
                    "available": False,
                    "available_quantity": 0.0,
                    "record_type": "template",
                })
                grouped["available"] = grouped["available"] or bool(
                    variant.get("available")
                )
                grouped["available_quantity"] = max(
                    grouped["available_quantity"], quantity,
                )
        rows.update(grouped_templates)

        for external_id, inventory in rows.items():
            existing = self.sudo().search([
                ("namespace", "=", "inventory"),
                ("external_id", "=", external_id),
                ("language", "=", "und"),
            ], limit=1)
            if not force and version and existing.version and existing.version > version:
                continue
            self.upsert(
                "inventory", external_id, inventory,
                version=version,
            )
        return rows

    @api.model
    def remove_product_inventory(self, payload):
        external_ids = [payload.get("id")]
        external_ids.extend(
            variant.get("id") for variant in payload.get("variants") or []
        )
        external_ids = [external_id for external_id in external_ids if external_id]
        if external_ids:
            self.sudo().search([
                ("namespace", "=", "inventory"),
                ("external_id", "in", external_ids),
            ]).unlink()


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
            existing._apply_authoritative_inventory_hint()
            return existing, False
        occurred_at = document.get("occurred_at") or False
        event = self.sudo().create({
            "event_id": event_id,
            "event_type": str(document.get("event_type") or "").strip(),
            "occurred_at": fields.Datetime.to_datetime(occurred_at) if occurred_at else False,
            "resource_id": document.get("resource_id") or False,
            "resource_version": document.get("resource_version") or False,
            "payload": document.get("data") if document.get("data") else {"_empty": True},
        })
        event._apply_authoritative_inventory_hint()
        return event, True

    def _apply_authoritative_inventory_hint(self):
        """Apply stock before the asynchronous catalogue/media queue runs."""
        cache = self.env["storefront.cache.entry"]
        applied = False
        for event in self:
            data = event.payload or {}
            if (
                event.event_type not in {
                    "product.created", "product.updated", "product.image.updated",
                }
                or data.get("authoritative") is not True
                or data.get("replace") is not True
            ):
                continue
            snapshot = (data.get("snapshots") or {}).get("zh_CN") or {}
            if not isinstance(snapshot, dict) or not snapshot.get("id"):
                continue
            if snapshot.get("published") is False:
                cache.remove_product_inventory(snapshot)
                applied = True
            elif snapshot.get("inventory_version"):
                cache.apply_authoritative_product_inventory(snapshot)
                applied = True
        if applied:
            self.env["storefront.erp.client"].clear_inventory_snapshot_cache()
        return applied

    @api.model
    def _cron_process_pending(self, limit=10):
        # Product media can be several megabytes and is downloaded before the
        # transaction commits.  Ten rows keeps each cron transaction bounded
        # while remaining below the ERP endpoint rate limit and leaving room
        # for live inventory/payment traffic.
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
        if self.event_type == "payment.completed":
            return self._process_payment_completed()
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

    def _process_payment_completed(self):
        """Confirm payment from ERP, then retire the matching local draft cart."""
        self.ensure_one()
        payment_id = self.resource_id or self.payload.get("id")
        if not payment_id:
            raise ValueError("Payment completion event has no resource identifier")
        payment = self.env["storefront.erp.client"].get(
            f"/api/v1/payments/{payment_id}"
        ) or {}
        remote_order_ids = [str(item) for item in payment.get("order_ids") or [] if item]
        if (
            payment.get("authoritative") is not True
            or payment.get("id") != payment_id
            or payment.get("state") != "done"
            or not payment.get("provider")
            or not payment.get("currency")
            or not remote_order_ids
        ):
            raise ValueError("ERP did not authoritatively confirm the completed payment")

        orders = self.env["sale.order"].sudo().search([
            "|",
            ("x_storefront_remote_payment_id", "=", payment_id),
            ("x_storefront_remote_order_id", "in", remote_order_ids),
        ])
        orders = orders.filtered(
            lambda order: (
                order.x_storefront_remote_order_id in remote_order_ids
                and order.x_storefront_remote_payment_id in (False, payment_id)
            )
        )
        if not orders:
            # The event can arrive before the storefront payment request commits.
            # Retrying preserves causal ordering without guessing locally.
            raise ValueError("The completed ERP payment has no matching storefront order yet")
        for order in orders:
            if not order.x_storefront_remote_payment_id:
                order.write({
                    "x_storefront_remote_payment_id": payment_id,
                    "x_storefront_payment_provider": payment["provider"],
                    "x_storefront_payment_currency": payment["currency"],
                    "x_storefront_payment_amount": payment.get("amount") or 0.0,
                })
            order._storefront_finalize_completed_attempt(payment)
        return True

    def _sync_product(self):
        self.ensure_one()
        product_id = self.resource_id or self.payload.get("product_id")
        if not product_id:
            raise ValueError("Product event has no resource identifier")
        client = self.env["storefront.erp.client"]
        snapshots = self.payload.get("snapshots") or {}
        payload_zh = snapshots.get("zh_CN") if self.payload.get("authoritative") is True else None
        payload_en = snapshots.get("en_US") if self.payload.get("authoritative") is True else None
        if not isinstance(payload_zh, dict) or payload_zh.get("id") != product_id:
            payload_zh = client.get(
                f"/api/v1/products/{product_id}", params={"language": "zh_CN"}
            )
        if not isinstance(payload_en, dict) or payload_en.get("id") != product_id:
            payload_en = client.get(
                f"/api/v1/products/{product_id}", params={"language": "en_US"}
            )
        cache = self.env["storefront.cache.entry"]
        previous_cache = cache.sudo().search([
            ("namespace", "=", "product"),
            ("external_id", "=", product_id),
            ("language", "=", "zh_CN"),
        ], limit=1)
        previous_payload = previous_cache.payload if previous_cache else {}
        incoming_version = payload_zh.get("version") or self.resource_version
        cached_versions = cache.sudo().search([
            ("namespace", "=", "product"),
            ("external_id", "=", product_id),
            ("version", "!=", False),
        ]).mapped("version")
        # A delayed retry must not overwrite a newer product batch that the
        # storefront has already applied. Odoo datetime versions are emitted
        # in a lexicographically sortable UTC format.
        if incoming_version and any(version > incoming_version for version in cached_versions):
            return True
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
            cache.remove_product_inventory(payload_zh)
            client.clear_inventory_snapshot_cache()
            if product:
                product.with_context(
                    shop_api_skip_event=True, tracking_disable=True,
                ).write({"website_published": False})
            return True

        catalog_sync = self.env["storefront.catalog.sync"]
        catalog_sync._validate_variant_mapping({product_id: payload_zh})
        product = catalog_sync._upsert_product(payload_zh, payload_en)
        media_is_current = self._product_media_is_current(
            product,
            previous_payload.get("images") if isinstance(previous_payload, dict) else [],
            payload_zh.get("images") or [],
        )
        cache.upsert(
            "product", product_id, payload_zh, language="zh_CN",
            version=payload_zh.get("version") or self.resource_version,
        )
        cache.upsert(
            "product", product_id, payload_en, language="en_US",
            version=payload_en.get("version") or self.resource_version,
        )
        authoritative_inventory = bool(
            self.payload.get("authoritative") is True
            and self.payload.get("replace") is True
            and payload_zh.get("inventory_version")
        )
        if authoritative_inventory:
            cache.apply_authoritative_product_inventory(payload_zh)
        else:
            cache.upsert_product_inventory(payload_zh)
        client.clear_inventory_snapshot_cache()
        if not media_is_current:
            self._sync_product_images(product, payload_zh.get("images") or [])
        return True

    @api.model
    def _product_media_is_current(self, product, cached_images, incoming_images):
        """Return true only when versioned ERP media is already present locally.

        Catalogue cache and product media are committed in one transaction, so
        matching versioned metadata is a reliable fast path.  The local binary
        and gallery identifiers are checked as an additional guard against old
        or manually altered cache rows.
        """
        cached_images = cached_images if isinstance(cached_images, list) else []
        incoming_images = incoming_images if isinstance(incoming_images, list) else []

        def signature(images):
            rows = []
            for image in images:
                if not isinstance(image, dict) or not image.get("id") or not image.get("version"):
                    return None
                rows.append((
                    str(image["id"]),
                    str(image.get("kind") or "gallery"),
                    str(image["version"]),
                    int(image.get("sequence") or 0),
                ))
            return sorted(rows)

        if signature(cached_images) != signature(incoming_images):
            return False
        if incoming_images and signature(incoming_images) is None:
            return False

        expects_cover = any(
            isinstance(image, dict) and image.get("kind") == "cover"
            for image in incoming_images
        )
        if bool(product.image_1920) != expects_cover:
            return False
        expected_gallery_ids = {
            str(image["id"])
            for image in incoming_images
            if isinstance(image, dict) and image.get("kind") != "cover" and image.get("id")
        }
        actual_gallery_ids = set(
            self.env["product.image"].sudo().with_context(active_test=False).search([
                ("product_tmpl_id", "=", product.id),
            ]).mapped("shop_api_uuid")
        )
        return actual_gallery_ids == expected_gallery_ids

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
