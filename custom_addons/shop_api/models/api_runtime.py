import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


SENSITIVE_KEYS = {
    "authorization", "password", "passwd", "secret", "token", "api_key",
    "private_key", "card_number", "cvv", "signature", "current_password",
    "new_password", "confirm_password",
}


def redact_payload(value):
    if isinstance(value, dict):
        return {
            key: "***" if (
                key.lower() in SENSITIVE_KEYS
                or key.lower().endswith("_password")
            ) else redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


class ShopApiRequestLog(models.Model):
    _name = "shop.api.request.log"
    _description = "Shop API Request Log"
    _order = "create_date desc, id desc"

    request_id = fields.Char(string="请求编号", required=True, index=True, default=lambda self: str(uuid.uuid4()))
    client_id = fields.Many2one("shop.api.client", string="客户端", ondelete="set null", index=True)
    endpoint_id = fields.Many2one("shop.api.endpoint", string="接口", ondelete="set null", index=True)
    method = fields.Char(string="HTTP 方法")
    path = fields.Char(string="路径", index=True)
    source_ip = fields.Char(string="来源 IP")
    request_body = fields.Json(string="请求正文")
    response_body = fields.Json(string="响应正文")
    response_status = fields.Integer(string="HTTP 状态")
    duration_ms = fields.Integer(string="耗时毫秒")
    state = fields.Selection(
        [("processing", "处理中"), ("success", "成功"), ("error", "失败")],
        default="processing",
        required=True,
        index=True,
    )
    error_code = fields.Char(string="错误代码", index=True)
    error_message = fields.Text(string="错误说明")
    idempotency_key = fields.Char(string="幂等键", index=True)

    _unique_request_id = models.Constraint("UNIQUE(request_id)", "API 请求编号必须唯一。")

    @api.model
    def start_request(self, client, endpoint, request_id, method, path, source_ip, body, key=False):
        return self.sudo().create({
            "request_id": request_id,
            "client_id": client.id if client else False,
            "endpoint_id": endpoint.id if endpoint else False,
            "method": method,
            "path": path,
            "source_ip": source_ip,
            "request_body": (
                redact_payload(body)
                if body is not None and endpoint.log_request_body
                else False
            ),
            "idempotency_key": key,
        })

    def finish_request(self, status, body, started_at, error_code=False, error_message=False):
        self.ensure_one()
        self.sudo().write({
            "response_status": status,
            "response_body": (
                redact_payload(body)
                if body is not None and self.endpoint_id.log_response_body
                else False
            ),
            "duration_ms": max(int((time.monotonic() - started_at) * 1000), 0),
            "state": "error" if status >= 400 else "success",
            "error_code": error_code,
            "error_message": error_message,
        })


class ShopApiIdempotency(models.Model):
    _name = "shop.api.idempotency"
    _description = "Shop API Idempotency Record"
    _order = "create_date desc"

    client_id = fields.Many2one("shop.api.client", required=True, ondelete="cascade", index=True)
    key = fields.Char(string="幂等键", required=True, index=True)
    method = fields.Char(required=True)
    path = fields.Char(required=True)
    request_hash = fields.Char(required=True)
    state = fields.Selection(
        [("processing", "处理中"), ("completed", "已完成"), ("failed", "失败")],
        default="processing",
        required=True,
        index=True,
    )
    response_status = fields.Integer()
    response_body = fields.Json()
    expires_at = fields.Datetime(required=True, index=True)

    _unique_client_key = models.Constraint(
        "UNIQUE(client_id, key)",
        "同一 API 客户端不能重复保存相同幂等键。",
    )

    @api.model
    def canonical_hash(self, method, path, body):
        canonical = json.dumps(
            {"method": method.upper(), "path": path, "body": body or {}},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @api.model
    def begin(self, client, key, method, path, body):
        request_hash = self.canonical_hash(method, path, body)
        existing = self.sudo().search([
            ("client_id", "=", client.id),
            ("key", "=", key),
            ("expires_at", ">", fields.Datetime.now()),
        ], limit=1)
        if existing:
            if existing.request_hash != request_hash:
                raise ValidationError(_("相同幂等键已用于不同的请求内容。"))
            return existing, existing.state == "completed"
        record = self.sudo().create({
            "client_id": client.id,
            "key": key,
            "method": method,
            "path": path,
            "request_hash": request_hash,
            "expires_at": fields.Datetime.now() + timedelta(hours=24),
        })
        return record, False

    def complete(self, status, body):
        self.sudo().write({
            "state": "completed" if status < 500 else "failed",
            "response_status": status,
            "response_body": body,
        })


class ShopApiEvent(models.Model):
    _name = "shop.api.event"
    _description = "Shop API Outbox Event"
    _order = "create_date, id"

    event_id = fields.Char(required=True, index=True, default=lambda self: str(uuid.uuid4()))
    event_type_id = fields.Many2one("shop.api.event.type", required=True, ondelete="restrict", index=True)
    event_type = fields.Char(related="event_type_id.code", store=True, index=True)
    client_id = fields.Many2one("shop.api.client", ondelete="set null", index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    resource_model = fields.Char(index=True)
    resource_id = fields.Integer(index=True)
    resource_uuid = fields.Char(index=True)
    resource_version = fields.Char()
    payload = fields.Json(required=True)
    occurred_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    state = fields.Selection(
        [("pending", "待投递"), ("delivering", "投递中"), ("delivered", "已投递"),
         ("failed", "等待重试"), ("dead", "已停止")],
        default="pending",
        required=True,
        index=True,
    )
    attempt_count = fields.Integer(default=0)
    next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True)
    last_error = fields.Text()
    delivered_at = fields.Datetime()

    _unique_event_id = models.Constraint("UNIQUE(event_id)", "事件编号必须唯一。")

    @api.model
    def enqueue(self, event_type, record=None, payload=None, client=None):
        event_type_record = self.env["shop.api.event.type"].sudo().search([
            ("code", "=", event_type),
        ], limit=1)
        if not event_type_record:
            event_type_record = self.env["shop.api.event.type"].sudo().create({
                "code": event_type, "name": event_type,
            })
        resource_uuid = False
        resource_version = False
        if record:
            record.ensure_one()
            if "shop_api_uuid" in record._fields:
                record._shop_api_ensure_uuid()
                resource_uuid = record.shop_api_uuid
            resource_version = fields.Datetime.to_string(record.write_date or fields.Datetime.now())
        return self.sudo().create({
            "event_type_id": event_type_record.id,
            "client_id": client.id if client else False,
            "company_id": (
                record.company_id.id if record and "company_id" in record._fields and record.company_id
                else self.env.company.id
            ),
            "resource_model": record._name if record else False,
            "resource_id": record.id if record else False,
            "resource_uuid": resource_uuid,
            "resource_version": resource_version,
            "payload": payload or {},
        })

    def action_retry(self):
        self.filtered(lambda event: event.state in ("failed", "dead")).write({
            "state": "pending", "next_attempt_at": fields.Datetime.now(), "last_error": False,
        })

    @api.model
    def _cron_dispatch_events(self, limit=100):
        events = self.sudo().search([
            ("state", "in", ("pending", "failed")),
            ("next_attempt_at", "<=", fields.Datetime.now()),
        ], limit=limit, order="create_date, id")
        for event in events:
            event._dispatch()

    def _dispatch(self):
        self.ensure_one()
        subscriptions = self.env["shop.api.webhook"].sudo().search([
            ("active", "=", True),
            ("event_type_ids", "in", self.event_type_id.id),
        ])
        if self.client_id:
            subscriptions = subscriptions.filtered(lambda item: item.client_id == self.client_id)
        if not subscriptions:
            self.write({"state": "delivered", "delivered_at": fields.Datetime.now()})
            return

        self.state = "delivering"
        all_delivered = True
        for subscription in subscriptions:
            delivery = self.env["shop.api.webhook.delivery"].sudo().search([
                ("event_id", "=", self.id),
                ("webhook_id", "=", subscription.id),
            ], limit=1) or self.env["shop.api.webhook.delivery"].sudo().create({
                "event_id": self.id, "webhook_id": subscription.id,
            })
            if not delivery._deliver():
                all_delivered = False

        self.attempt_count += 1
        if all_delivered:
            self.write({
                "state": "delivered", "delivered_at": fields.Datetime.now(), "last_error": False,
            })
            return
        configuration = self.env["shop.api.configuration"].sudo().search([
            ("company_id", "=", self.company_id.id), ("active", "=", True),
        ], limit=1) or self.env["shop.api.configuration"].sudo()._ensure_default_configuration()
        if self.attempt_count >= configuration.webhook_retry_count:
            self.state = "dead"
        else:
            delay = configuration.webhook_retry_backoff_seconds * (2 ** max(self.attempt_count - 1, 0))
            self.write({
                "state": "failed",
                "next_attempt_at": fields.Datetime.now() + timedelta(seconds=min(delay, 86400)),
            })


class ShopApiWebhookDelivery(models.Model):
    _name = "shop.api.webhook.delivery"
    _description = "Shop API Webhook Delivery"
    _order = "create_date desc"

    event_id = fields.Many2one("shop.api.event", required=True, ondelete="cascade", index=True)
    webhook_id = fields.Many2one("shop.api.webhook", required=True, ondelete="cascade", index=True)
    state = fields.Selection(
        [("pending", "待投递"), ("success", "成功"), ("failed", "失败")],
        default="pending", required=True, index=True,
    )
    attempt_count = fields.Integer(default=0)
    last_attempt_at = fields.Datetime()
    response_status = fields.Integer()
    response_body = fields.Text()
    duration_ms = fields.Integer()
    last_error = fields.Text()

    _unique_event_webhook = models.Constraint(
        "UNIQUE(event_id, webhook_id)",
        "同一事件对同一 Webhook 只能有一条投递记录。",
    )

    def _deliver(self):
        self.ensure_one()
        event = self.event_id
        webhook = self.webhook_id
        body = json.dumps({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": fields.Datetime.to_string(event.occurred_at),
            "resource_id": event.resource_uuid,
            "resource_version": event.resource_version,
            "data": event.payload,
        }, ensure_ascii=False, separators=(",", ":"))
        timestamp = str(int(time.time()))
        signature = hmac.new(
            webhook.secret.encode("utf-8"),
            f"{timestamp}.{body}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        started = time.monotonic()
        values = {"attempt_count": self.attempt_count + 1, "last_attempt_at": fields.Datetime.now()}
        try:
            response = requests.post(
                webhook.url,
                data=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Shop-Event-Id": event.event_id,
                    "X-Shop-Timestamp": timestamp,
                    "X-Shop-Signature": f"sha256={signature}",
                },
                timeout=webhook.timeout_seconds,
            )
            values.update({
                "response_status": response.status_code,
                "response_body": response.text[:4000],
                "duration_ms": int((time.monotonic() - started) * 1000),
                "state": "success" if 200 <= response.status_code < 300 else "failed",
                "last_error": False if 200 <= response.status_code < 300 else response.text[:1000],
            })
        except requests.RequestException as error:
            values.update({
                "duration_ms": int((time.monotonic() - started) * 1000),
                "state": "failed", "last_error": str(error)[:1000],
            })
        self.write(values)
        if self.state == "success":
            webhook.write({"last_success_at": fields.Datetime.now(), "last_error": False})
            return True
        webhook.write({
            "last_failure_at": fields.Datetime.now(), "last_error": self.last_error,
        })
        event.last_error = self.last_error
        return False


class ShopApiSyncCheckpoint(models.Model):
    _name = "shop.api.sync.checkpoint"
    _description = "Shop API Synchronization Checkpoint"
    _order = "resource_type, client_id"

    client_id = fields.Many2one("shop.api.client", required=True, ondelete="cascade", index=True)
    resource_type = fields.Char(string="资源类型", required=True, index=True)
    cursor = fields.Char(string="游标")
    last_event_id = fields.Char(string="最后事件编号")
    synchronized_at = fields.Datetime(string="同步时间")
    state = fields.Selection(
        [("idle", "正常"), ("running", "同步中"), ("error", "异常")],
        default="idle", required=True,
    )
    last_error = fields.Text()

    _unique_client_resource = models.Constraint(
        "UNIQUE(client_id, resource_type)",
        "每个客户端与资源只能有一个同步检查点。",
    )


class ShopApiReconciliation(models.Model):
    _name = "shop.api.reconciliation"
    _description = "Shop API Reconciliation"
    _order = "create_date desc"

    name = fields.Char(default=lambda self: str(uuid.uuid4()), required=True)
    client_id = fields.Many2one("shop.api.client", required=True, ondelete="cascade", index=True)
    resource_type = fields.Char(required=True, index=True)
    state = fields.Selection(
        [("pending", "待执行"), ("running", "执行中"), ("done", "完成"), ("error", "失败")],
        default="pending", required=True, index=True,
    )
    requested_at = fields.Datetime(default=fields.Datetime.now)
    completed_at = fields.Datetime()
    checked_count = fields.Integer(default=0)
    mismatch_count = fields.Integer(default=0)
    details = fields.Json()
    last_error = fields.Text()


class ShopApiExternalReference(models.Model):
    _name = "shop.api.external.reference"
    _description = "Shop API External Reference"
    _order = "resource_type, external_id"

    client_id = fields.Many2one("shop.api.client", required=True, ondelete="cascade", index=True)
    resource_type = fields.Char(required=True, index=True)
    external_id = fields.Char(required=True, index=True)
    resource_model = fields.Char(required=True, index=True)
    resource_id = fields.Integer(required=True, index=True)
    resource_uuid = fields.Char(required=True, index=True)

    _unique_external_reference = models.Constraint(
        "UNIQUE(client_id, resource_type, external_id)",
        "同一客户端的资源外部编号必须唯一。",
    )

    @api.model
    def set_reference(self, client, resource_type, external_id, record):
        record.ensure_one()
        record._shop_api_ensure_uuid()
        existing = self.sudo().search([
            ("client_id", "=", client.id),
            ("resource_type", "=", resource_type),
            ("external_id", "=", external_id),
        ], limit=1)
        values = {
            "resource_model": record._name,
            "resource_id": record.id,
            "resource_uuid": record.shop_api_uuid,
        }
        if existing:
            existing.write(values)
            return existing
        return self.sudo().create({
            "client_id": client.id,
            "resource_type": resource_type,
            "external_id": external_id,
            **values,
        })

    def resolve(self):
        self.ensure_one()
        return self.env[self.resource_model].sudo().browse(self.resource_id).exists()
