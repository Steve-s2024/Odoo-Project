import base64
import os
import time

from odoo import _, fields, models

from .api_client import StorefrontApiError


class StorefrontCatalogSync(models.AbstractModel):
    _name = "storefront.catalog.sync"
    _description = "Authoritative ERP storefront catalogue refresh"

    def _fetch_catalog(self, language):
        client = self.env["storefront.erp.client"]
        page = 1
        rows = {}
        expected_total = None
        while True:
            payload, meta = client.call("GET", "/api/v1/products", params={
                "language": language,
                "page": page,
                "page_size": 100,
            })
            expected_total = int((meta or {}).get("total") or 0)
            page_rows = payload or []
            for item in page_rows:
                external_id = str(item.get("id") or "").strip()
                if not external_id or external_id in rows:
                    raise StorefrontApiError(
                        _("ERP catalogue contains a missing or duplicate product identifier."),
                        code="invalid_catalogue_identifier", status=502,
                    )
                rows[external_id] = item
            if not page_rows or len(rows) >= expected_total:
                break
            page += 1
        if not rows or len(rows) != expected_total:
            raise StorefrontApiError(
                _("ERP catalogue pagination was incomplete."),
                code="incomplete_catalogue", status=502,
                details={"expected": expected_total, "received": len(rows)},
            )
        return rows

    def _validate_variant_mapping(self, catalog):
        Product = self.env["product.template"].sudo().with_context(active_test=False)
        for external_id, payload in catalog.items():
            product = Product.search([("shop_api_uuid", "=", external_id)], limit=1)
            variants = payload.get("variants") or []
            remote_ids = {str(item.get("id") or "").strip() for item in variants}
            if "" in remote_ids or len(remote_ids) != len(variants):
                raise StorefrontApiError(
                    _("ERP catalogue contains invalid variant identifiers."),
                    code="invalid_variant_identifiers", status=502,
                )
            if not product and len(variants) > 1:
                raise StorefrontApiError(
                    _("A new multi-variant ERP product needs an explicit attribute mapping."),
                    code="variant_mapping_required", status=409,
                    details={"product_id": external_id},
                )
            if product and len(variants) > 1:
                local_ids = set(product.product_variant_ids.mapped("shop_api_uuid"))
                if not remote_ids.issubset(local_ids):
                    raise StorefrontApiError(
                        _("ERP and storefront variant structures do not match."),
                        code="variant_mapping_mismatch", status=409,
                        details={"product_id": external_id},
                    )

    def _category(self, payload):
        category_payload = payload.get("category") or {}
        external_id = str(category_payload.get("id") or "").strip()
        if not external_id:
            return self.env["product.category"]
        Category = self.env["product.category"].sudo().with_context(active_test=False)
        category = Category.search([("shop_api_uuid", "=", external_id)], limit=1)
        values = {"name": category_payload.get("name") or _("ERP catalogue")}
        if category:
            category.write(values)
        else:
            category = Category.create({"shop_api_uuid": external_id, **values})
        return category

    def _upsert_product(self, payload_zh, payload_en):
        external_id = payload_zh["id"]
        Product = self.env["product.template"].sudo().with_context(
            lang="zh_CN", active_test=False, shop_api_skip_event=True, tracking_disable=True,
        )
        product = Product.search([("shop_api_uuid", "=", external_id)], limit=1)
        category = self._category(payload_zh)
        values = {
            "name": payload_zh.get("name_zh") or payload_zh.get("name") or external_id,
            "x_website_english_name": payload_en.get("name_en") or payload_en.get("name") or "",
            "x_website_description_zh": payload_zh.get("description_zh") or "",
            "x_website_description_en": payload_en.get("description_en") or "",
            "list_price": float(payload_zh.get("price_cny") or 0.0),
            "x_website_usd_price": float(payload_en.get("price_usd") or 0.0),
            "website_published": bool(payload_zh.get("published")),
            "sale_ok": bool(payload_zh.get("sale_ok")),
            "x_shop_group_cover": bool(payload_zh.get("group_cover")),
            "active": True,
        }
        if category:
            values["categ_id"] = category.id
        variants = payload_zh.get("variants") or []
        if variants:
            values["default_code"] = variants[0].get("sku") or False
        if product:
            product.write(values)
        else:
            product = Product.create({"shop_api_uuid": external_id, **values})

        if values["x_shop_group_cover"]:
            # The ERP guarantees one selected cover per same-name group.  Also
            # clear any obsolete local flag immediately so a partially applied
            # event batch can never display two representatives.
            stale_covers = product._get_all_shop_group_siblings().filtered(
                lambda sibling: sibling != product and sibling.x_shop_group_cover
            )
            if stale_covers:
                stale_covers.with_context(
                    shop_api_skip_event=True, tracking_disable=True,
                ).write({"x_shop_group_cover": False})

        material_type = payload_zh.get("material_type")
        if material_type and material_type in dict(product._fields["x_material_type"].selection):
            product.x_material_type = material_type

        for variant_payload in variants:
            variant_id = str(variant_payload.get("id") or "").strip()
            variant = product.product_variant_ids.filtered(
                lambda item: item.shop_api_uuid == variant_id
            )[:1]
            if not variant and len(product.product_variant_ids) == 1 and len(variants) == 1:
                variant = product.product_variant_id
            if not variant:
                raise StorefrontApiError(
                    _("ERP and storefront variant structures do not match."),
                    code="variant_mapping_mismatch", status=409,
                    details={"product_id": external_id, "variant_id": variant_id},
                )
            variant.with_context(shop_api_skip_event=True, tracking_disable=True).write({
                "shop_api_uuid": variant_id,
                "default_code": variant_payload.get("sku") or False,
            })
        return product

    def _sync_media(self, product, images, delay):
        client = self.env["storefront.erp.client"]
        Image = self.env["product.image"].sudo().with_context(
            shop_api_skip_event=True, tracking_disable=True,
        )
        downloaded = 0
        for image in images or []:
            image_id = str(image.get("id") or "").strip()
            image_url = str(image.get("url") or "").strip()
            if not image_id or not image_url:
                raise StorefrontApiError(
                    _("ERP returned invalid product media metadata."),
                    code="invalid_media_metadata", status=502,
                )
            binary = base64.b64encode(client.get_binary(image_url))
            if image.get("kind") == "cover":
                product.with_context(
                    shop_api_skip_event=True, tracking_disable=True,
                ).image_1920 = binary
            else:
                Image.create({
                    "shop_api_uuid": image_id,
                    "name": image.get("name") or f"ERP image {image_id}",
                    "product_tmpl_id": product.id,
                    "image_1920": binary,
                    "sequence": int(image.get("sequence") or 0),
                })
            downloaded += 1
            if delay:
                time.sleep(delay)
        return downloaded

    def full_refresh_from_erp(self):
        """Replace cache-owned catalogue/media content in one DB transaction."""
        catalog_zh = self._fetch_catalog("zh_CN")
        catalog_en = self._fetch_catalog("en_US")
        if set(catalog_zh) != set(catalog_en):
            raise StorefrontApiError(
                _("ERP Chinese and English catalogues do not contain the same products."),
                code="catalogue_language_mismatch", status=502,
            )
        self._validate_variant_mapping(catalog_zh)

        remote_ids = list(catalog_zh)
        Product = self.env["product.template"].sudo().with_context(
            active_test=False, shop_api_skip_event=True, tracking_disable=True,
        )
        Image = self.env["product.image"].sudo().with_context(
            active_test=False, shop_api_skip_event=True, tracking_disable=True,
        )
        Cache = self.env["storefront.cache.entry"].sudo()

        # This method intentionally leaves website views/themes, editor assets,
        # users, orders, configuration and secrets untouched.
        Cache.search([]).unlink()
        Image.search([]).unlink()
        products_with_images = Product.search([("image_1920", "!=", False)])
        if products_with_images:
            products_with_images.write({"image_1920": False})
        stale_products = Product.search([
            ("shop_api_uuid", "not in", remote_ids),
            ("website_published", "=", True),
        ])
        if stale_products:
            stale_products.write({"website_published": False})

        delay = max(0.0, min(
            float(os.environ.get("STOREFRONT_MEDIA_SYNC_DELAY_SECONDS", "0.55")), 2.0,
        ))
        downloaded = 0
        for external_id in remote_ids:
            payload_zh = catalog_zh[external_id]
            payload_en = catalog_en[external_id]
            product = self._upsert_product(payload_zh, payload_en)
            Cache.upsert(
                "product", external_id, payload_zh, language="zh_CN",
                version=payload_zh.get("version"),
            )
            Cache.upsert(
                "product", external_id, payload_en, language="en_US",
                version=payload_en.get("version"),
            )
            downloaded += self._sync_media(product, payload_zh.get("images") or [], delay)

        inventory = self.env["storefront.erp.client"].refresh_inventory_snapshot()
        Cache.upsert("catalog_sync", "latest", {
            "products": len(remote_ids),
            "media": downloaded,
            "inventory_rows": len(inventory),
        }, version=fields.Datetime.to_string(fields.Datetime.now()))
        return {
            "products": len(remote_ids),
            "media": downloaded,
            "inventory_rows": len(inventory),
            "unpublished_stale_products": len(stale_products),
        }
