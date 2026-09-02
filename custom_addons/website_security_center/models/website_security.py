import hashlib
import logging
import os
from datetime import timedelta
from urllib.parse import urlparse

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessDenied, AccessError, ValidationError


ACTIVITY_PREFIX = "网站安全事件："
OPEN_STATES = ("open", "acknowledged")
_logger = logging.getLogger(__name__)


class WebsiteSecurityIncident(models.Model):
    _name = "website.security.incident"
    _description = "Website Security Incident"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "occurred_at asc, id asc"

    name = fields.Char(string="事件编号", required=True, readonly=True, copy=False,
                       default=lambda self: _("新事件"))
    fingerprint = fields.Char(required=True, index=True, readonly=True, copy=False)
    company_id = fields.Many2one(
        "res.company", string="公司", required=True, index=True,
        default=lambda self: self.env.company, ondelete="cascade",
    )
    category = fields.Selection([
        ("authentication", "身份验证异常"),
        ("rate_limit", "访问频率异常"),
        ("idempotency", "幂等冲突"),
        ("payment", "支付接口异常"),
        ("server_error", "服务端异常"),
        ("slow_request", "接口响应过慢"),
        ("webhook", "Webhook 投递异常"),
        ("configuration", "安全配置异常"),
    ], string="事件类型", required=True, index=True, tracking=True)
    severity = fields.Selection([
        ("low", "低"), ("medium", "中"), ("high", "高"), ("critical", "严重"),
    ], string="严重程度", required=True, default="medium", index=True, tracking=True)
    state = fields.Selection([
        ("open", "待处理"),
        ("acknowledged", "处理中"),
        ("resolved", "已解决"),
        ("ignored", "已忽略"),
    ], string="处理状态", required=True, default="open", index=True, tracking=True)
    summary = fields.Char(string="事件摘要", required=True, tracking=True)
    safe_details = fields.Text(
        string="安全详情", readonly=True,
        help="只保存经过裁剪的状态、错误代码和关联编号，不保存密码、密钥、支付卡数据或请求正文。",
    )
    occurred_at = fields.Datetime(string="首次发生", required=True, default=fields.Datetime.now, index=True)
    last_seen_at = fields.Datetime(string="最近发生", required=True, default=fields.Datetime.now, index=True)
    occurrence_count = fields.Integer(string="发生次数", required=True, default=1, readonly=True)
    request_log_id = fields.Many2one("shop.api.request.log", string="关联请求", ondelete="set null")
    webhook_delivery_id = fields.Many2one(
        "shop.api.webhook.delivery", string="关联 Webhook 投递", ondelete="set null"
    )
    client_id = fields.Many2one("shop.api.client", string="API 客户端", ondelete="set null", index=True)
    endpoint_id = fields.Many2one("shop.api.endpoint", string="API 接口", ondelete="set null")
    request_id = fields.Char(string="请求编号", readonly=True, index=True)
    source_ip = fields.Char(string="来源 IP", readonly=True, groups="website_security_center.group_website_security_admin")
    assignee_id = fields.Many2one("res.users", string="负责人", tracking=True)
    resolution_note = fields.Text(string="处理说明", tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if vals.get("name", _("新事件")) == _("新事件"):
                vals["name"] = sequence.next_by_code("website.security.incident") or _("新事件")
        records = super().create(vals_list)
        records._schedule_review_activities()
        return records

    def _reviewers(self):
        group = self.env.ref("website_security_center.group_website_security_admin", raise_if_not_found=False)
        if not group:
            return self.env["res.users"]
        return group.user_ids.filtered(
            lambda user: user.active and not user.share
            and (not self.company_id or self.company_id in user.company_ids)
        )

    def _schedule_review_activities(self):
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        for incident in self.filtered(lambda record: record.state == "open"):
            summary = f"{ACTIVITY_PREFIX}{incident.name}"
            for reviewer in incident._reviewers():
                duplicate = incident.activity_ids.filtered(
                    lambda activity: activity.activity_type_id == activity_type
                    and activity.user_id == reviewer and activity.summary == summary
                )
                if not duplicate:
                    incident.activity_schedule(
                        activity_type_id=activity_type.id,
                        user_id=reviewer.id,
                        summary=summary,
                        note=_("请核查网站安全事件并记录处置结果。"),
                    )
            incident.message_post(body=_("安全事件已加入待处理队列。"))

    def _check_operator(self):
        if not self.env.user.has_group("website_security_center.group_website_security_operator"):
            raise AccessError(_("您无权处理网站安全事件。"))

    def _complete_review_activities(self):
        for incident in self:
            activities = incident.activity_ids.filtered(
                lambda activity: (activity.summary or "").startswith(ACTIVITY_PREFIX)
            )
            if activities:
                activities.action_feedback(feedback=_("网站安全事件已完成处置。"))

    def action_acknowledge(self):
        self._check_operator()
        self.filtered(lambda record: record.state == "open").write({
            "state": "acknowledged", "assignee_id": self.env.user.id,
        })
        return True

    def action_resolve(self):
        self._check_operator()
        if any(not record.resolution_note for record in self):
            raise ValidationError(_("解决事件前必须填写处理说明。"))
        self.write({"state": "resolved", "assignee_id": self.env.user.id})
        self._complete_review_activities()
        return True

    def action_ignore(self):
        self._check_operator()
        if any(not record.resolution_note for record in self):
            raise ValidationError(_("忽略事件前必须填写处理说明。"))
        self.write({"state": "ignored", "assignee_id": self.env.user.id})
        self._complete_review_activities()
        return True

    def action_quarantine_client(self):
        if not self.env.user.has_group("website_security_center.group_website_security_admin"):
            raise AccessError(_("只有网站安全管理员可以停用 API 客户端。"))
        clients = self.mapped("client_id").filtered("active")
        clients.write({"active": False})
        for incident in self:
            incident.message_post(body=_("关联 API 客户端已由 %s 停用。", self.env.user.display_name))
        return True

    def action_open_request_log(self):
        self.ensure_one()
        if not self.request_log_id:
            return False
        action = self.env["ir.actions.actions"]._for_xml_id("shop_api.action_shop_api_request_logs")
        action.update({"view_mode": "form", "res_id": self.request_log_id.id, "views": [(False, "form")]})
        return action


class WebsiteSecurityPolicy(models.Model):
    _name = "website.security.policy"
    _description = "Website Security Policy"
    _order = "company_id"

    name = fields.Char(string="策略名称", required=True, default="网站安全基线")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="公司", required=True, default=lambda self: self.env.company,
        ondelete="cascade", index=True,
    )
    fail_closed = fields.Boolean(string="关键操作故障时拒绝", default=True, readonly=True)
    require_erp_confirmation = fields.Boolean(string="订单与支付必须由 ERP 确认", default=True, readonly=True)
    require_https = fields.Boolean(string="生产连接必须使用 HTTPS", default=True)
    authentication_window_minutes = fields.Integer(string="登录异常统计分钟", default=10, required=True)
    authentication_failure_threshold = fields.Integer(string="登录失败告警次数", default=5, required=True)
    login_cooldown_failure_threshold = fields.Integer(
        string="账户连续登录失败上限", default=5, required=True,
        help="账户连续失败达到此次数后，暂停接受该账户的登录尝试。成功登录会清零失败次数。",
    )
    login_cooldown_minutes = fields.Integer(
        string="账户登录冷却分钟", default=60, required=True,
        help="账户进入登录冷却后，必须等待的分钟数。密码重置不受此冷却限制。",
    )
    slow_request_threshold_ms = fields.Integer(string="普通接口慢请求阈值（毫秒）", default=5000, required=True)
    payment_timeout_threshold_ms = fields.Integer(string="支付接口慢请求阈值（毫秒）", default=25000, required=True)
    webhook_failure_threshold = fields.Integer(string="Webhook 失败告警次数", default=3, required=True)
    incident_retention_days = fields.Integer(string="安全事件保留天数", default=180, required=True)
    last_scan_at = fields.Datetime(string="最近扫描时间", readonly=True)
    last_scan_summary = fields.Char(string="最近扫描结果", readonly=True)
    open_incident_count = fields.Integer(string="待处理事件", compute="_compute_dashboard_counts")
    critical_incident_count = fields.Integer(string="严重事件", compute="_compute_dashboard_counts")
    requests_24h = fields.Integer(string="24 小时请求", compute="_compute_dashboard_counts")
    errors_24h = fields.Integer(string="24 小时失败", compute="_compute_dashboard_counts")
    cooldown_account_count = fields.Integer(string="冷却中账户", compute="_compute_dashboard_counts")

    _unique_company = models.Constraint("UNIQUE(company_id)", "每个公司只能设置一条网站安全策略。")

    @api.constrains(
        "authentication_window_minutes", "authentication_failure_threshold",
        "login_cooldown_failure_threshold", "login_cooldown_minutes",
        "slow_request_threshold_ms", "payment_timeout_threshold_ms",
        "webhook_failure_threshold", "incident_retention_days",
    )
    def _check_positive_values(self):
        for record in self:
            if min(
                record.authentication_window_minutes,
                record.authentication_failure_threshold,
                record.login_cooldown_failure_threshold,
                record.login_cooldown_minutes,
                record.slow_request_threshold_ms,
                record.payment_timeout_threshold_ms,
                record.webhook_failure_threshold,
                record.incident_retention_days,
            ) <= 0:
                raise ValidationError(_("网站安全策略中的阈值必须大于零。"))

    def _compute_dashboard_counts(self):
        incident_model = self.env["website.security.incident"].sudo()
        request_model = self.env["shop.api.request.log"].sudo()
        since = fields.Datetime.now() - timedelta(hours=24)
        for policy in self:
            base = [("company_id", "=", policy.company_id.id)]
            policy.open_incident_count = incident_model.search_count(base + [("state", "in", OPEN_STATES)])
            policy.critical_incident_count = incident_model.search_count(
                base + [("state", "in", OPEN_STATES), ("severity", "=", "critical")]
            )
            client_ids = self.env["shop.api.client"].sudo().search([
                "|", ("company_ids", "=", False), ("company_ids", "in", policy.company_id.id),
            ]).ids
            request_domain = [("create_date", ">=", since), ("client_id", "in", client_ids)]
            policy.requests_24h = request_model.search_count(request_domain)
            policy.errors_24h = request_model.search_count(request_domain + [("state", "=", "error")])
            policy.cooldown_account_count = self.env["res.users"].sudo().search_count([
                ("active", "=", True),
                ("company_id", "=", policy.company_id.id),
                ("security_login_cooldown_until", ">", fields.Datetime.now()),
            ])

    @api.model
    def _ensure_defaults(self):
        for company in self.env["res.company"].sudo().search([]):
            if not self.sudo().search([("company_id", "=", company.id)], limit=1):
                self.sudo().create({"company_id": company.id, "name": _("网站安全基线")})

    @staticmethod
    def _fingerprint(*parts):
        canonical = "|".join(str(part or "-") for part in parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _upsert_incident(self, values):
        incident_model = self.env["website.security.incident"].sudo()
        existing = incident_model.search([
            ("company_id", "=", self.company_id.id),
            ("fingerprint", "=", values["fingerprint"]),
            ("state", "in", OPEN_STATES),
        ], limit=1)
        if existing:
            existing.write({
                "last_seen_at": values.get("last_seen_at", fields.Datetime.now()),
                "occurrence_count": existing.occurrence_count + 1,
                "request_log_id": values.get("request_log_id") or existing.request_log_id.id,
                "webhook_delivery_id": values.get("webhook_delivery_id") or existing.webhook_delivery_id.id,
                "request_id": values.get("request_id") or existing.request_id,
            })
            return existing
        values["company_id"] = self.company_id.id
        return incident_model.create(values)

    def _clients(self):
        return self.env["shop.api.client"].sudo().search([
            "|", ("company_ids", "=", False), ("company_ids", "in", self.company_id.id),
        ])

    def _classify_request(self, log):
        path = log.path or ""
        error_code = (log.error_code or "").lower()
        if log.response_status in (401, 403):
            recent = fields.Datetime.now() - timedelta(minutes=self.authentication_window_minutes)
            count = self.env["shop.api.request.log"].sudo().search_count([
                ("create_date", ">=", recent),
                ("source_ip", "=", log.source_ip),
                ("response_status", "in", (401, 403)),
            ])
            if count < self.authentication_failure_threshold:
                return False
            return "authentication", "high", _("同一来源多次身份验证失败")
        if log.response_status == 429:
            return "rate_limit", "medium", _("API 请求触发访问频率限制")
        if "idempot" in error_code:
            return "idempotency", "high", _("关键请求发生幂等冲突")
        if path.endswith("/payments") and log.response_status >= 400:
            return "payment", "high", _("支付初始化接口返回失败")
        if log.response_status >= 500:
            return "server_error", "critical", _("商城 API 出现服务端错误")
        threshold = self.payment_timeout_threshold_ms if "/payments" in path else self.slow_request_threshold_ms
        if (log.duration_ms or 0) >= threshold:
            severity = "high" if "/payments" in path else "medium"
            return "slow_request", severity, _("接口响应时间超过安全阈值")
        return False

    def _scan_request_logs(self, since):
        count = 0
        logs = self.env["shop.api.request.log"].sudo().search([
            ("create_date", ">", since),
            "|", ("client_id", "=", False), ("client_id", "in", self._clients().ids),
        ], order="create_date,id")
        for log in logs:
            classification = self._classify_request(log)
            if not classification:
                continue
            category, severity, summary = classification
            fingerprint = self._fingerprint(
                category, log.client_id.id, log.path, log.source_ip, log.error_code,
            )
            self._upsert_incident({
                "fingerprint": fingerprint,
                "category": category,
                "severity": severity,
                "summary": summary,
                "safe_details": _(
                    "HTTP 状态：%(status)s；错误代码：%(code)s；耗时：%(duration)s 毫秒；请求编号：%(request)s",
                    status=log.response_status or 0,
                    code=log.error_code or "-",
                    duration=log.duration_ms or 0,
                    request=log.request_id,
                ),
                "occurred_at": log.create_date,
                "last_seen_at": log.create_date,
                "request_log_id": log.id,
                "client_id": log.client_id.id,
                "endpoint_id": log.endpoint_id.id,
                "request_id": log.request_id,
                "source_ip": log.source_ip,
            })
            count += 1
        return count

    def _scan_webhooks(self, since):
        count = 0
        deliveries = self.env["shop.api.webhook.delivery"].sudo().search([
            ("write_date", ">", since),
            ("state", "=", "failed"),
            ("attempt_count", ">=", self.webhook_failure_threshold),
        ], order="write_date,id")
        for delivery in deliveries:
            webhook = delivery.webhook_id
            fingerprint = self._fingerprint("webhook", webhook.id, delivery.event_id.event_type)
            self._upsert_incident({
                "fingerprint": fingerprint,
                "category": "webhook",
                "severity": "high",
                "summary": _("Webhook 连续投递失败"),
                "safe_details": _(
                    "Webhook：%(webhook)s；尝试次数：%(attempts)s；HTTP 状态：%(status)s",
                    webhook=webhook.display_name,
                    attempts=delivery.attempt_count,
                    status=delivery.response_status or 0,
                ),
                "occurred_at": delivery.create_date,
                "last_seen_at": delivery.write_date,
                "webhook_delivery_id": delivery.id,
            })
            count += 1
        return count

    def _run_scan(self):
        self.ensure_one()
        now = fields.Datetime.now()
        since = self.last_scan_at or now - timedelta(minutes=15)
        request_count = self._scan_request_logs(since)
        webhook_count = self._scan_webhooks(since)
        self.write({
            "last_scan_at": now,
            "last_scan_summary": _(
                "已检查新增记录，识别 %(requests)s 个 API 异常、%(webhooks)s 个 Webhook 异常。",
                requests=request_count, webhooks=webhook_count,
            ),
        })
        return request_count + webhook_count

    def action_run_scan(self):
        if not self.env.user.has_group("website_security_center.group_website_security_admin"):
            raise AccessError(_("只有网站安全管理员可以立即执行扫描。"))
        total = sum(policy._run_scan() for policy in self)
        self.env["website.security.health.check"].sudo().search([
            ("company_id", "in", self.company_id.ids), ("active", "=", True),
        ]).action_run_check()
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": _("网站安全扫描完成"), "message": _("本次识别 %s 条异常记录。", total),
                       "type": "success", "sticky": False},
        }

    def action_open_incidents(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "website_security_center.action_website_security_incidents_pending"
        )
        action["domain"] = [("company_id", "=", self.company_id.id), ("state", "in", OPEN_STATES)]
        return action

    def action_open_cooldown_accounts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("登录冷却账户"),
            "res_model": "res.users",
            "view_mode": "list,form",
            "views": [
                (self.env.ref("website_security_center.view_security_login_cooldown_user_list").id, "list"),
                (False, "form"),
            ],
            "domain": [
                ("active", "=", True),
                ("company_id", "=", self.company_id.id),
                ("security_login_cooldown_until", ">", fields.Datetime.now()),
            ],
        }

    def _record_login_cooldown_incident(self, user, cooldown_until, source_ip=None):
        self.ensure_one()
        fingerprint = self._fingerprint("account_login_cooldown", user.id)
        return self._upsert_incident({
            "fingerprint": fingerprint,
            "category": "authentication",
            "severity": "high",
            "summary": _("账户因连续登录失败进入冷却"),
            "safe_details": _(
                "账户：%(user)s（ID %(user_id)s）；连续失败达到 %(threshold)s 次；冷却至 %(until)s。"
                "密码重置仍可使用。",
                user=user.display_name,
                user_id=user.id,
                threshold=self.login_cooldown_failure_threshold,
                until=fields.Datetime.to_string(cooldown_until),
            ),
            "occurred_at": fields.Datetime.now(),
            "last_seen_at": fields.Datetime.now(),
            "source_ip": (source_ip or "")[:128],
        })

    @api.model
    def _cron_scan_and_check(self):
        self._ensure_defaults()
        for policy in self.sudo().search([("active", "=", True)]):
            policy._run_scan()
        checks = self.env["website.security.health.check"].sudo()
        checks._ensure_defaults()
        checks.search([("active", "=", True)]).action_run_check()


class WebsiteSecurityHealthCheck(models.Model):
    _name = "website.security.health.check"
    _description = "Website Security Health Check"
    _order = "state desc, sequence, id"

    name = fields.Char(string="检查项目", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="公司", required=True, default=lambda self: self.env.company,
        ondelete="cascade", index=True,
    )
    check_type = fields.Selection([
        ("api", "ERP API 通道"),
        ("webhook", "Webhook 投递"),
        ("payment", "支付运行环境"),
        ("transport", "HTTPS 与返回来源"),
    ], string="检查类型", required=True)
    state = fields.Selection([
        ("unknown", "未检查"), ("healthy", "正常"),
        ("warning", "警告"), ("critical", "严重"),
    ], string="状态", required=True, default="unknown", index=True)
    last_checked_at = fields.Datetime(string="最近检查", readonly=True)
    response_time_ms = fields.Integer(string="最近响应耗时（毫秒）", readonly=True)
    message = fields.Char(string="检查结果", readonly=True)

    _unique_company_type = models.Constraint(
        "UNIQUE(company_id, check_type)", "每个公司只能设置一项同类型安全检查。"
    )

    @api.model
    def _ensure_defaults(self):
        names = {
            "api": _("ERP API 通道"), "webhook": _("Webhook 投递"),
            "payment": _("支付运行环境"), "transport": _("HTTPS 与返回来源"),
        }
        for company in self.env["res.company"].sudo().search([]):
            for sequence, (check_type, name) in enumerate(names.items(), start=1):
                if not self.sudo().search([
                    ("company_id", "=", company.id), ("check_type", "=", check_type),
                ], limit=1):
                    self.sudo().create({
                        "company_id": company.id, "check_type": check_type,
                        "name": name, "sequence": sequence * 10,
                    })

    def _result(self, state, message, response_time_ms=0):
        self.write({
            "state": state, "message": message,
            "response_time_ms": response_time_ms,
            "last_checked_at": fields.Datetime.now(),
        })

    def _run_api_check(self):
        clients = self.env["shop.api.client"].sudo().search([
            ("active", "=", True),
            "|", ("company_ids", "=", False), ("company_ids", "in", self.company_id.id),
        ])
        if not clients:
            return self._result("critical", _("没有启用的商城 API 客户端。"))
        last = self.env["shop.api.request.log"].sudo().search([
            ("client_id", "in", clients.ids), ("state", "=", "success"),
        ], order="create_date desc", limit=1)
        if not last or last.create_date < fields.Datetime.now() - timedelta(hours=24):
            return self._result("warning", _("24 小时内没有成功的商城 API 请求。"))
        return self._result("healthy", _("商城与 ERP API 最近通信正常。"), last.duration_ms or 0)

    def _run_webhook_check(self):
        failed = self.env["shop.api.webhook.delivery"].sudo().search_count([
            ("state", "=", "failed"), ("write_date", ">=", fields.Datetime.now() - timedelta(hours=1)),
        ])
        if failed:
            return self._result("critical", _("最近一小时有 %s 条 Webhook 投递失败。", failed))
        pending = self.env["shop.api.webhook.delivery"].sudo().search_count([
            ("state", "=", "pending"), ("create_date", "<", fields.Datetime.now() - timedelta(minutes=10)),
        ])
        if pending:
            return self._result("warning", _("有 %s 条 Webhook 等待超过 10 分钟。", pending))
        return self._result("healthy", _("Webhook 队列未发现积压或失败。"))

    def _run_payment_check(self):
        providers = self.env["payment.provider"].sudo().search([("state", "in", ("test", "enabled"))])
        if not providers:
            return self._result("warning", _("当前没有启用的在线支付服务。"))
        lianlian = providers.filtered(lambda provider: provider.code == "lianlian")[:1]
        if lianlian:
            node_binary = lianlian.lianlian_node_binary or "node"
            if os.path.isabs(node_binary) and not os.path.isfile(node_binary):
                return self._result("critical", _("连连支付 Node.js 运行程序不存在。"))
        return self._result("healthy", _("在线支付服务配置与本地运行环境可用。"))

    def _run_transport_check(self):
        configurations = self.env["shop.api.configuration"].sudo().search([
            ("company_id", "=", self.company_id.id), ("active", "=", True),
        ])
        invalid = []
        for configuration in configurations:
            urls = [configuration.public_erp_base_url, configuration.shop_base_url]
            urls += (configuration.allowed_shop_return_origins or "").splitlines()
            for value in filter(None, urls):
                parsed = urlparse(value.strip())
                if configuration.environment == "production" and parsed.scheme != "https":
                    invalid.append(value.strip())
        if invalid:
            return self._result("critical", _("生产连接或支付返回来源存在非 HTTPS 地址。"))
        return self._result("healthy", _("生产连接与支付返回来源均使用 HTTPS。"))

    def action_run_check(self):
        for check in self:
            {
                "api": check._run_api_check,
                "webhook": check._run_webhook_check,
                "payment": check._run_payment_check,
                "transport": check._run_transport_check,
            }[check.check_type]()
        return True


class ResUsers(models.Model):
    _inherit = "res.users"

    security_login_failure_count = fields.Integer(
        string="连续登录失败次数",
        default=0,
        readonly=True,
        copy=False,
        groups="website_security_center.group_website_security_admin",
    )
    security_login_last_failure_at = fields.Datetime(
        string="最近登录失败时间",
        readonly=True,
        copy=False,
        groups="website_security_center.group_website_security_admin",
    )
    security_login_cooldown_until = fields.Datetime(
        string="登录冷却截止时间",
        readonly=True,
        copy=False,
        index=True,
        groups="website_security_center.group_website_security_admin",
    )

    def _website_security_login_policy(self):
        self.ensure_one()
        return self.env["website.security.policy"].sudo().search([
            ("company_id", "=", self.company_id.id),
            ("active", "=", True),
        ], limit=1)

    def _website_security_login_cooldown_status(self):
        self.ensure_one()
        now = fields.Datetime.now()
        cooldown_until = self.security_login_cooldown_until
        remaining_seconds = 0
        if cooldown_until and cooldown_until > now:
            remaining_seconds = max(
                1, int((cooldown_until - now).total_seconds() + 0.999)
            )
        policy = self._website_security_login_policy()
        return {
            "locked": bool(remaining_seconds),
            "retry_after_seconds": remaining_seconds,
            "cooldown_until": (
                fields.Datetime.to_string(cooldown_until)
                if remaining_seconds else False
            ),
            "failure_count": self.security_login_failure_count,
            "failure_threshold": policy.login_cooldown_failure_threshold if policy else 5,
            "cooldown_minutes": policy.login_cooldown_minutes if policy else 60,
        }

    def _website_security_register_login_failure(self, source_ip=None):
        """Persist one account failure with a database row lock.

        This method is intentionally SQL-backed so parallel Shop workers cannot
        lose increments.  It stores counters and timestamps only; credentials
        and request bodies are never retained.
        """
        self.ensure_one()
        policy = self._website_security_login_policy()
        threshold = policy.login_cooldown_failure_threshold if policy else 5
        cooldown_minutes = policy.login_cooldown_minutes if policy else 60
        now = fields.Datetime.now()
        self.env.cr.execute(
            """
                SELECT security_login_failure_count,
                       security_login_cooldown_until
                  FROM res_users
                 WHERE id = %s
                 FOR UPDATE
            """,
            [self.id],
        )
        row = self.env.cr.fetchone()
        if not row:
            return {"locked": False, "retry_after_seconds": 0}
        failure_count, previous_cooldown_until = row
        if previous_cooldown_until and previous_cooldown_until > now:
            cooldown_until = previous_cooldown_until
            failure_count = max(failure_count or 0, threshold)
        else:
            failure_count = 1 if previous_cooldown_until else (failure_count or 0) + 1
            cooldown_until = (
                now + timedelta(minutes=cooldown_minutes)
                if failure_count >= threshold else None
            )
        self.env.cr.execute(
            """
                UPDATE res_users
                   SET security_login_failure_count = %s,
                       security_login_last_failure_at = %s,
                       security_login_cooldown_until = %s
                 WHERE id = %s
            """,
            [failure_count, now, cooldown_until, self.id],
        )
        self.invalidate_recordset([
            "security_login_failure_count",
            "security_login_last_failure_at",
            "security_login_cooldown_until",
        ])
        if cooldown_until and (
            not previous_cooldown_until or previous_cooldown_until <= now
        ) and policy:
            try:
                with self.env.cr.savepoint():
                    policy._record_login_cooldown_incident(
                        self, cooldown_until, source_ip=source_ip
                    )
            except Exception:
                # Incident delivery must never undo the authoritative lock.
                _logger.exception(
                    "Unable to create the login-cooldown incident for user id %s",
                    self.id,
                )
        return self._website_security_login_cooldown_status()

    def _website_security_clear_login_failures(self):
        users = self.sudo().filtered(
            lambda user: user.security_login_failure_count
            or user.security_login_last_failure_at
            or user.security_login_cooldown_until
        )
        if users:
            users.write({
                "security_login_failure_count": 0,
                "security_login_last_failure_at": False,
                "security_login_cooldown_until": False,
            })

    @api.model
    def _website_security_register_login_failure_durable(
        self, user_id, source_ip=None
    ):
        """Record a rejected login outside the request savepoint.

        Odoo rolls back failed authentication transactions.  A short dedicated
        cursor makes the security counter durable without retaining credentials.
        """
        try:
            with self.env.registry.cursor() as security_cr:
                security_env = api.Environment(security_cr, SUPERUSER_ID, {})
                user = security_env["res.users"].sudo().browse(user_id).exists()
                if user:
                    user._website_security_register_login_failure(source_ip=source_ip)
                security_cr.commit()
        except Exception:
            _logger.exception(
                "Unable to persist the account login-failure counter for user id %s",
                user_id,
            )

    @api.model
    def authenticate(self, credential, user_agent_env):
        if credential.get("type") != "password":
            return super().authenticate(credential, user_agent_env)

        login = str(credential.get("login") or "").strip()
        user = self.sudo().with_context(active_test=False).search([
            ("login", "=", login),
            ("active", "=", True),
        ], limit=1)
        if user and user._website_security_login_cooldown_status()["locked"]:
            raise AccessDenied(_("账户处于登录冷却期，请稍后重试。"))

        try:
            auth_info = super().authenticate(credential, user_agent_env)
        except AccessDenied:
            if user:
                source_ip = (user_agent_env or {}).get("REMOTE_ADDR")
                self._website_security_register_login_failure_durable(
                    user.id, source_ip=source_ip
                )
            raise

        if user:
            user._website_security_clear_login_failures()
        return auth_info
