import hashlib
import hmac
import json
import os
import time

from odoo.http import Controller, request, route


class StorefrontWebhookController(Controller):
    @staticmethod
    def _valid_signature(raw_body, timestamp, supplied_signature, secret, now=None):
        try:
            timestamp_int = int(timestamp)
        except (TypeError, ValueError):
            return False
        if abs((int(now) if now is not None else int(time.time())) - timestamp_int) > 300:
            return False
        supplied = str(supplied_signature or "")
        if supplied.startswith("sha256="):
            supplied = supplied.split("=", 1)[1]
        expected = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp_int}.".encode("utf-8") + raw_body,
            hashlib.sha256,
        ).hexdigest()
        return bool(secret and hmac.compare_digest(supplied, expected))

    @route(
        "/webhooks/odoo", type="http", auth="public", methods=["POST"],
        csrf=False, save_session=False,
    )
    def receive_odoo_event(self):
        raw_body = request.httprequest.get_data(cache=True)
        timestamp = request.httprequest.headers.get("X-Shop-Timestamp")
        signature = request.httprequest.headers.get("X-Shop-Signature")
        secret = os.environ.get("STOREFRONT_WEBHOOK_SECRET", "").strip()
        if not self._valid_signature(raw_body, timestamp, signature, secret):
            return request.make_json_response({"error": "invalid_signature"}, status=401)
        try:
            document = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return request.make_json_response({"error": "invalid_json"}, status=400)
        if not isinstance(document, dict):
            return request.make_json_response({"error": "invalid_payload"}, status=400)
        event_id = str(document.get("event_id") or "").strip()
        event_type = str(document.get("event_type") or "").strip()
        header_event_id = str(request.httprequest.headers.get("X-Shop-Event-Id") or "").strip()
        if not event_id or not event_type or (header_event_id and header_event_id != event_id):
            return request.make_json_response({"error": "invalid_event"}, status=400)
        event, created = request.env["storefront.webhook.event"].sudo().enqueue_document(document)
        return request.make_json_response({
            "accepted": True,
            "duplicate": not created,
            "event_id": event.event_id,
            "state": event.state,
        }, status=202 if created else 200)
