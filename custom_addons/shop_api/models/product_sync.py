from odoo import _, api, fields, models


class ShopApiProductSyncPending(models.Model):
    """Database-backed set of ERP products waiting for storefront refresh."""

    _name = "shop.api.product.sync.pending"
    _description = "Pending storefront product synchronization"
    _order = "first_queued_at, id"

    product_tmpl_id = fields.Many2one(
        "product.template",
        string="产品",
        required=True,
        ondelete="cascade",
        index=True,
    )
    product_uuid = fields.Char(string="产品 UUID", required=True, index=True)
    first_queued_at = fields.Datetime(
        string="首次进入集合",
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    last_queued_at = fields.Datetime(
        string="最近变更时间",
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    reason = fields.Char(string="最近变更原因")

    _unique_product = models.Constraint(
        "UNIQUE(product_tmpl_id)",
        "每个产品在商店待推送集合中只能出现一次。",
    )

    @api.model
    def queue_products(self, products, reason="product.updated"):
        products = products.sudo().with_context(active_test=False).exists()
        if not products:
            return self.browse()
        products._shop_api_ensure_uuid()
        now = fields.Datetime.now()
        # The unique key makes this table a durable set.  ON CONFLICT also
        # makes concurrent product writes collapse into the same queue row.
        self.env.cr.execute(
            """
            INSERT INTO shop_api_product_sync_pending
                (product_tmpl_id, product_uuid, first_queued_at, last_queued_at,
                 reason, create_uid, create_date, write_uid, write_date)
            SELECT product_id, product_uuid, %s, %s, %s, %s, %s, %s, %s
              FROM unnest(%s::int[], %s::varchar[]) AS pending(product_id, product_uuid)
            ON CONFLICT (product_tmpl_id) DO UPDATE SET
                product_uuid = EXCLUDED.product_uuid,
                last_queued_at = EXCLUDED.last_queued_at,
                reason = EXCLUDED.reason,
                write_uid = EXCLUDED.write_uid,
                write_date = EXCLUDED.write_date
            """,
            [
                now, now, reason, self.env.uid, now, self.env.uid, now,
                products.ids, products.mapped("shop_api_uuid"),
            ],
        )
        self.invalidate_model()
        return self.sudo().search([("product_tmpl_id", "in", products.ids)])

    @api.model
    def _flush_pending_products(self, dispatch=False):
        # Only one cron or manual force action may drain the set at a time.
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ["shop_api.product.sync.pending.flush"],
        )
        pending = self.sudo().search([], order="first_queued_at, id")
        if not pending:
            return {"products": 0, "events": self.env["shop.api.event"]}

        inventory_snapshot = self.env["product.template"]._shop_api_inventory_snapshot()
        inventory_by_product = {
            row["id"]: row
            for row in inventory_snapshot.get("products") or []
            if row.get("id")
        }
        inventory_by_variant = {
            variant["id"]: variant
            for row in inventory_by_product.values()
            for variant in row.get("variants") or []
            if variant.get("id")
        }

        events = self.env["shop.api.event"]
        for row in pending:
            product = row.product_tmpl_id.sudo().with_context(active_test=False)
            if not product.exists():
                continue
            product._shop_api_ensure_uuid()
            snapshots = {
                language: product._shop_api_payload(language=language, detail=True)
                for language in ("zh_CN", "en_US")
            }
            for snapshot in snapshots.values():
                self._merge_inventory_snapshot(
                    snapshot,
                    inventory_by_product,
                    inventory_by_variant,
                    inventory_snapshot.get("generated_at"),
                )
            event_type = "product.archived" if not product.active else "product.updated"
            events |= self.env["shop.api.event"].enqueue(
                event_type,
                product,
                {
                    "product_id": product.shop_api_uuid,
                    "authoritative": True,
                    "replace": True,
                    "snapshots": snapshots,
                },
            )

        # The durable outbox owns retries from this point onward, so clearing
        # the set cannot lose an update even if the storefront is unavailable.
        pending.unlink()
        if dispatch:
            for event in events:
                event._dispatch()
        return {"products": len(events), "events": events}

    @api.model
    def _merge_inventory_snapshot(
        self, payload, inventory_by_product, inventory_by_variant, generated_at,
    ):
        """Embed the authoritative current availability in a product event."""
        product_inventory = inventory_by_product.get(payload.get("id")) or {}
        if product_inventory:
            payload.update({
                "available": bool(product_inventory.get("available")),
                "available_quantity": float(
                    product_inventory.get("available_quantity") or 0.0
                ),
            })
        for variant in payload.get("variants") or []:
            inventory = inventory_by_variant.get(variant.get("id"))
            if inventory:
                variant.update({
                    "available": bool(inventory.get("available")),
                    "available_quantity": float(
                        inventory.get("available_quantity") or 0.0
                    ),
                })
        for variant in payload.get("group_variants") or []:
            inventory = inventory_by_variant.get(variant.get("id"))
            if inventory:
                variant.update({
                    "available": bool(inventory.get("available")),
                    "available_quantity": float(
                        inventory.get("available_quantity") or 0.0
                    ),
                })
        payload["inventory_version"] = generated_at or fields.Datetime.to_string(
            fields.Datetime.now()
        )
        return payload

    @api.model
    def _cron_flush_pending_products(self):
        return self._flush_pending_products(dispatch=True)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def _queue_shop_product_sync(self, reason="product.updated"):
        if not self or self.env.context.get("shop_api_skip_event"):
            return self.env["shop.api.product.sync.pending"]
        return self.env["shop.api.product.sync.pending"].queue_products(self, reason=reason)

    def action_push_updates_to_shop(self):
        products = self.exists()
        if not products:
            return False
        # A product card represents every published SKU with the same Chinese
        # name.  Include those siblings so a manual push also replaces the
        # selected group-cover flag, cover image and all selector inventory.
        grouped_products = self.browse()
        for product in products:
            grouped_products |= product._get_all_shop_group_siblings()
        grouped_products._queue_shop_product_sync(reason="manual.force")
        result = self.env["shop.api.product.sync.pending"]._flush_pending_products(
            dispatch=True,
        )
        event_count = len(result["events"])
        failed_count = len(result["events"].filtered(lambda event: event.state in ("failed", "dead")))
        if failed_count:
            message = _(
                "已处理 %(count)s 个待更新产品；其中 %(failed)s 个正在等待自动重试。",
                count=event_count,
                failed=failed_count,
            )
            notification_type = "warning"
        else:
            message = _(
                "已选择 %(selected)s 个产品，并推送共 %(count)s 个待更新产品至商店。",
                selected=len(products),
                count=event_count,
            )
            notification_type = "success"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("商店产品同步"),
                "message": message,
                "type": notification_type,
                "sticky": False,
            },
        }


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model_create_multi
    def create(self, vals_list):
        variants = super().create(vals_list)
        variants.mapped("product_tmpl_id")._queue_shop_product_sync("variant.created")
        return variants

    def write(self, vals):
        templates = self.mapped("product_tmpl_id")
        result = super().write(vals)
        (templates | self.mapped("product_tmpl_id"))._queue_shop_product_sync("variant.updated")
        return result

    def action_push_updates_to_shop(self):
        return self.mapped("product_tmpl_id").action_push_updates_to_shop()


class ProductTemplateAttributeLine(models.Model):
    _inherit = "product.template.attribute.line"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.mapped("product_tmpl_id")._queue_shop_product_sync("attribute.updated")
        return lines

    def write(self, vals):
        templates = self.mapped("product_tmpl_id")
        result = super().write(vals)
        (templates | self.mapped("product_tmpl_id"))._queue_shop_product_sync("attribute.updated")
        return result

    def unlink(self):
        templates = self.mapped("product_tmpl_id")
        result = super().unlink()
        templates.exists()._queue_shop_product_sync("attribute.updated")
        return result


class ProductTemplateAttributeValue(models.Model):
    _inherit = "product.template.attribute.value"

    @api.model_create_multi
    def create(self, vals_list):
        values = super().create(vals_list)
        values.mapped("product_tmpl_id")._queue_shop_product_sync("attribute.updated")
        return values

    def write(self, vals):
        templates = self.mapped("product_tmpl_id")
        result = super().write(vals)
        (templates | self.mapped("product_tmpl_id"))._queue_shop_product_sync("attribute.updated")
        return result

    def unlink(self):
        templates = self.mapped("product_tmpl_id")
        result = super().unlink()
        templates.exists()._queue_shop_product_sync("attribute.updated")
        return result


class ProductAttribute(models.Model):
    _inherit = "product.attribute"

    def write(self, vals):
        templates = (
            self.mapped("product_tmpl_ids")
            | self.env["product.template.custom.attribute.value"].sudo().search([
                ("attribute_id", "in", self.ids),
            ]).mapped("product_tmpl_id")
        )
        result = super().write(vals)
        templates.exists()._queue_shop_product_sync("attribute.definition.updated")
        return result


class ProductAttributeValue(models.Model):
    _inherit = "product.attribute.value"

    def write(self, vals):
        templates = self.env["product.template.attribute.value"].sudo().search([
            ("product_attribute_value_id", "in", self.ids),
        ]).mapped("product_tmpl_id")
        result = super().write(vals)
        templates.exists()._queue_shop_product_sync("attribute.value.updated")
        return result


class ProductCategory(models.Model):
    _inherit = "product.category"

    def write(self, vals):
        affected_categories = self.search([("id", "child_of", self.ids)])
        products = self.env["product.template"].sudo().with_context(active_test=False).search([
            ("categ_id", "in", affected_categories.ids),
        ])
        result = super().write(vals)
        products._queue_shop_product_sync("category.updated")
        return result


class ProductTemplateCustomAttributeValue(models.Model):
    _inherit = "product.template.custom.attribute.value"

    @api.model_create_multi
    def create(self, vals_list):
        values = super().create(vals_list)
        values.mapped("product_tmpl_id")._queue_shop_product_sync("custom_attribute.updated")
        return values

    def write(self, vals):
        templates = self.mapped("product_tmpl_id")
        result = super().write(vals)
        (templates | self.mapped("product_tmpl_id"))._queue_shop_product_sync(
            "custom_attribute.updated"
        )
        return result

    def unlink(self):
        templates = self.mapped("product_tmpl_id")
        result = super().unlink()
        templates.exists()._queue_shop_product_sync("custom_attribute.updated")
        return result
