import base64
import json
import time
import uuid
from datetime import timedelta
from urllib.parse import urlparse

from psycopg2.errors import LockNotAvailable

from odoo import Command, fields
from odoo.exceptions import AccessDenied, AccessError, MissingError, UserError, ValidationError
from odoo.http import Controller, content_disposition, request, route
from odoo.tools import SQL
from odoo.tools.mimetypes import guess_mimetype


class ShopApiError(Exception):
    def __init__(self, code, message, status=400, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


class ShopApiController(Controller):
    @staticmethod
    def _active_credential_user(submitted_login):
        submitted_login = str(submitted_login or "").strip()
        if not submitted_login:
            return request.env["res.users"]
        Users = request.env["res.users"].sudo().with_context(active_test=False)
        user = Users.search([
            ("login", "=", submitted_login),
            ("active", "=", True),
        ], limit=1)
        if not user and "@" in submitted_login:
            candidates = Users.search([
                ("partner_id.email", "=ilike", submitted_login),
                ("active", "=", True),
            ], limit=2)
            user = candidates if len(candidates) == 1 else Users.browse()
        is_anonymous_website_user = bool(
            user
            and request.env["website"].sudo().search_count([
                ("user_id", "=", user.id),
            ], limit=1)
        )
        if (
            not user
            # Portal users can inherit the public group.  Identify anonymous
            # users by the website's configured public-user record instead of
            # group inheritance, otherwise valid customer credentials are
            # rejected before ERP ever verifies their password.
            or is_anonymous_website_user
            or user.has_group("shop_api.group_shop_api_integration")
        ):
            return Users.browse()
        return user

    @staticmethod
    def _body():
        if not request.httprequest.data:
            return {}
        payload = request.httprequest.get_json(silent=True)
        if payload is None:
            raise ShopApiError("invalid_json", "请求正文必须是有效的 JSON。", 400)
        if not isinstance(payload, dict):
            raise ShopApiError("invalid_payload", "JSON 请求正文必须是对象。", 400)
        return payload

    @staticmethod
    def _response(data=None, status=200, request_id=None, meta=None):
        payload = {"data": data, "request_id": request_id}
        if meta is not None:
            payload["meta"] = meta
        return request.make_json_response(payload, status=status)

    @staticmethod
    def _error(code, message, status, request_id, details=None):
        return request.make_json_response({
            "error": {"code": code, "message": message, "details": details or {}},
            "request_id": request_id,
        }, status=status)

    def _run(self, endpoint_code, handler):
        started_at = time.monotonic()
        request_id = (request.httprequest.headers.get("X-Request-Id") or str(uuid.uuid4()))[:128]
        method = request.httprequest.method.upper()
        path = request.httprequest.path
        body = {}
        log = None
        idempotency = None
        try:
            endpoint = request.env["shop.api.endpoint"].sudo().search([
                ("code", "=", endpoint_code), ("active", "=", True),
            ], limit=1)
            if not endpoint:
                raise ShopApiError("endpoint_disabled", "该 API 接口未启用。", 503)
            client = request.env["shop.api.client"]._client_for_current_user()
            if not client:
                raise ShopApiError("client_not_configured", "当前密钥没有绑定有效的 API 客户端。", 403)
            scope_code = endpoint.scope_id.code
            if not client.allows_scope(scope_code):
                raise ShopApiError("scope_denied", "API 客户端没有此操作所需权限。", 403)
            if endpoint.rate_limit_per_minute and request.env["shop.api.request.log"].sudo().search_count([
                ("client_id", "=", client.id),
                ("endpoint_id", "=", endpoint.id),
                ("create_date", ">=", fields.Datetime.now() - timedelta(minutes=1)),
            ]) >= min(endpoint.rate_limit_per_minute, client.rate_limit_per_minute):
                raise ShopApiError("rate_limit_exceeded", "请求过于频繁，请稍后重试。", 429)

            body = self._body() if method in ("POST", "PATCH", "PUT", "DELETE") else {}
            key = request.httprequest.headers.get("Idempotency-Key")
            if endpoint.idempotency_required:
                if not key:
                    raise ShopApiError("idempotency_key_required", "此操作必须提供 Idempotency-Key。", 400)
                idempotency, replay = request.env["shop.api.idempotency"].begin(
                    client, key[:255], method, path, body,
                )
                if replay:
                    return request.make_json_response(
                        idempotency.response_body, status=idempotency.response_status,
                    )

            log = request.env["shop.api.request.log"].start_request(
                client, endpoint, request_id, method, path,
                request.httprequest.remote_addr, body, key=key,
            )
            # Request logs are the authoritative usage audit. Writing the same
            # client row on every request makes harmless parallel reads contend
            # and can raise PostgreSQL serialization errors (for example when a
            # checkout loads countries and payment methods concurrently).
            # Keep the audit/idempotency records outside the business savepoint. Any
            # failed command rolls back all business mutations while its failure log
            # and deterministic response remain available for a safe retry.
            with request.env.cr.savepoint():
                data, status, meta = handler(body, client)
            response_payload = {"data": data, "request_id": request_id}
            if meta is not None:
                response_payload["meta"] = meta
            if idempotency:
                idempotency.complete(status, response_payload)
            log.finish_request(status, response_payload, started_at)
            return request.make_json_response(response_payload, status=status)
        except ShopApiError as error:
            payload = {
                "error": {"code": error.code, "message": error.message, "details": error.details},
                "request_id": request_id,
            }
            if idempotency:
                idempotency.complete(error.status, payload)
            if log:
                log.finish_request(
                    error.status, payload, started_at,
                    error_code=error.code, error_message=error.message,
                )
            return request.make_json_response(payload, status=error.status)
        except (UserError, ValidationError) as error:
            code = "business_rule_failed"
            status = 409
            payload = {
                "error": {"code": code, "message": str(error), "details": {}},
                "request_id": request_id,
            }
            if idempotency:
                idempotency.complete(status, payload)
            if log:
                log.finish_request(status, payload, started_at, error_code=code, error_message=str(error))
            return request.make_json_response(payload, status=status)
        except (AccessError, MissingError) as error:
            status = 403 if isinstance(error, AccessError) else 404
            code = "access_denied" if status == 403 else "not_found"
            payload = {
                "error": {"code": code, "message": str(error), "details": {}},
                "request_id": request_id,
            }
            if idempotency:
                idempotency.complete(status, payload)
            if log:
                log.finish_request(status, payload, started_at, error_code=code, error_message=str(error))
            return request.make_json_response(payload, status=status)
        except Exception as error:
            status = 500
            code = "internal_error"
            payload = {
                "error": {
                    "code": code,
                    "message": "服务器处理请求时发生错误。",
                    "details": {"type": type(error).__name__},
                },
                "request_id": request_id,
            }
            if idempotency:
                idempotency.complete(status, payload)
            if log:
                log.finish_request(
                    status, payload, started_at,
                    error_code=code, error_message=str(error),
                )
            return request.make_json_response(payload, status=status)

    @staticmethod
    def _pagination():
        configuration = request.env["shop.api.configuration"].sudo().search([
            ("active", "=", True),
        ], limit=1) or request.env["shop.api.configuration"].sudo()._ensure_default_configuration()
        args = request.httprequest.args
        try:
            page = max(int(args.get("page", 1)), 1)
            page_size = max(int(args.get("page_size", configuration.default_page_size)), 1)
        except ValueError as error:
            raise ShopApiError("invalid_pagination", "分页参数必须是整数。", 400) from error
        page_size = min(page_size, configuration.maximum_page_size)
        return page, page_size

    @staticmethod
    def _record(model, uuid_value, domain=None):
        record = request.env[model].sudo().search([
            ("shop_api_uuid", "=", uuid_value), *(domain or []),
        ], limit=1)
        if not record:
            raise ShopApiError("not_found", "找不到所请求的资源。", 404)
        return record

    @route("/api/v1/health", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def health(self):
        return request.make_json_response({
            "data": {"status": "ok", "service": "shop_api", "version": "v1"},
            "request_id": str(uuid.uuid4()),
        })

    @route("/api/v1/capabilities", type="http", auth="bearer", methods=["GET"], csrf=False)
    def capabilities(self):
        return self._run("capabilities", lambda _body, _client: ({
            "api_version": "v1",
            "features": [
                "catalog", "inventory", "reservations", "customers", "orders",
                "payments", "shipments", "refunds", "events", "reconciliation",
            ],
        }, 200, None))

    @route("/api/v1/shop/configuration", type="http", auth="bearer", methods=["GET"], csrf=False)
    def shop_configuration(self):
        def handler(_body, client):
            config = request.env["shop.api.reservation"]._configuration_for_client(client)
            return {
                "environment": config.environment,
                "api_version": config.api_version,
                "reservation_ttl_minutes": config.reservation_ttl_minutes,
                "languages": ["zh_CN", "en_US"],
                "currencies": [code for code in ("CNY", "USD") if request.env["res.currency"].sudo().search_count([("name", "=", code), ("active", "=", True)])],
                "media_cache_ttl_seconds": config.media_cache_ttl_seconds,
                "shop_base_url": client.shop_base_url or config.shop_base_url,
                "payment_return_origins": sorted(config.payment_return_origins()),
            }, 200, None
        return self._run("shop_configuration", handler)

    @staticmethod
    def _site_language():
        language = request.httprequest.args.get("language", "zh_CN")
        return language if language in ("zh_CN", "en_US") else "zh_CN"

    @staticmethod
    def _site_website(client):
        website = client.website_ids[:1]
        if not website:
            website = request.env["website"].sudo().search([], limit=1)
        if not website:
            raise ShopApiError("site_not_configured", "网站尚未配置。", 503)
        return website

    @route("/api/v1/site/configuration", type="http", auth="bearer", methods=["GET"], csrf=False)
    def site_configuration(self):
        def handler(_body, client):
            website = self._site_website(client)
            language = self._site_language()
            return {
                "id": str(website.id),
                "name": website.with_context(lang=language).name,
                "language": language,
                "languages": ["zh_CN", "en_US"],
                "home_url": "/en" if language == "en_US" else "/",
                "legacy_redirects": {
                    "/shop": "/collections",
                    "/my/orders": "/purchase-history",
                    "/en/product-categories": "/en/collections",
                },
            }, 200, None
        return self._run("site_configuration", handler)

    @route("/api/v1/site/navigation", type="http", auth="bearer", methods=["GET"], csrf=False)
    def site_navigation(self):
        def handler(_body, client):
            website = self._site_website(client)
            language = self._site_language()
            menus = request.env["website.menu"].sudo().with_context(lang=language).search([
                ("website_id", "in", [False, website.id]),
            ], order="sequence, id")
            return [{
                "id": str(menu.id),
                "parent_id": str(menu.parent_id.id) if menu.parent_id else None,
                "name": menu.name,
                "url": menu.url or "#",
                "sequence": menu.sequence,
            } for menu in menus], 200, None
        return self._run("site_navigation", handler)

    @staticmethod
    def _site_page_payload(page, language, detail=False):
        localized = page.with_context(lang=language)
        view = page.view_id.with_context(lang=language)
        payload = {
            "id": str(page.id),
            "name": localized.name,
            "url": page.url,
            "language": language,
            "published": bool(getattr(page, "is_published", True)),
            "version": fields.Datetime.to_string(page.write_date),
            "view_key": view.key or "",
        }
        if detail:
            payload["qweb_arch"] = view.arch_db or ""
        return payload

    @route("/api/v1/site/pages", type="http", auth="bearer", methods=["GET"], csrf=False)
    def site_pages(self):
        def handler(_body, client):
            website = self._site_website(client)
            language = self._site_language()
            pages = request.env["website.page"].sudo().search([
                ("website_id", "in", [False, website.id]),
            ], order="url, id")
            return [self._site_page_payload(page, language) for page in pages], 200, None
        return self._run("site_pages", handler)

    @route("/api/v1/site/pages/<int:page_id>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def site_page_detail(self, page_id):
        def handler(_body, client):
            website = self._site_website(client)
            page = request.env["website.page"].sudo().search([
                ("id", "=", page_id), ("website_id", "in", [False, website.id]),
            ], limit=1)
            if not page:
                raise ShopApiError("not_found", "找不到网站页面。", 404)
            return self._site_page_payload(page, self._site_language(), detail=True), 200, None
        return self._run("site_page_detail", handler)

    @route("/api/v1/site/legacy-routes", type="http", auth="bearer", methods=["GET"], csrf=False)
    def site_legacy_routes(self):
        def handler(_body, _client):
            products = request.env["product.template"].sudo().search([
                ("sale_ok", "=", True), ("website_published", "=", True), ("active", "=", True),
            ])
            routes = [
                {"source": "/shop", "target": "/collections", "status": 302},
                {"source": "/my/orders", "target": "/purchase-history", "status": 302},
                {"source": "/en/product-categories", "target": "/en/collections", "status": 302},
            ]
            for product in products:
                product._shop_api_ensure_uuid()
                for language, prefix in (("zh_CN", ""), ("en_US", "/en")):
                    localized = product.with_context(lang=language)
                    website_url = localized.website_url or f"/shop/product-{product.id}"
                    source = website_url if website_url.startswith(prefix) else f"{prefix}{website_url}"
                    routes.append({
                        "source": source,
                        "target": f"{prefix}/product/{product.shop_api_uuid}" or "/",
                        "status": 200,
                        "product_id": product.shop_api_uuid,
                        "language": language,
                    })
            return routes, 200, None
        return self._run("site_legacy_routes", handler)

    @route("/api/v1/countries", type="http", auth="bearer", methods=["GET"], csrf=False)
    def countries(self):
        return self._run("countries", lambda _body, _client: ([
            {
                "code": country.code,
                "name": country.name,
                "available_for_zh": request.env["sale.order"]._is_website_checkout_country_allowed(
                    country, False,
                ),
                "available_for_en": request.env["sale.order"]._is_website_checkout_country_allowed(
                    country, True,
                ),
            }
            for country in request.env["res.country"].sudo().search([])
        ], 200, None))

    @route("/api/v1/shipping-methods", type="http", auth="bearer", methods=["GET"], csrf=False)
    def shipping_methods(self):
        def handler(_body, _client):
            language = str(
                request.httprequest.args.get("language")
                or request.httprequest.args.get("lang")
                or "zh_CN"
            ).strip()
            language = language if language in ("zh_CN", "en_US") else "zh_CN"
            carriers = request.env["delivery.carrier"].sudo().search([("active", "=", True)])
            carriers._shop_api_ensure_uuid()
            return [{
                "id": carrier.shop_api_uuid,
                "name": carrier._shop_api_display_name(language=language),
                "delivery_type": carrier.delivery_type,
            } for carrier in carriers], 200, None
        return self._run("shipping_methods", handler)

    @route("/api/v1/payment-methods", type="http", auth="bearer", methods=["GET"], csrf=False)
    def payment_methods(self):
        def handler(_body, _client):
            language = str(request.httprequest.args.get("lang") or "zh_CN").strip()
            language = language if language in ("zh_CN", "en_US") else "zh_CN"
            standard_names = {
                "transfer": {"zh_CN": "银行转账", "en_US": "Bank Transfer"},
                "alipay": {"zh_CN": "支付宝", "en_US": "Alipay"},
                "wechatpay": {"zh_CN": "微信支付", "en_US": "WeChat Pay"},
            }
            providers = request.env["payment.provider"].sudo().search([
                ("state", "in", ("enabled", "test")),
            ], order="state asc, id desc")
            providers = providers.filtered(
                lambda provider: provider.code != "custom"
                and not self._is_cash_on_delivery_provider(provider)
            )
            # A database may retain an older test provider beside the enabled
            # provider. Expose one deterministic choice per provider code.
            providers_by_code = {}
            for provider in providers:
                existing = providers_by_code.get(provider.code)
                if not existing or (existing.state != "enabled" and provider.state == "enabled"):
                    providers_by_code[provider.code] = provider
            providers = providers.browse(
                [provider.id for provider in providers_by_code.values()]
            )
            methods = [{
                "code": provider.code,
                "name": standard_names.get(provider.code, {}).get(language)
                        or provider.with_context(lang=language).name,
                "language": language,
                "state": provider.state,
                "currencies": provider._get_supported_currencies().mapped("name"),
                "supports_refund": bool(provider.support_refund),
                "available": True,
                "shell": False,
            } for provider in providers]
            methods.append({
                "code": "bank_card",
                "name": "Bank card (coming soon)" if language == "en_US" else "银行卡支付（即将开放）",
                "language": language,
                "state": "disabled",
                "currencies": ["CNY", "USD"],
                "supports_refund": False,
                "available": False,
                "shell": True,
            })
            return methods, 200, None
        return self._run("payment_methods", handler)

    @staticmethod
    def _is_cash_on_delivery_provider(provider):
        names = {
            str(provider.with_context(lang=language).name or "").strip().casefold()
            for language in ("en_US", "zh_CN")
        }
        return bool(names.intersection({"cash on delivery", "货到付款"}))

    @route("/api/v1/openapi.json", type="http", auth="bearer", methods=["GET"], csrf=False)
    def openapi(self):
        def handler(_body, _client):
            paths = {}
            for endpoint in request.env["shop.api.endpoint"].sudo().search([("active", "=", True)]):
                paths.setdefault(endpoint.path, {})[endpoint.method.lower()] = {
                    "operationId": endpoint.code,
                    "summary": endpoint.name,
                    "description": endpoint.summary_en or "",
                    "security": [{"bearerAuth": []}] if endpoint.authentication_required else [],
                }
            return {
                "openapi": "3.1.0",
                "info": {"title": "Shop APIs", "version": "1.0.0"},
                "paths": paths,
                "components": {"securitySchemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer"},
                }},
            }, 200, None
        return self._run("openapi", handler)

    def _product_list(self, endpoint_code="products", changes=False, language="zh_CN"):
        def handler(_body, _client):
            page, page_size = self._pagination()
            domain = [("sale_ok", "=", True), ("website_published", "=", True), ("active", "=", True)]
            since = request.httprequest.args.get("since")
            if changes and since:
                domain.append(("write_date", ">", fields.Datetime.to_datetime(since)))
            Product = request.env["product.template"].sudo()
            total = Product.search_count(domain)
            products = Product.search(domain, offset=(page - 1) * page_size, limit=page_size, order="write_date, id")
            return [product._shop_api_payload(language=language, detail=False) for product in products], 200, {
                "page": page, "page_size": page_size, "total": total,
            }
        return self._run(endpoint_code, handler)

    @route("/api/v1/products", type="http", auth="bearer", methods=["GET"], csrf=False)
    def products(self, language="zh_CN"):
        return self._product_list(language=language)

    @route("/api/v1/products/changes", type="http", auth="bearer", methods=["GET"], csrf=False)
    def product_changes(self, language="zh_CN"):
        return self._product_list("product_changes", changes=True, language=language)

    @route("/api/v1/products/<string:uuid_value>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def product_detail(self, uuid_value, language="zh_CN"):
        return self._run("product_detail", lambda _body, _client: (
            self._record("product.template", uuid_value)._shop_api_payload(
                language=language, detail=True,
            ), 200, None,
        ))

    @route("/api/v1/product-groups", type="http", auth="bearer", methods=["GET"], csrf=False)
    def product_groups(self, language="zh_CN"):
        def handler(_body, _client):
            products = request.env["product.template"].sudo().search([
                ("sale_ok", "=", True), ("website_published", "=", True), ("active", "=", True),
            ])._get_shop_grouped_products()
            return [product._shop_api_payload(language=language, detail=False) for product in products], 200, None
        return self._run("product_groups", handler)

    @route("/api/v1/categories", type="http", auth="bearer", methods=["GET"], csrf=False)
    def categories(self):
        return self._run("categories", lambda _body, _client: ([
            category._shop_api_summary() for category in request.env["product.category"].sudo().search([])
        ], 200, None))

    @route("/api/v1/attributes", type="http", auth="bearer", methods=["GET"], csrf=False)
    def attributes(self):
        def handler(_body, _client):
            attributes = request.env["product.attribute"].sudo().search([])
            attributes._shop_api_ensure_uuid()
            attributes.mapped("value_ids")._shop_api_ensure_uuid()
            return [{
                "id": attribute.shop_api_uuid,
                "name": attribute.name,
                "values": [
                    {"id": value.shop_api_uuid, "name": value.name}
                    for value in attribute.value_ids
                ],
            } for attribute in attributes], 200, None
        return self._run("attributes", handler)

    @route("/api/v1/pricelists/<string:uuid_value>/prices", type="http", auth="bearer", methods=["GET"], csrf=False)
    def pricelist_prices(self, uuid_value):
        def handler(_body, _client):
            pricelist = self._record("product.pricelist", uuid_value)
            products = request.env["product.template"].sudo().search([
                ("sale_ok", "=", True), ("website_published", "=", True),
            ])
            return [{
                "product_id": product.shop_api_uuid,
                "price": pricelist._get_product_price(product.product_variant_id, 1.0),
                "currency": pricelist.currency_id.name,
            } for product in products], 200, None
        return self._run("pricelist_prices", handler)

    @route("/api/v1/products/<string:uuid_value>/images", type="http", auth="bearer", methods=["GET"], csrf=False)
    def product_images(self, uuid_value):
        return self._run("product_images", lambda _body, _client: (
            self._record("product.template", uuid_value)._shop_api_image_payloads(), 200, None,
        ))

    @route("/api/v1/media/<string:uuid_value>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def media(self, uuid_value):
        client = request.env["shop.api.client"]._client_for_current_user()
        endpoint = request.env["shop.api.endpoint"].sudo().search([("code", "=", "media"), ("active", "=", True)], limit=1)
        if not client or not endpoint or not client.allows_scope(endpoint.scope_id.code):
            return request.make_json_response({"error": {"code": "access_denied", "message": "无权读取媒体。"}}, status=403)
        product = request.env["product.template"].sudo().search([
            ("shop_api_uuid", "=", uuid_value),
        ], limit=1)
        image = product.image_1920 if product else False
        if not image:
            gallery = request.env["product.image"].sudo().search([
                ("shop_api_uuid", "=", uuid_value),
            ], limit=1)
            image = gallery.image_1920 if gallery else False
        if not image:
            return request.make_json_response({"error": {"code": "not_found", "message": "找不到图片。"}}, status=404)
        binary = base64.b64decode(image)
        return request.make_response(binary, headers=[
            ("Content-Type", guess_mimetype(binary, default="application/octet-stream")),
            ("Cache-Control", "private, max-age=86400"),
        ])

    @route("/api/v1/inventory/check", type="http", auth="bearer", methods=["POST"], csrf=False)
    def inventory_check(self):
        return self._run("inventory_check", lambda body, client: (
            request.env["shop.api.reservation"].check_inventory(body.get("items"), client), 200, None,
        ))

    @route("/api/v1/inventory/snapshot", type="http", auth="bearer", methods=["GET"], csrf=False)
    def inventory_snapshot(self):
        return self._run("inventory_snapshot", lambda _body, _client: (
            request.env["product.template"]._shop_api_inventory_snapshot(), 200, None,
        ))

    @route("/api/v1/checkout/quote", type="http", auth="bearer", methods=["POST"], csrf=False)
    def checkout_quote(self):
        def handler(body, client):
            language = body.get("language") if body.get("language") in ("zh_CN", "en_US") else "zh_CN"
            external_id = str(body.get("external_id") or "").strip()
            if not external_id:
                raise ShopApiError("external_id_required", "结账报价 external_id 不能为空。", 400)
            reservation = request.env["shop.api.reservation"].create_reservation(client, {
                "external_id": external_id,
                "items": body.get("items") or [],
            })
            total = 0.0
            items = []
            currency = "USD" if language == "en_US" else request.env.company.currency_id.name
            for line in reservation.line_ids:
                template = line.product_id.product_tmpl_id
                unit_price = template.x_website_usd_price if language == "en_US" else template.list_price
                subtotal = unit_price * line.quantity
                total += subtotal
                items.append({
                    **line._shop_api_payload(),
                    "unit_price": unit_price,
                    "subtotal": subtotal,
                    "currency": currency,
                })
            return {
                "id": reservation.name,
                "reservation_id": reservation.name,
                "expires_at": fields.Datetime.to_string(reservation.expires_at),
                "language": language,
                "currency": currency,
                "items": items,
                "amount_untaxed": total,
                "amount_tax": 0.0,
                "shipping_amount": 0.0,
                "amount_total": total,
                "authoritative": True,
            }, 201, None
        return self._run("checkout_quote", handler)

    @route("/api/v1/inventory/changes", type="http", auth="bearer", methods=["GET"], csrf=False)
    def inventory_changes(self):
        def handler(_body, _client):
            since = request.httprequest.args.get("since")
            domain = [("location_id.usage", "=", "internal")]
            if since:
                domain.append(("write_date", ">", fields.Datetime.to_datetime(since)))
            quants = request.env["stock.quant"].sudo().search(domain, order="write_date, id", limit=1000)
            rows = {}
            for quant in quants:
                quant.product_id._shop_api_ensure_uuid()
                key = (quant.product_id.shop_api_uuid, quant.location_id.id)
                rows[key] = {
                    "product_id": quant.product_id.shop_api_uuid,
                    "location": quant.location_id.complete_name,
                    "available_quantity": quant.available_quantity,
                    "version": fields.Datetime.to_string(quant.write_date),
                }
            return list(rows.values()), 200, {"count": len(rows)}
        return self._run("inventory_changes", handler)

    @route("/api/v1/reservations", type="http", auth="bearer", methods=["POST"], csrf=False)
    def reservation_create(self):
        return self._run("reservation_create", lambda body, client: (
            request.env["shop.api.reservation"].create_reservation(client, body)._shop_api_payload(), 201, None,
        ))

    @route("/api/v1/reservations/<string:reservation_uuid>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def reservation_detail(self, reservation_uuid):
        def handler(_body, client):
            record = request.env["shop.api.reservation"].sudo().search([
                ("name", "=", reservation_uuid), ("client_id", "=", client.id),
            ], limit=1)
            if not record:
                raise ShopApiError("not_found", "找不到库存预留。", 404)
            return record._shop_api_payload(), 200, None
        return self._run("reservation_detail", handler)

    def _reservation_action(self, code, reservation_uuid, action):
        def handler(body, client):
            reservation = request.env["shop.api.reservation"].sudo().search([
                ("name", "=", reservation_uuid), ("client_id", "=", client.id),
            ], limit=1)
            if not reservation:
                raise ShopApiError("not_found", "找不到库存预留。", 404)
            action(reservation, body, client)
            return reservation._shop_api_payload(), 200, None
        return self._run(code, handler)

    @route("/api/v1/reservations/<string:reservation_uuid>/extend", type="http", auth="bearer", methods=["POST"], csrf=False)
    def reservation_extend(self, reservation_uuid):
        return self._reservation_action(
            "reservation_extend", reservation_uuid,
            lambda reservation, body, _client: reservation.action_extend(body.get("minutes")),
        )

    @route("/api/v1/reservations/<string:reservation_uuid>/release", type="http", auth="bearer", methods=["POST"], csrf=False)
    def reservation_release(self, reservation_uuid):
        return self._reservation_action(
            "reservation_release", reservation_uuid,
            lambda reservation, _body, _client: reservation.action_release(),
        )

    @route("/api/v1/reservations/<string:reservation_uuid>/confirm", type="http", auth="bearer", methods=["POST"], csrf=False)
    def reservation_confirm(self, reservation_uuid):
        def action(reservation, body, client):
            order = self._order_record(body.get("order_id"), client)
            if order.state not in ("draft", "sent"):
                raise ShopApiError("order_not_editable", "只能绑定草稿订单。", 409)
            reservation.write({"state": "confirmed", "confirmed_order_id": order.id})
            request.env["shop.api.external.reference"].set_reference(
                client, "order", body.get("external_id") or order.name, order,
            )
        return self._reservation_action("reservation_confirm", reservation_uuid, action)

    def _upsert_customer_record(self, body, client):
        external_id = str(body.get("external_id") or "").strip()
        if not external_id:
            raise ShopApiError("external_id_required", "客户 external_id 不能为空。", 400)
        reference = request.env["shop.api.external.reference"].sudo().search([
            ("client_id", "=", client.id), ("resource_type", "=", "customer"),
            ("external_id", "=", external_id),
        ], limit=1)
        partner = reference.resolve() if reference else request.env["res.partner"]
        values = {
            "name": body.get("name") or body.get("email") or "Shop customer",
            "email": body.get("email") or False,
            "phone": body.get("phone") or False,
            "lang": body.get("language") if body.get("language") in ("zh_CN", "en_US") else "zh_CN",
            "customer_rank": 1,
        }
        if partner:
            partner.sudo().write(values)
        else:
            partner = request.env["res.partner"].sudo().create(values)
        partner._shop_api_ensure_uuid()
        request.env["shop.api.external.reference"].set_reference(
            client, "customer", external_id, partner,
        )
        return partner

    @route("/api/v1/customers/upsert", type="http", auth="bearer", methods=["POST"], csrf=False)
    def customer_upsert(self):
        return self._run("customer_upsert", lambda body, client: (
            self._upsert_customer_record(body, client)._shop_api_payload(), 200, None,
        ))

    @route("/api/v1/customers/authenticate", type="http", auth="bearer", methods=["POST"], csrf=False)
    def customer_authenticate(self):
        """Retire the previous credential bridge explicitly.

        This route intentionally does not parse or log the submitted body.  It
        remains only as a deterministic migration signal for an outdated Shop.
        """
        request_id = (
            request.httprequest.headers.get("X-Request-Id") or str(uuid.uuid4())
        )[:128]
        return self._error(
            "legacy_auth_endpoint_retired",
            "旧版登录接口已停用，请使用 Odoo 原生登录接口。",
            410,
            request_id,
        )

    @route(
        "/api/v2/native-auth/login",
        type="http",
        auth="bearer",
        methods=["POST"],
        csrf=False,
    )
    def native_login(self):
        """Authenticate against ERP's unmodified Odoo credential backend.

        Passwords are accepted only over the authenticated server-to-server
        channel, are redacted by the API audit layer, and never leave ERP in a
        response.  The Shop receives only the authoritative identity/profile
        required to create its own Odoo session.
        """
        def handler(body, client):
            submitted_login = str(body.get("login") or "").strip()
            password = body.get("password") or ""
            if not submitted_login or not password:
                raise ShopApiError("invalid_credentials", "用户名或密码错误。", 401)

            user = self._active_credential_user(submitted_login)
            if not user:
                raise ShopApiError("invalid_credentials", "用户名或密码错误。", 401)

            credential = {
                "type": "password",
                "login": user.login,
                "password": password,
            }
            user_agent = {
                "interactive": True,
                "base_location": request.httprequest.url_root.rstrip("/"),
                "HTTP_HOST": request.httprequest.host,
                "REMOTE_ADDR": request.httprequest.remote_addr,
            }
            try:
                auth_info = request.env["res.users"].authenticate(
                    credential, user_agent
                )
            except AccessDenied:
                raise ShopApiError("invalid_credentials", "用户名或密码错误。", 401) from None
            if auth_info.get("mfa") != "skip" and user._mfa_url():
                raise ShopApiError(
                    "mfa_required",
                    "此账户启用了双重验证，暂时无法通过商城登录。",
                    409,
                )

            customer = user.partner_id.commercial_partner_id.sudo()
            customer._shop_api_ensure_uuid()
            request.env["shop.api.external.reference"].set_reference(
                client, "customer", f"erp-account-{user.id}", customer,
            )
            return {
                **customer._shop_api_payload(),
                "authoritative": True,
                "authentication_backend": "odoo_native",
                "authentication_version": 2,
                "login": user.login,
                "is_internal": user._is_internal(),
                "website_editor": bool(
                    user._is_internal()
                    and (
                        user.has_group("base.group_system")
                        or user.has_group("website.group_website_designer")
                        or user.has_group("website.group_website_restricted_editor")
                    )
                ),
            }, 200, None

        return self._run("native_login", handler)

    @route("/api/v1/customers/register", type="http", auth="bearer", methods=["POST"], csrf=False)
    def customer_register(self):
        def handler(body, client):
            login = str(body.get("login") or body.get("email") or "").strip().lower()
            name = str(body.get("name") or "").strip()
            password = str(body.get("password") or "")
            language = body.get("language") if body.get("language") in ("zh_CN", "en_US") else "zh_CN"
            missing_fields = []
            if not login or "@" not in login:
                missing_fields.append("email")
            if not password:
                missing_fields.append("password")
            if missing_fields:
                raise ShopApiError(
                    "invalid_registration",
                    "有效电子邮箱和密码不能为空。",
                    400,
                    details={"missing_fields": missing_fields},
                )
            name = name or login.partition("@")[0]
            Users = request.env["res.users"].sudo().with_context(active_test=False)
            if Users.search_count([
                "|", ("login", "=ilike", login), ("partner_id.email", "=ilike", login),
            ], limit=1):
                raise ShopApiError(
                    "account_exists",
                    "此电子邮箱已注册，请直接登录。",
                    409,
                )
            partner = request.env["res.partner"].sudo().create({
                "name": name,
                "email": login,
                "lang": language,
                "customer_rank": 1,
            })
            user = Users.with_context(no_reset_password=True).create({
                "name": name,
                "login": login,
                "password": password,
                "partner_id": partner.id,
                "group_ids": [Command.set([request.env.ref("base.group_portal").id])],
            })
            if self._active_credential_user(user.login) != user:
                raise ShopApiError(
                    "registration_not_verified",
                    "ERP 未能将新账户识别为有效登录账户，注册已回滚。",
                    500,
                )
            try:
                user.with_user(user).sudo()._check_credentials({
                    "type": "password",
                    "login": user.login,
                    "password": password,
                }, {"interactive": True})
            except AccessDenied:
                raise ShopApiError(
                    "registration_not_verified",
                    "ERP 未能验证新账户密码，注册已回滚。",
                    500,
                ) from None
            customer = user.partner_id.commercial_partner_id.sudo()
            customer._shop_api_ensure_uuid()
            request.env["shop.api.external.reference"].set_reference(
                client, "customer", f"erp-account-{user.id}", customer,
            )
            return {
                **customer._shop_api_payload(),
                "authoritative": True,
                "registered": True,
                "login": user.login,
            }, 201, None
        return self._run("customer_register", handler)

    @route(
        "/api/v1/customers/password-reset/request",
        type="http",
        auth="bearer",
        methods=["POST"],
        csrf=False,
    )
    def customer_password_reset_request(self):
        def handler(body, client):
            submitted_login = str(body.get("login") or body.get("email") or "").strip()
            if not submitted_login:
                raise ShopApiError(
                    "login_required",
                    "请输入登录名或电子邮箱。",
                    400,
                )

            # Deliberately return the same accepted response for an unknown,
            # ambiguous, or valid account. This prevents account enumeration.
            # Password hashes and reset tokens never leave ERP.
            user = self._active_credential_user(submitted_login)
            if user:
                user.action_reset_password()
            return {
                "authoritative": True,
                "accepted": True,
            }, 202, None

        return self._run("customer_password_reset_request", handler)

    @route(
        "/api/v1/customers/password/change",
        type="http",
        auth="bearer",
        methods=["POST"],
        csrf=False,
    )
    def customer_password_change(self):
        def handler(body, client):
            submitted_login = str(body.get("login") or body.get("email") or "").strip()
            current_password = str(body.get("current_password") or "")
            new_password = str(body.get("new_password") or "")
            if not submitted_login or not current_password or not new_password:
                raise ShopApiError(
                    "invalid_password_change",
                    "登录名、当前密码和新密码不能为空。",
                    400,
                )

            Users = request.env["res.users"]
            Users._shop_api_assert_plaintext_password(new_password)
            user = self._active_credential_user(submitted_login)
            if not user:
                raise ShopApiError("invalid_credentials", "用户名或密码错误。", 401)

            if user._mfa_url():
                raise ShopApiError(
                    "mfa_required",
                    "此账户启用了双重验证，不能通过商城修改密码。",
                    409,
                )

            try:
                user._shop_api_change_password(current_password, new_password)
            except AccessDenied:
                raise ShopApiError("invalid_credentials", "用户名或密码错误。", 401) from None
            return {
                "authoritative": True,
                "changed": True,
            }, 200, None

        return self._run("customer_password_change", handler)

    @route("/api/v1/customers/by-external-id/<string:external_id>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def customer_external(self, external_id):
        def handler(_body, client):
            reference = request.env["shop.api.external.reference"].sudo().search([
                ("client_id", "=", client.id), ("resource_type", "=", "customer"),
                ("external_id", "=", external_id),
            ], limit=1)
            if not reference or not reference.resolve():
                raise ShopApiError("not_found", "找不到客户。", 404)
            return reference.resolve()._shop_api_payload(), 200, None
        return self._run("customer_external", handler)

    @route("/api/v1/customers/<string:customer_uuid>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def customer_detail(self, customer_uuid):
        return self._run("customer_detail", lambda _body, client: (
            {
                **self._customer_record(customer_uuid, client)._shop_api_payload(),
                "authoritative": True,
            }, 200, None,
        ))

    @route("/api/v1/customers/<string:customer_uuid>/orders", type="http", auth="bearer", methods=["GET"], csrf=False)
    def customer_orders(self, customer_uuid):
        def handler(_body, client):
            customer = self._customer_record(customer_uuid, client).commercial_partner_id
            page, page_size = self._pagination()
            domain = [("partner_id.commercial_partner_id", "=", customer.id)]
            Order = request.env["sale.order"].sudo()
            total = Order.search_count(domain)
            orders = Order.search(domain, order="date_order desc, id desc", offset=(page - 1) * page_size, limit=page_size)
            language = self._site_language()
            return [order._shop_api_payload(language=language) for order in orders], 200, {
                "page": page, "page_size": page_size, "total": total,
            }
        return self._run("customer_orders", handler)

    @route("/api/v1/customers/<string:customer_uuid>/refund-requests", type="http", auth="bearer", methods=["GET"], csrf=False)
    def customer_refunds(self, customer_uuid):
        def handler(_body, client):
            customer = self._customer_record(customer_uuid, client).commercial_partner_id
            refunds = request.env["stock.subwarehouse.website.refund.request"].sudo().search([
                ("order_id.partner_id.commercial_partner_id", "=", customer.id),
            ], order="create_date desc, id desc")
            language = self._site_language()
            return [refund._shop_api_payload(language=language) for refund in refunds], 200, None
        return self._run("customer_refunds", handler)

    @staticmethod
    def _address_values(body):
        address_fields = ["name", "street", "street2", "city", "zip", "phone"]
        if "mobile" in request.env["res.partner"]._fields:
            address_fields.append("mobile")
        values = {
            key: body.get(key) or False
            for key in address_fields
        }
        values["type"] = body.get("type") if body.get("type") in ("delivery", "invoice", "contact") else "delivery"
        if body.get("country"):
            country = request.env["res.country"].sudo().search([("code", "=", body["country"].upper())], limit=1)
            if not country:
                raise ShopApiError("invalid_country", "国家或地区代码无效。", 400)
            values["country_id"] = country.id
        return values

    @route("/api/v1/customers/<string:customer_uuid>/addresses", type="http", auth="bearer", methods=["POST"], csrf=False)
    def address_create(self, customer_uuid):
        def handler(body, client):
            customer = self._customer_record(customer_uuid, client)
            address = request.env["res.partner"].sudo().create({
                **self._address_values(body), "parent_id": customer.commercial_partner_id.id,
            })
            return address._shop_api_address_payload(), 201, None
        return self._run("address_create", handler)

    def _address_action(self, endpoint, customer_uuid, address_uuid, delete=False):
        def handler(body, client):
            customer = self._customer_record(customer_uuid, client)
            address = request.env["res.partner"].sudo().search([
                ("shop_api_uuid", "=", address_uuid),
                ("id", "child_of", customer.commercial_partner_id.id),
                ("id", "!=", customer.commercial_partner_id.id),
            ], limit=1)
            if not address:
                raise ShopApiError("not_found", "找不到客户地址。", 404)
            if delete:
                address.active = False
            else:
                address.write(self._address_values(body))
            return address._shop_api_address_payload(), 200, None
        return self._run(endpoint, handler)

    @route("/api/v1/customers/<string:customer_uuid>/addresses/<string:address_uuid>", type="http", auth="bearer", methods=["PATCH"], csrf=False)
    def address_update(self, customer_uuid, address_uuid):
        return self._address_action("address_update", customer_uuid, address_uuid)

    @route("/api/v1/customers/<string:customer_uuid>/addresses/<string:address_uuid>", type="http", auth="bearer", methods=["DELETE"], csrf=False)
    def address_delete(self, customer_uuid, address_uuid):
        return self._address_action("address_delete", customer_uuid, address_uuid, delete=True)

    @route("/api/v1/orders", type="http", auth="bearer", methods=["POST"], csrf=False)
    def order_create(self):
        def handler(body, client):
            reservation = request.env["shop.api.reservation"].sudo().search([
                ("name", "=", body.get("reservation_id")),
                ("client_id", "=", client.id),
            ], limit=1)
            if not reservation:
                raise ShopApiError("reservation_required", "创建订单需要有效的库存预留。", 400)
            customer = self._record("res.partner", body.get("customer_id"))
            external_id = str(body.get("external_id") or "").strip()
            if not external_id:
                raise ShopApiError("external_id_required", "订单 external_id 不能为空。", 400)
            shipping_address = self._record("res.partner", body.get("shipping_address_id")) \
                if body.get("shipping_address_id") else customer
            if shipping_address.commercial_partner_id != customer.commercial_partner_id:
                raise ShopApiError("address_mismatch", "配送地址不属于订单客户。", 409)
            shipping_method = self._record("delivery.carrier", body.get("shipping_method_id")) \
                if body.get("shipping_method_id") else request.env["delivery.carrier"]
            if shipping_method and not shipping_method.active:
                raise ShopApiError("shipping_method_unavailable", "配送方式不可用。", 409)
            order = reservation.create_order(
                customer, external_id, language=body.get("language", "zh_CN"),
                shipping_address=shipping_address, shipping_method=shipping_method,
            )
            return order._shop_api_payload(), 201, None
        return self._run("order_create", handler)

    @route("/api/v1/orders", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_list(self):
        def handler(_body, client):
            page, page_size = self._pagination()
            domain = [("x_channel", "=", client.code)]
            Order = request.env["sale.order"].sudo()
            total = Order.search_count(domain)
            orders = Order.search(
                domain, order="date_order desc, id desc",
                offset=(page - 1) * page_size, limit=page_size,
            )
            return [order._shop_api_payload() for order in orders], 200, {
                "page": page, "page_size": page_size, "total": total,
            }
        return self._run("order_list", handler)

    def _order_record(self, order_uuid, client=None):
        order = self._record("sale.order", order_uuid)
        if client:
            reference = request.env["shop.api.external.reference"].sudo().search_count([
                ("client_id", "=", client.id), ("resource_type", "=", "order"),
                ("resource_id", "=", order.id),
            ])
            if not reference and order.x_channel != client.code:
                raise ShopApiError("not_found", "找不到订单。", 404)
        return order

    def _customer_record(self, customer_uuid, client):
        customer = self._record("res.partner", customer_uuid)
        owned = request.env["shop.api.external.reference"].sudo().search_count([
            ("client_id", "=", client.id),
            ("resource_type", "=", "customer"),
            ("resource_model", "=", "res.partner"),
            ("resource_id", "=", customer.commercial_partner_id.id),
        ])
        if not owned:
            raise ShopApiError("not_found", "找不到客户。", 404)
        return customer

    def _payment_record(self, payment_uuid, client):
        transaction = self._record("payment.transaction", payment_uuid)
        orders = transaction.sale_order_ids or transaction.source_transaction_id.sale_order_ids
        if not orders.filtered(lambda order: order.x_channel == client.code):
            raise ShopApiError("not_found", "找不到支付交易。", 404)
        return transaction

    def _shipment_record(self, shipment_uuid, client):
        picking = self._record("stock.picking", shipment_uuid)
        if not picking.sale_id or picking.sale_id.x_channel != client.code:
            raise ShopApiError("not_found", "找不到物流记录。", 404)
        return picking

    def _refund_record(self, refund_uuid, client):
        refund = self._record("stock.subwarehouse.website.refund.request", refund_uuid)
        if refund.order_id.x_channel != client.code:
            raise ShopApiError("not_found", "找不到退款申请。", 404)
        return refund

    @route("/api/v1/orders/<string:order_uuid>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_detail(self, order_uuid):
        return self._run("order_detail", lambda _body, client: (
            self._order_record(order_uuid, client)._shop_api_payload(
                language=self._site_language()
            ), 200, None,
        ))

    @route("/api/v1/orders/by-external-id/<string:external_id>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_external(self, external_id):
        def handler(_body, client):
            reference = request.env["shop.api.external.reference"].sudo().search([
                ("client_id", "=", client.id), ("resource_type", "=", "order"),
                ("external_id", "=", external_id),
            ], limit=1)
            if not reference or not reference.resolve():
                raise ShopApiError("not_found", "找不到订单。", 404)
            return reference.resolve()._shop_api_payload(), 200, None
        return self._run("order_external", handler)

    @route("/api/v1/orders/changes", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_changes(self):
        def handler(_body, client):
            since = request.httprequest.args.get("since")
            domain = [("x_channel", "=", client.code)]
            if since:
                domain.append(("write_date", ">", fields.Datetime.to_datetime(since)))
            orders = request.env["sale.order"].sudo().search(domain, order="write_date, id", limit=500)
            return [order._shop_api_payload() for order in orders], 200, {"count": len(orders)}
        return self._run("order_changes", handler)

    @route("/api/v1/orders/<string:order_uuid>", type="http", auth="bearer", methods=["PATCH"], csrf=False)
    def order_update(self, order_uuid):
        def handler(body, client):
            order = self._order_record(order_uuid, client)
            if order.state not in ("draft", "sent") or order.transaction_ids.filtered(lambda tx: tx.state == "done"):
                raise ShopApiError("order_not_editable", "只有未支付草稿订单可以修改。", 409)
            allowed = {key: body[key] for key in ("client_order_ref", "note") if key in body}
            if allowed:
                order.sudo().write(allowed)
            return order._shop_api_payload(), 200, None
        return self._run("order_update", handler)

    @route("/api/v1/orders/<string:order_uuid>/confirm", type="http", auth="bearer", methods=["POST"], csrf=False)
    def order_confirm(self, order_uuid):
        def handler(_body, client):
            order = self._order_record(order_uuid, client)
            if order.amount_total and not order.transaction_ids.filtered(lambda tx: tx.state == "done"):
                raise ShopApiError("payment_required", "订单尚未完成支付，不能确认。", 409)
            if order.state in ("draft", "sent"):
                order.action_confirm()
            return order._shop_api_payload(), 200, None
        return self._run("order_confirm", handler)

    @route("/api/v1/orders/<string:order_uuid>/cancel", type="http", auth="bearer", methods=["POST"], csrf=False)
    def order_cancel(self, order_uuid):
        def handler(_body, client):
            order = self._order_record(order_uuid, client)
            if order.transaction_ids.filtered(lambda tx: tx.state == "done"):
                raise ShopApiError("refund_required", "已支付订单必须通过退款流程取消。", 409)
            order.action_cancel()
            return order._shop_api_payload(), 200, None
        return self._run("order_cancel", handler)

    @route("/api/v1/orders/<string:order_uuid>/documents", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_documents(self, order_uuid):
        def handler(_body, client):
            order = self._order_record(order_uuid, client)
            invoices = order.invoice_ids.filtered(
                lambda move: move.state != "cancel" and move.move_type in ("out_invoice", "out_refund")
            )
            payment = order._get_website_payment_receipt()
            return {
                "receipt": ({
                    "available": True,
                    "number": payment.name,
                    "payment_id": str(payment.id),
                } if payment else None),
                "invoices": [move._shop_api_payload() for move in invoices],
            }, 200, None
        return self._run("order_documents", handler)

    def _binary_client(self, endpoint_code):
        endpoint = request.env["shop.api.endpoint"].sudo().search([
            ("code", "=", endpoint_code), ("active", "=", True),
        ], limit=1)
        client = request.env["shop.api.client"]._client_for_current_user()
        if not endpoint or not client or not client.allows_scope(endpoint.scope_id.code):
            raise ShopApiError("scope_denied", "API 客户端无权下载该单据。", 403)
        return client

    @route("/api/v1/orders/<string:order_uuid>/receipt.pdf", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_receipt_download(self, order_uuid):
        client = self._binary_client("order_receipt_download")
        order = self._order_record(order_uuid, client)
        payment = order._get_website_payment_receipt()
        if not payment:
            raise ShopApiError("not_found", "该订单没有可下载的付款收据。", 404)
        pdf, _report_type = request.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "stock_subwarehouse_hierarchy.action_report_website_payment_receipt",
            res_ids=payment.ids,
        )
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", str(len(pdf))),
            ("Content-Disposition", content_disposition(f"payment-receipt-{payment.name}.pdf")),
        ])

    @route("/api/v1/orders/<string:order_uuid>/invoices/<string:invoice_uuid>.pdf", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_invoice_download(self, order_uuid, invoice_uuid):
        client = self._binary_client("order_invoice_download")
        order = self._order_record(order_uuid, client)
        invoice = order.invoice_ids.filtered(
            lambda move: move.shop_api_uuid == invoice_uuid
            and move.state != "cancel"
            and move.move_type in ("out_invoice", "out_refund")
        )[:1]
        if not invoice:
            raise ShopApiError("not_found", "找不到该订单的发票。", 404)
        report = invoice.partner_id.invoice_template_pdf_report_id or request.env.ref(
            "account.account_invoices"
        )
        pdf, _report_type = request.env["ir.actions.report"].sudo()._render_qweb_pdf(
            report.report_name, res_ids=invoice.ids,
        )
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", str(len(pdf))),
            ("Content-Disposition", content_disposition(f"invoice-{invoice.name}.pdf")),
        ])

    @route("/api/v1/orders/<string:order_uuid>/payments", type="http", auth="bearer", methods=["POST"], csrf=False)
    def payment_create(self, order_uuid):
        def handler(body, client):
            order = self._order_record(order_uuid, client)
            try:
                request.env.cr.execute(
                    SQL(
                        "SELECT 1 FROM sale_order WHERE id = %s "
                        "FOR NO KEY UPDATE NOWAIT",
                        order.id,
                    )
                )
            except LockNotAvailable as error:
                raise ShopApiError(
                    "payment_already_processing",
                    "Payment is already being processed.",
                    409,
                ) from error
            if order.state == "cancel":
                raise ShopApiError("order_cancelled", "The order has been cancelled.", 409)

            # Keep the same readiness and stock gates as Odoo's native
            # /shop/payment/transaction route. The only difference is that the
            # caller is authenticated through the Shop API instead of an Odoo
            # browser session.
            order._check_cart_is_ready_to_be_paid()
            # The deadline was created by ERP when the reservation became an
            # order. Re-entering this endpoint must never refresh that deadline.
            order._assert_website_payment_reservation_active()
            if order.currency_id.compare_amounts(
                order.amount_paid, order.amount_total,
            ) == 0:
                raise ShopApiError(
                    "order_already_paid",
                    "The order has already been paid. Please refresh the page.",
                    409,
                )
            if body.get("provider") == "bank_card":
                raise ShopApiError(
                    "payment_provider_unavailable",
                    "银行卡支付尚未开放。",
                    409,
                )
            provider = request.env["payment.provider"].sudo().search([
                ("code", "=", body.get("provider")),
                ("state", "in", ("enabled", "test")),
            ], limit=1)
            if (
                not provider
                or provider.code == "custom"
                or self._is_cash_on_delivery_provider(provider)
                or order.currency_id not in provider._get_supported_currencies()
            ):
                raise ShopApiError("payment_provider_unavailable", "支付方式不可用。", 409)
            payment_method = provider.payment_method_ids[:1]
            if not payment_method:
                raise ShopApiError("payment_method_unavailable", "支付方式没有可用的支付方法。", 409)
            reference = request.env["payment.transaction"]._compute_reference(
                provider.code, prefix=order.name,
            )
            configuration = request.env["shop.api.reservation"]._configuration_for_client(client)
            return_url = str(body.get("return_url") or "").strip()
            if return_url:
                requested = urlparse(return_url)
                origin = f"{requested.scheme}://{requested.netloc}"
                if (
                    requested.scheme not in ("http", "https")
                    or origin not in configuration.payment_return_origins()
                ):
                    raise ShopApiError(
                        "invalid_return_url",
                        "支付返回地址必须属于已配置的商城地址。",
                        400,
                    )
            transaction = request.env["payment.transaction"].sudo().create({
                "provider_id": provider.id,
                "payment_method_id": payment_method.id,
                "reference": reference,
                "amount": order.amount_total,
                "currency_id": order.currency_id.id,
                "partner_id": order.partner_invoice_id.id,
                "operation": "online_redirect",
                "tokenize": False,
                "sale_order_ids": [Command.set(order.ids)],
                "landing_route": return_url or f"/shop/payment/status/{order.shop_api_uuid}",
            })
            transaction._log_sent_message()
            processing = transaction._get_processing_values()
            safe_processing = {
                key: value for key, value in processing.items()
                if key not in {"provider_id", "currency_id", "partner_id"}
            }
            return {
                "authoritative": True,
                "payment": transaction._shop_api_payload(),
                "processing": safe_processing,
            }, 201, None
        return self._run("payment_create", handler)

    @route("/api/v1/payments/<string:payment_uuid>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def payment_detail(self, payment_uuid):
        def handler(_body, client):
            transaction = self._payment_record(payment_uuid, client)
            refresh = getattr(transaction, "_lianlian_refresh_status", None)
            if transaction.provider_code == "lianlian" and refresh:
                # A checkout redirect never proves payment. Refresh only from
                # LianLian's signed query response, while keeping transient
                # provider outages fail-closed as a pending ERP transaction.
                try:
                    refresh()
                except ValidationError:
                    pass
            return transaction._shop_api_payload(), 200, None
        return self._run("payment_detail", handler)

    @route("/api/v1/payments/<string:payment_uuid>/simulate-success", type="http", auth="bearer", methods=["POST"], csrf=False)
    def payment_simulate_success(self, payment_uuid):
        def handler(_body, client):
            transaction = self._payment_record(payment_uuid, client)
            configuration = request.env["shop.api.reservation"]._configuration_for_client(client)
            if not configuration.allow_payment_simulators:
                raise ShopApiError("payment_simulator_disabled", "支付模拟器未启用。", 403)
            provider = transaction.provider_id.sudo()
            if transaction.state == "done":
                return transaction._shop_api_payload(), 200, None
            if transaction.state not in ("draft", "pending"):
                raise ShopApiError("payment_not_simulatable", "当前支付状态不能模拟成功。", 409)
            if transaction.provider_code == "alipay" and provider.alipay_simulation_mode:
                transaction._process("alipay", {
                    "reference": transaction.reference,
                    "out_trade_no": transaction.alipay_out_trade_no,
                    "trade_no": f"API-SIM-{transaction.id}",
                    "trade_status": "TRADE_SUCCESS",
                    "total_amount": str(transaction.amount),
                })
            elif transaction.provider_code == "wechatpay" and provider.wechatpay_simulation_mode:
                transaction._process("wechatpay", {
                    "transaction_id": f"API-SIM-{transaction.id}",
                    "out_trade_no": transaction.wechatpay_out_trade_no,
                    "trade_state": "SUCCESS",
                    "amount": {"total": int(round(transaction.amount * 100)), "currency": transaction.currency_id.name},
                })
            else:
                raise ShopApiError("payment_not_simulatable", "该支付方式未处于模拟模式。", 409)
            return transaction._shop_api_payload(), 200, None
        return self._run("payment_simulate_success", handler)

    @route("/api/v1/orders/<string:order_uuid>/payments", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_payments(self, order_uuid):
        return self._run("order_payments", lambda _body, client: ([
            tx._shop_api_payload() for tx in self._order_record(order_uuid, client).transaction_ids
        ], 200, None))

    @route("/api/v1/payments/<string:payment_uuid>/reconcile", type="http", auth="bearer", methods=["POST"], csrf=False)
    def payment_reconcile(self, payment_uuid):
        def handler(_body, client):
            transaction = self._payment_record(payment_uuid, client)
            refresh = getattr(transaction, "_lianlian_refresh_status", None)
            supported = bool(transaction.provider_code == "lianlian" and refresh)
            if supported:
                refresh(force=True)
            return transaction._shop_api_payload(), 200, {
                "provider_query_supported": supported,
            }
        return self._run("payment_reconcile", handler)

    @route("/api/v1/orders/<string:order_uuid>/shipments", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_shipments(self, order_uuid):
        return self._run("order_shipments", lambda _body, client: ([
            picking._shop_api_payload() for picking in self._order_record(order_uuid, client).picking_ids
        ], 200, None))

    @route("/api/v1/shipments/<string:shipment_uuid>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def shipment_detail(self, shipment_uuid):
        return self._run("shipment_detail", lambda _body, client: (
            self._shipment_record(shipment_uuid, client)._shop_api_payload(), 200, None,
        ))

    @route("/api/v1/orders/<string:order_uuid>/shipping-address-change", type="http", auth="bearer", methods=["POST"], csrf=False)
    def shipping_address_change(self, order_uuid):
        def handler(body, client):
            order = self._order_record(order_uuid, client)
            if order.picking_ids.filtered(lambda picking: picking.state not in ("draft", "cancel")):
                raise ShopApiError("shipment_already_started", "物流已经开始，不能修改配送地址。", 409)
            address = self._record("res.partner", body.get("address_id"))
            if address.commercial_partner_id != order.partner_id.commercial_partner_id:
                raise ShopApiError("address_mismatch", "配送地址不属于订单客户。", 409)
            order.partner_shipping_id = address
            return order._shop_api_payload(), 200, None
        return self._run("shipping_address_change", handler)

    @route("/api/v1/orders/<string:order_uuid>/refund-requests", type="http", auth="bearer", methods=["POST"], csrf=False)
    def refund_request_create(self, order_uuid):
        def handler(body, client):
            order = self._order_record(order_uuid, client)
            transaction = order.transaction_ids.filtered(
                lambda tx: tx.state == "done"
                and tx._supports_website_original_refund()
            ).sorted("id")[-1:]
            if not transaction:
                raise ShopApiError(
                    "refund_provider_not_supported",
                    "该订单没有支持原路退款的已完成支付交易。",
                    409,
                )
            lines = []
            for item in body.get("items") or []:
                product = self._record("product.product", item.get("product_id"))
                sale_line = order.order_line.filtered(lambda line: line.product_id == product)[:1]
                quantity = float(item.get("quantity") or 0)
                if not sale_line or quantity <= 0 or quantity > sale_line.product_uom_qty:
                    raise ShopApiError("invalid_refund_quantity", "退款商品或数量无效。", 409)
                lines.append(Command.create({"sale_line_id": sale_line.id, "quantity": quantity}))
            if not lines:
                raise ShopApiError("refund_items_required", "退款申请至少需要一个商品。", 400)
            refund_request = request.env["stock.subwarehouse.website.refund.request"].sudo().create({
                "order_id": order.id,
                "source_transaction_id": transaction.id,
                "line_ids": lines,
            })
            return refund_request._shop_api_payload(), 201, None
        return self._run("refund_request_create", handler)

    @route("/api/v1/refund-requests/<string:refund_uuid>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def refund_request_detail(self, refund_uuid):
        return self._run("refund_request_detail", lambda _body, client: (
            self._refund_record(refund_uuid, client)._shop_api_payload(
                language=self._site_language()
            ), 200, None,
        ))

    @route("/api/v1/orders/<string:order_uuid>/refund-requests", type="http", auth="bearer", methods=["GET"], csrf=False)
    def order_refund_requests(self, order_uuid):
        return self._run("order_refund_requests", lambda _body, client: ([
            item._shop_api_payload(language=self._site_language())
            for item in self._order_record(order_uuid, client).x_website_refund_request_ids
        ], 200, None))

    @route("/api/v1/refund-requests/<string:refund_uuid>/cancel", type="http", auth="bearer", methods=["POST"], csrf=False)
    def refund_request_cancel(self, refund_uuid):
        def handler(_body, client):
            refund = self._refund_record(refund_uuid, client)
            if refund.state != "requested":
                raise ShopApiError("refund_not_cancellable", "只有待审核退款申请可以取消。", 409)
            refund.review_state = "rejected"
            return refund._shop_api_payload(), 200, None
        return self._run("refund_request_cancel", handler)

    @route("/api/v1/refund-requests/<string:refund_uuid>/return-instructions", type="http", auth="bearer", methods=["GET"], csrf=False)
    def return_instructions(self, refund_uuid):
        def handler(_body, client):
            refund = self._refund_record(refund_uuid, client)
            return {
                "return_required": refund.return_required,
                "state": refund.state,
                "destination": refund.return_location_id.complete_name if refund.return_location_id else "",
                "return_reference": refund.return_picking_ids[:1].name or "",
            }, 200, None
        return self._run("return_instructions", handler)

    @route("/api/v1/refund-requests/<string:refund_uuid>/return-shipped", type="http", auth="bearer", methods=["POST"], csrf=False)
    def return_shipped(self, refund_uuid):
        def handler(body, client):
            refund = self._refund_record(refund_uuid, client)
            if (
                not refund.return_required
                or refund.state != "returning"
                or refund.x_return_delivery_state != "awaiting_delivery"
            ):
                raise ShopApiError("return_not_expected", "该退款申请当前不等待客户退货。", 409)
            refund.with_context(shop_api_skip_event=True).write({
                "shop_api_return_carrier": body.get("carrier") or False,
                "shop_api_return_tracking": body.get("tracking_number") or False,
                "shop_api_return_shipped_at": fields.Datetime.now(),
            })
            # This transition records that the goods are in transit only. The
            # incoming picking remains untouched, so ERP stock is not restored
            # until an administrator confirms physical receipt.
            refund.action_start_customer_return_delivery()
            return refund._shop_api_payload(), 200, None
        return self._run("return_shipped", handler)

    @route("/api/v1/refunds/<string:refund_uuid>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def refund_detail(self, refund_uuid):
        def handler(_body, client):
            transaction = self._payment_record(refund_uuid, client)
            if transaction.operation != "refund":
                raise ShopApiError("not_found", "找不到退款交易。", 404)
            return transaction._shop_api_payload(), 200, None
        return self._run("refund_detail", handler)

    @route("/api/v1/credit-notes/<string:credit_uuid>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def credit_note_detail(self, credit_uuid):
        def handler(_body, client):
            move = self._record("account.move", credit_uuid, [("move_type", "=", "out_refund")])
            owned = request.env["stock.subwarehouse.website.refund.request"].sudo().search_count([
                ("credit_note_id", "=", move.id),
                ("order_id.x_channel", "=", client.code),
            ])
            if not owned:
                raise ShopApiError("not_found", "找不到贷项通知单。", 404)
            return move._shop_api_payload(), 200, None
        return self._run("credit_note_detail", handler)

    @route("/api/v1/events", type="http", auth="bearer", methods=["GET"], csrf=False)
    def events(self):
        def handler(_body, client):
            events = request.env["shop.api.event"].sudo().search([
                "|", ("client_id", "=", False), ("client_id", "=", client.id),
            ], order="create_date desc", limit=200)
            return [{
                "event_id": event.event_id,
                "event_type": event.event_type,
                "resource_id": event.resource_uuid,
                "resource_version": event.resource_version,
                "occurred_at": fields.Datetime.to_string(event.occurred_at),
                "state": event.state,
                "data": event.payload,
            } for event in events], 200, {"count": len(events)}
        return self._run("events", handler)

    @route("/api/v1/events/<string:event_id>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def event_detail(self, event_id):
        def handler(_body, client):
            event = request.env["shop.api.event"].sudo().search([
                ("event_id", "=", event_id),
                "|", ("client_id", "=", False), ("client_id", "=", client.id),
            ], limit=1)
            if not event:
                raise ShopApiError("not_found", "找不到事件。", 404)
            return {
                "event_id": event.event_id, "event_type": event.event_type,
                "state": event.state, "attempt_count": event.attempt_count,
                "resource_id": event.resource_uuid, "data": event.payload,
            }, 200, None
        return self._run("event_detail", handler)

    @route("/api/v1/events/<string:event_id>/retry", type="http", auth="bearer", methods=["POST"], csrf=False)
    def event_retry(self, event_id):
        def handler(_body, client):
            event = request.env["shop.api.event"].sudo().search([
                ("event_id", "=", event_id),
                "|", ("client_id", "=", False), ("client_id", "=", client.id),
            ], limit=1)
            if not event:
                raise ShopApiError("not_found", "找不到事件。", 404)
            event.action_retry()
            return {"event_id": event.event_id, "state": event.state}, 200, None
        return self._run("event_retry", handler)

    @route("/api/v1/sync/checkpoints/<string:resource_type>", type="http", auth="bearer", methods=["GET"], csrf=False)
    def sync_checkpoint(self, resource_type):
        def handler(_body, client):
            checkpoint = request.env["shop.api.sync.checkpoint"].sudo().search([
                ("client_id", "=", client.id), ("resource_type", "=", resource_type),
            ], limit=1)
            return ({
                "resource": resource_type,
                "cursor": checkpoint.cursor or "",
                "last_event_id": checkpoint.last_event_id or "",
                "synchronized_at": fields.Datetime.to_string(checkpoint.synchronized_at),
                "state": checkpoint.state,
            } if checkpoint else {
                "resource": resource_type, "cursor": "", "last_event_id": "", "state": "idle",
            }), 200, None
        return self._run("sync_checkpoint", handler)

    @route("/api/v1/sync/changes", type="http", auth="bearer", methods=["GET"], csrf=False)
    def sync_changes(self):
        def handler(_body, client):
            since = request.httprequest.args.get("since")
            domain = ["|", ("client_id", "=", False), ("client_id", "=", client.id)]
            if since:
                domain.append(("occurred_at", ">", fields.Datetime.to_datetime(since)))
            events = request.env["shop.api.event"].sudo().search(
                domain, order="occurred_at, id", limit=500,
            )
            return [{
                "event_id": event.event_id,
                "event_type": event.event_type,
                "resource_id": event.resource_uuid,
                "resource_version": event.resource_version,
                "occurred_at": fields.Datetime.to_string(event.occurred_at),
                "data": event.payload,
            } for event in events], 200, {"count": len(events)}
        return self._run("sync_changes", handler)

    @route("/api/v1/sync/reconcile", type="http", auth="bearer", methods=["POST"], csrf=False)
    def sync_reconcile(self):
        def handler(body, client):
            resource_type = body.get("resource")
            if resource_type not in ("products", "inventory", "orders", "payments", "refunds"):
                raise ShopApiError("invalid_resource", "不支持此对账资源。", 400)
            reconciliation = request.env["shop.api.reconciliation"].sudo().create({
                "client_id": client.id, "resource_type": resource_type,
                "state": "done", "completed_at": fields.Datetime.now(),
                "details": {"mode": "checkpoint_requested", "external_items": body.get("items") or []},
            })
            return {"id": reconciliation.name, "state": reconciliation.state}, 202, None
        return self._run("sync_reconcile", handler)
