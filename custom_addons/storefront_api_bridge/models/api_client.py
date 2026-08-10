import json
import os
import threading
import time
import uuid
from urllib import error, parse, request as urlrequest

from odoo import _, api, models
from odoo.exceptions import UserError


_INVENTORY_SNAPSHOT_CACHE = {}
_INVENTORY_SNAPSHOT_LOCK = threading.RLock()


class StorefrontApiError(UserError):
    def __init__(self, message, code="erp_api_error", status=502, details=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}


class StorefrontErpClient(models.AbstractModel):
    _name = "storefront.erp.client"
    _description = "Storefront server-side ERP API client"

    @api.model
    def _settings(self):
        base_url = os.environ.get("STOREFRONT_ERP_BASE_URL", "http://127.0.0.1:8069").rstrip("/")
        api_key = os.environ.get("STOREFRONT_ERP_API_KEY", "").strip()
        timeout = int(os.environ.get("STOREFRONT_ERP_TIMEOUT_SECONDS", "10"))
        if not api_key:
            raise StorefrontApiError(
                _("ERP API credential is not configured."),
                code="erp_api_not_configured",
                status=503,
            )
        return base_url, api_key, max(1, min(timeout, 60))

    @api.model
    def public_url(self):
        return os.environ.get("STOREFRONT_PUBLIC_URL", "http://127.0.0.1:8070").rstrip("/")

    @api.model
    def clear_inventory_snapshot_cache(self):
        with _INVENTORY_SNAPSHOT_LOCK:
            _INVENTORY_SNAPSHOT_CACHE.clear()

    @api.model
    def inventory_snapshot(self):
        base_url, _api_key, _timeout = self._settings()
        cache_seconds = max(
            1, min(int(os.environ.get("STOREFRONT_INVENTORY_CACHE_SECONDS", "30")), 300)
        )
        stale_seconds = max(
            cache_seconds,
            min(int(os.environ.get("STOREFRONT_INVENTORY_STALE_SECONDS", "300")), 3600),
        )
        now = time.monotonic()
        with _INVENTORY_SNAPSHOT_LOCK:
            cached = _INVENTORY_SNAPSHOT_CACHE.get(base_url)
            if cached and now - cached["fetched_at"] < cache_seconds:
                return cached["items"]
            persisted = self.env["storefront.cache.entry"].inventory_map()
            if persisted:
                _INVENTORY_SNAPSHOT_CACHE[base_url] = {
                    "fetched_at": now,
                    "items": persisted,
                }
                return persisted
            try:
                snapshot = self.get("/api/v1/inventory/snapshot") or {}
            except StorefrontApiError:
                if cached and now - cached["fetched_at"] < stale_seconds:
                    return cached["items"]
                raise

            items = self.env["storefront.cache.entry"].replace_inventory_snapshot(snapshot)
            _INVENTORY_SNAPSHOT_CACHE[base_url] = {
                "fetched_at": now,
                "items": items,
            }
            return items

    @api.model
    def refresh_inventory_snapshot(self):
        base_url, _api_key, _timeout = self._settings()
        snapshot = self.get("/api/v1/inventory/snapshot") or {}
        items = self.env["storefront.cache.entry"].replace_inventory_snapshot(snapshot)
        with _INVENTORY_SNAPSHOT_LOCK:
            _INVENTORY_SNAPSHOT_CACHE[base_url] = {
                "fetched_at": time.monotonic(),
                "items": items,
            }
        return items

    @api.model
    def call(
        self, method, path, payload=None, params=None, idempotency_key=None,
        timeout_seconds=None,
    ):
        base_url, api_key, default_timeout = self._settings()
        timeout = default_timeout if timeout_seconds is None else max(
            1, min(int(timeout_seconds), 60)
        )
        query = f"?{parse.urlencode(params)}" if params else ""
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-Request-Id": str(uuid.uuid4()),
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)[:255]
        api_request = urlrequest.Request(
            f"{base_url}{path}{query}", data=body, headers=headers, method=method.upper()
        )
        try:
            with urlrequest.urlopen(api_request, timeout=timeout) as response:
                raw = response.read()
        except error.HTTPError as exc:
            raw = exc.read()
            try:
                failure = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                failure = {}
            api_error = failure.get("error") or {}
            raise StorefrontApiError(
                api_error.get("message") or _("ERP rejected the request."),
                code=api_error.get("code") or "erp_http_error",
                status=exc.code,
                details=api_error.get("details") or {},
            ) from None
        except (error.URLError, TimeoutError, OSError) as exc:
            raise StorefrontApiError(
                _("ERP is temporarily unavailable. Please try again."),
                code="erp_unavailable",
                status=503,
            ) from exc
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorefrontApiError(
                _("ERP returned an invalid response."), code="invalid_erp_response", status=502
            ) from exc
        if document.get("error"):
            api_error = document["error"]
            raise StorefrontApiError(
                api_error.get("message") or _("ERP request failed."),
                code=api_error.get("code") or "erp_api_error",
                details=api_error.get("details") or {},
            )
        return document.get("data"), document.get("meta") or {}

    @api.model
    def get(self, path, params=None):
        return self.call("GET", path, params=params)[0]

    @api.model
    def post(self, path, payload, idempotency_key=None, timeout_seconds=None):
        return self.call(
            "POST", path, payload=payload, idempotency_key=idempotency_key,
            timeout_seconds=timeout_seconds,
        )[0]

    @api.model
    def payment_timeout_seconds(self):
        return max(
            1,
            min(int(os.environ.get("STOREFRONT_ERP_PAYMENT_TIMEOUT_SECONDS", "30")), 60),
        )

    @api.model
    def get_binary(self, path):
        base_url, api_key, timeout = self._settings()
        api_request = urlrequest.Request(
            f"{base_url}{path}",
            headers={
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {api_key}",
                "X-Request-Id": str(uuid.uuid4()),
            },
            method="GET",
        )
        try:
            with urlrequest.urlopen(api_request, timeout=timeout) as response:
                return response.read()
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            raise StorefrontApiError(
                _("ERP media is temporarily unavailable."),
                code="erp_media_unavailable",
                status=getattr(exc, "code", 503),
            ) from exc
