import datetime
import secrets
from urllib.parse import urlparse

from odoo import _, Command, api, fields, models
from odoo.exceptions import ValidationError


BUILTIN_SCOPES = [
    ("system:read", "系统信息读取"),
    ("configuration:read", "商城配置读取"),
    ("documentation:read", "接口文档读取"),
    ("catalog:read", "产品目录读取"),
    ("pricing:read", "价格读取"),
    ("media:read", "产品媒体读取"),
    ("inventory:read", "库存读取"),
    ("reservation:read", "库存预留读取"),
    ("reservation:write", "库存预留操作"),
    ("customer:read", "客户读取"),
    ("customer:write", "客户维护"),
    ("order:read", "订单读取"),
    ("order:write", "订单操作"),
    ("payment:read", "支付读取"),
    ("payment:write", "支付创建"),
    ("payment:reconcile", "支付对账"),
    ("shipment:read", "物流读取"),
    ("shipment:write", "物流信息操作"),
    ("refund:read", "退款读取"),
    ("refund:write", "退款申请操作"),
    ("document:read", "财务单据读取"),
    ("event:read", "事件读取"),
    ("event:retry", "事件重试"),
    ("sync:read", "同步状态读取"),
    ("sync:write", "同步与对账操作"),
]


BUILTIN_SCOPES.extend([
    ("site:read", "网站内容读取"),
    ("checkout:write", "结账报价创建"),
])


BUILTIN_ENDPOINTS = [
    ("health", "GET", "/api/v1/health", False, False, "健康检查", "Health check"),
    ("capabilities", "GET", "/api/v1/capabilities", "system:read", False, "能力与版本", "Capabilities and version"),
    ("shop_configuration", "GET", "/api/v1/shop/configuration", "configuration:read", False, "商城配置", "Shop configuration"),
    ("countries", "GET", "/api/v1/countries", "configuration:read", False, "可用国家地区", "Available countries"),
    ("shipping_methods", "GET", "/api/v1/shipping-methods", "configuration:read", False, "配送方式", "Shipping methods"),
    ("payment_methods", "GET", "/api/v1/payment-methods", "configuration:read", False, "支付方式", "Payment methods"),
    ("openapi", "GET", "/api/v1/openapi.json", "documentation:read", False, "OpenAPI 文档", "OpenAPI document"),
    ("products", "GET", "/api/v1/products", "catalog:read", False, "产品列表", "Product list"),
    ("product_detail", "GET", "/api/v1/products/{uuid}", "catalog:read", False, "产品详情", "Product detail"),
    ("product_changes", "GET", "/api/v1/products/changes", "catalog:read", False, "产品变更", "Product changes"),
    ("product_groups", "GET", "/api/v1/product-groups", "catalog:read", False, "商城产品组", "Product groups"),
    ("categories", "GET", "/api/v1/categories", "catalog:read", False, "产品类别", "Product categories"),
    ("attributes", "GET", "/api/v1/attributes", "catalog:read", False, "产品属性", "Product attributes"),
    ("pricelist_prices", "GET", "/api/v1/pricelists/{uuid}/prices", "pricing:read", False, "价目表价格", "Pricelist prices"),
    ("product_images", "GET", "/api/v1/products/{uuid}/images", "media:read", False, "产品图片清单", "Product image list"),
    ("media", "GET", "/api/v1/media/{uuid}", "media:read", False, "媒体内容", "Media content"),
    ("inventory_check", "POST", "/api/v1/inventory/check", "inventory:read", False, "库存校验", "Inventory check"),
    ("inventory_snapshot", "GET", "/api/v1/inventory/snapshot", "inventory:read", False, "库存快照", "Compact inventory snapshot"),
    ("inventory_changes", "GET", "/api/v1/inventory/changes", "inventory:read", False, "库存变更", "Inventory changes"),
    ("reservation_create", "POST", "/api/v1/reservations", "reservation:write", True, "创建库存预留", "Create reservation"),
    ("reservation_detail", "GET", "/api/v1/reservations/{uuid}", "reservation:read", False, "库存预留详情", "Reservation detail"),
    ("reservation_extend", "POST", "/api/v1/reservations/{uuid}/extend", "reservation:write", True, "延长库存预留", "Extend reservation"),
    ("reservation_confirm", "POST", "/api/v1/reservations/{uuid}/confirm", "reservation:write", True, "确认库存预留", "Confirm reservation"),
    ("reservation_release", "POST", "/api/v1/reservations/{uuid}/release", "reservation:write", True, "释放库存预留", "Release reservation"),
    ("customer_upsert", "POST", "/api/v1/customers/upsert", "customer:write", True, "新增或更新客户", "Upsert customer"),
    ("customer_authenticate", "POST", "/api/v1/customers/authenticate", "customer:read", False, "验证商城客户账户", "Authenticate storefront customer"),
    ("customer_detail", "GET", "/api/v1/customers/{uuid}", "customer:read", False, "客户详情", "Customer detail"),
    ("customer_external", "GET", "/api/v1/customers/by-external-id/{id}", "customer:read", False, "按外部编号查询客户", "Customer by external ID"),
    ("address_create", "POST", "/api/v1/customers/{uuid}/addresses", "customer:write", True, "新增客户地址", "Create customer address"),
    ("address_update", "PATCH", "/api/v1/customers/{uuid}/addresses/{address_uuid}", "customer:write", True, "更新客户地址", "Update customer address"),
    ("address_delete", "DELETE", "/api/v1/customers/{uuid}/addresses/{address_uuid}", "customer:write", True, "停用客户地址", "Archive customer address"),
    ("order_create", "POST", "/api/v1/orders", "order:write", True, "创建订单", "Create order"),
    ("order_detail", "GET", "/api/v1/orders/{uuid}", "order:read", False, "订单详情", "Order detail"),
    ("order_external", "GET", "/api/v1/orders/by-external-id/{id}", "order:read", False, "按外部编号查询订单", "Order by external ID"),
    ("order_changes", "GET", "/api/v1/orders/changes", "order:read", False, "订单变更", "Order changes"),
    ("order_update", "PATCH", "/api/v1/orders/{uuid}", "order:write", True, "更新草稿订单", "Update draft order"),
    ("order_confirm", "POST", "/api/v1/orders/{uuid}/confirm", "order:write", True, "确认订单", "Confirm order"),
    ("order_cancel", "POST", "/api/v1/orders/{uuid}/cancel", "order:write", True, "取消订单", "Cancel order"),
    ("order_documents", "GET", "/api/v1/orders/{uuid}/documents", "document:read", False, "订单单据", "Order documents"),
    ("payment_create", "POST", "/api/v1/orders/{uuid}/payments", "payment:write", True, "创建支付", "Create payment"),
    ("payment_detail", "GET", "/api/v1/payments/{uuid}", "payment:read", False, "支付详情", "Payment detail"),
    ("payment_simulate_success", "POST", "/api/v1/payments/{uuid}/simulate-success", "payment:write", True, "模拟支付成功", "Simulate successful payment"),
    ("order_payments", "GET", "/api/v1/orders/{uuid}/payments", "payment:read", False, "订单支付", "Order payments"),
    ("payment_reconcile", "POST", "/api/v1/payments/{uuid}/reconcile", "payment:reconcile", True, "支付对账", "Reconcile payment"),
    ("order_shipments", "GET", "/api/v1/orders/{uuid}/shipments", "shipment:read", False, "订单物流", "Order shipments"),
    ("shipment_detail", "GET", "/api/v1/shipments/{uuid}", "shipment:read", False, "物流详情", "Shipment detail"),
    ("shipping_address_change", "POST", "/api/v1/orders/{uuid}/shipping-address-change", "shipment:write", True, "变更配送地址", "Change shipping address"),
    ("refund_request_create", "POST", "/api/v1/orders/{uuid}/refund-requests", "refund:write", True, "创建退款申请", "Create refund request"),
    ("refund_request_detail", "GET", "/api/v1/refund-requests/{uuid}", "refund:read", False, "退款申请详情", "Refund request detail"),
    ("order_refund_requests", "GET", "/api/v1/orders/{uuid}/refund-requests", "refund:read", False, "订单退款申请", "Order refund requests"),
    ("refund_request_cancel", "POST", "/api/v1/refund-requests/{uuid}/cancel", "refund:write", True, "取消退款申请", "Cancel refund request"),
    ("return_instructions", "GET", "/api/v1/refund-requests/{uuid}/return-instructions", "refund:read", False, "退货说明", "Return instructions"),
    ("return_shipped", "POST", "/api/v1/refund-requests/{uuid}/return-shipped", "refund:write", True, "登记退货发出", "Register return shipment"),
    ("refund_detail", "GET", "/api/v1/refunds/{uuid}", "refund:read", False, "退款交易详情", "Refund transaction detail"),
    ("credit_note_detail", "GET", "/api/v1/credit-notes/{uuid}", "document:read", False, "贷项通知单详情", "Credit note detail"),
    ("event_detail", "GET", "/api/v1/events/{event_id}", "event:read", False, "事件详情", "Event detail"),
    ("events", "GET", "/api/v1/events", "event:read", False, "事件列表", "Event list"),
    ("event_retry", "POST", "/api/v1/events/{event_id}/retry", "event:retry", True, "重试事件", "Retry event"),
    ("sync_checkpoint", "GET", "/api/v1/sync/checkpoints/{resource}", "sync:read", False, "同步检查点", "Sync checkpoint"),
    ("sync_changes", "GET", "/api/v1/sync/changes", "sync:read", False, "同步变更", "Synchronization changes"),
    ("sync_reconcile", "POST", "/api/v1/sync/reconcile", "sync:write", True, "执行对账", "Run reconciliation"),
]


BUILTIN_ENDPOINTS.extend([
    ("site_configuration", "GET", "/api/v1/site/configuration", "site:read", False, "网站配置", "Localized website configuration"),
    ("site_navigation", "GET", "/api/v1/site/navigation", "site:read", False, "网站导航", "Localized website navigation"),
    ("site_pages", "GET", "/api/v1/site/pages", "site:read", False, "网站页面列表", "Localized website page list"),
    ("site_page_detail", "GET", "/api/v1/site/pages/{page_id}", "site:read", False, "网站页面详情", "Localized website page detail"),
    ("site_legacy_routes", "GET", "/api/v1/site/legacy-routes", "site:read", False, "旧网址映射", "Legacy and localized route mapping"),
    ("checkout_quote", "POST", "/api/v1/checkout/quote", "checkout:write", True, "创建结账报价", "Create authoritative checkout quote"),
    ("customer_orders", "GET", "/api/v1/customers/{uuid}/orders", "order:read", False, "客户订单列表", "Customer order list"),
    ("customer_refunds", "GET", "/api/v1/customers/{uuid}/refund-requests", "refund:read", False, "客户退款列表", "Customer refund request list"),
])


BUILTIN_EVENT_TYPES = [
    "product.created", "product.updated", "product.archived", "product.image.updated",
    "price.updated", "inventory.updated", "reservation.expired", "order.created",
    "order.confirmed", "order.cancelled", "payment.pending", "payment.completed",
    "payment.failed", "shipment.ready", "shipment.shipped", "shipment.delivered",
    "refund.requested", "refund.updated", "return.required", "return.received",
    "credit_note.created",
]
BUILTIN_EVENT_TYPES.extend([
    "site.page.updated", "site.menu.updated", "site.media.updated", "site.configuration.updated",
])


class ShopApiScope(models.Model):
    _name = "shop.api.scope"
    _description = "Shop API Permission Scope"
    _order = "code"

    name = fields.Char(string="权限名称", required=True, translate=True)
    code = fields.Char(string="权限代码", required=True, index=True)
    active = fields.Boolean(default=True)
    description = fields.Text(string="说明", translate=True)

    _unique_code = models.Constraint("UNIQUE(code)", "API 权限代码必须唯一。")

    @api.model
    def _ensure_builtin_scopes(self):
        for code, name in BUILTIN_SCOPES:
            record = self.search([("code", "=", code)], limit=1)
            values = {"name": name, "active": True}
            record.write(values) if record else self.create({"code": code, **values})


class ShopApiEndpoint(models.Model):
    _name = "shop.api.endpoint"
    _description = "Shop API Endpoint"
    _order = "path, method"

    name = fields.Char(string="接口名称", required=True, translate=True)
    code = fields.Char(string="接口代码", required=True, index=True)
    version = fields.Char(string="版本", default="v1", required=True)
    method = fields.Selection(
        [(item, item) for item in ("GET", "POST", "PATCH", "PUT", "DELETE")],
        string="HTTP 方法",
        required=True,
    )
    path = fields.Char(string="路径", required=True, index=True)
    scope_id = fields.Many2one("shop.api.scope", string="所需权限", ondelete="restrict")
    authentication_required = fields.Boolean(string="需要认证", default=True)
    idempotency_required = fields.Boolean(string="需要幂等键")
    active = fields.Boolean(default=True)
    summary_en = fields.Char(string="英文说明")
    request_example = fields.Text(string="请求示例")
    response_example = fields.Text(string="响应示例")
    rate_limit_per_minute = fields.Integer(string="每分钟请求上限", default=120)
    log_request_body = fields.Boolean(string="记录请求正文", default=True)
    log_response_body = fields.Boolean(string="记录响应正文", default=True)

    _unique_code = models.Constraint("UNIQUE(code)", "API 接口代码必须唯一。")
    _unique_route = models.Constraint(
        "UNIQUE(version, method, path)",
        "同一版本中的 HTTP 方法与路径组合必须唯一。",
    )

    @api.model
    def _ensure_builtin_endpoints(self):
        self.env["shop.api.scope"]._ensure_builtin_scopes()
        scopes = {scope.code: scope for scope in self.env["shop.api.scope"].search([])}
        for code, method, path, scope_code, idempotency, name, summary_en in BUILTIN_ENDPOINTS:
            record = self.search([("code", "=", code)], limit=1)
            values = {
                "name": name,
                "method": method,
                "path": path,
                "scope_id": scopes[scope_code].id if scope_code else False,
                "authentication_required": bool(scope_code),
                "idempotency_required": idempotency,
                "summary_en": summary_en,
                "active": True,
            }
            if code == "customer_authenticate":
                values.update({
                    "log_request_body": False,
                    "log_response_body": False,
                })
            record.write(values) if record else self.create({"code": code, **values})


class ShopApiConfiguration(models.Model):
    _name = "shop.api.configuration"
    _description = "Shop API Configuration"
    _rec_name = "name"

    name = fields.Char(string="配置名称", required=True, default="默认商城 API 配置")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="公司", required=True, default=lambda self: self.env.company,
        ondelete="cascade",
    )
    website_id = fields.Many2one("website", string="Odoo 网站", ondelete="set null")
    environment = fields.Selection(
        [("test", "测试"), ("staging", "预发布"), ("production", "生产")],
        string="环境",
        default="test",
        required=True,
    )
    api_version = fields.Char(string="API 版本", default="v1", required=True)
    api_base_path = fields.Char(string="API 基础路径", default="/api/v1", required=True)
    public_erp_base_url = fields.Char(string="ERP 公共地址")
    shop_base_url = fields.Char(string="商城地址", default="http://127.0.0.1:8070")
    allowed_shop_return_origins = fields.Text(
        string="允许的支付返回来源",
        help="每行一个完整来源（协议+主机+端口），用于并行测试或迁移。",
    )
    reservation_ttl_minutes = fields.Integer(string="库存预留分钟数", default=15, required=True)
    reservation_max_extension_minutes = fields.Integer(string="最大延长分钟数", default=15)
    request_timeout_seconds = fields.Integer(string="Webhook 超时秒数", default=10)
    webhook_retry_count = fields.Integer(string="Webhook 最大重试次数", default=8)
    webhook_retry_backoff_seconds = fields.Integer(string="Webhook 初始退避秒数", default=30)
    default_page_size = fields.Integer(string="默认分页数量", default=50)
    maximum_page_size = fields.Integer(string="最大分页数量", default=200)
    event_retention_days = fields.Integer(string="事件保留天数", default=90)
    request_log_retention_days = fields.Integer(string="请求记录保留天数", default=30)
    media_cache_ttl_seconds = fields.Integer(string="媒体缓存秒数", default=86400)
    allow_payment_simulators = fields.Boolean(string="允许支付模拟器", default=True)
    allowed_clock_skew_seconds = fields.Integer(string="签名时间允许偏差秒数", default=300)
    chinese_pricelist_id = fields.Many2one("product.pricelist", string="中文价目表")
    english_pricelist_id = fields.Many2one("product.pricelist", string="英文价目表")
    notes = fields.Text(string="备注")

    def payment_return_origins(self):
        self.ensure_one()
        values = [self.shop_base_url, *(self.allowed_shop_return_origins or "").splitlines()]
        origins = set()
        for value in values:
            parsed = urlparse((value or "").strip())
            if parsed.scheme in ("http", "https") and parsed.netloc:
                origins.add(f"{parsed.scheme}://{parsed.netloc}")
        return origins

    _unique_company_website = models.Constraint(
        "UNIQUE(company_id, website_id)",
        "每个公司与网站只能有一条 Shop API 配置。",
    )

    @api.constrains(
        "reservation_ttl_minutes", "request_timeout_seconds", "default_page_size",
        "maximum_page_size", "webhook_retry_count",
    )
    def _check_positive_limits(self):
        for record in self:
            if min(
                record.reservation_ttl_minutes,
                record.request_timeout_seconds,
                record.default_page_size,
                record.maximum_page_size,
            ) <= 0 or record.webhook_retry_count < 0:
                raise ValidationError(_("API 时间、分页配置必须为正数，重试次数不能为负数。"))
            if record.default_page_size > record.maximum_page_size:
                raise ValidationError(_("默认分页数量不能大于最大分页数量。"))
            if record.environment == "production" and record.allow_payment_simulators:
                raise ValidationError(_("生产环境不能启用支付模拟器。"))

    @api.model
    def _ensure_default_configuration(self):
        company = self.env.company
        website = self.env["website"].search([("company_id", "=", company.id)], limit=1)
        configuration = self.search([
            ("company_id", "=", company.id),
            ("website_id", "=", website.id or False),
        ], limit=1)
        if not configuration:
            configuration = self.create({
                "company_id": company.id,
                "website_id": website.id or False,
                "public_erp_base_url": website.get_base_url() if website else False,
            })
        values = {}
        if not configuration.chinese_pricelist_id:
            values["chinese_pricelist_id"] = self.env["product.pricelist"].search([
                ("currency_id", "=", company.currency_id.id),
                "|", ("website_id", "=", website.id), ("website_id", "=", False),
            ], limit=1).id
        if not configuration.english_pricelist_id:
            values["english_pricelist_id"] = self.env["product.pricelist"].search([
                ("currency_id.name", "=", "USD"),
                "|", ("website_id", "=", website.id), ("website_id", "=", False),
            ], limit=1).id
        if values:
            configuration.write(values)
        return configuration


class ShopApiClient(models.Model):
    _name = "shop.api.client"
    _description = "Shop API Client"
    _order = "name"

    name = fields.Char(string="客户端名称", required=True)
    code = fields.Char(string="客户端代码", required=True, index=True)
    active = fields.Boolean(default=True)
    user_id = fields.Many2one("res.users", string="集成用户", required=True, ondelete="restrict")
    scope_ids = fields.Many2many("shop.api.scope", string="允许权限")
    company_ids = fields.Many2many("res.company", string="允许公司")
    website_ids = fields.Many2many("website", string="允许网站")
    rate_limit_per_minute = fields.Integer(string="每分钟请求上限", default=300)
    last_used_at = fields.Datetime(string="最后使用时间", readonly=True)
    last_used_ip = fields.Char(string="最后来源 IP", readonly=True)
    notes = fields.Text(string="备注")

    _unique_code = models.Constraint("UNIQUE(code)", "API 客户端代码必须唯一。")
    _unique_user = models.Constraint("UNIQUE(user_id)", "一个集成用户只能绑定一个 API 客户端。")

    @api.constrains("user_id")
    def _check_integration_user(self):
        integration_group = self.env.ref("shop_api.group_shop_api_integration", raise_if_not_found=False)
        for client in self:
            if integration_group and integration_group not in client.user_id.group_ids:
                client.user_id.write({"group_ids": [Command.link(integration_group.id)]})

    def generate_api_key(self, name=None, expiration_date=None):
        """Generate a standard Odoo bearer key. The returned secret is shown once."""
        self.ensure_one()
        if not self.env.user.has_group("shop_api.group_shop_api_admin") and not self.env.is_system():
            raise ValidationError(_("只有 Shop API 管理员可以生成密钥。"))
        expiration_date = expiration_date or (fields.Datetime.now() + datetime.timedelta(days=90))
        key_model = self.env["res.users.apikeys"].with_user(self.user_id).sudo()
        return key_model._generate(None, name or f"Shop API - {self.name}", expiration_date)

    @api.model
    def _client_for_current_user(self):
        return self.sudo().search([
            ("user_id", "=", self.env.uid),
            ("active", "=", True),
        ], limit=1)

    def allows_scope(self, scope_code):
        self.ensure_one()
        return not scope_code or scope_code in self.scope_ids.mapped("code")


class ShopApiEventType(models.Model):
    _name = "shop.api.event.type"
    _description = "Shop API Event Type"
    _order = "code"

    name = fields.Char(string="事件名称", required=True, translate=True)
    code = fields.Char(string="事件代码", required=True, index=True)
    active = fields.Boolean(default=True)
    description = fields.Text(string="说明", translate=True)

    _unique_code = models.Constraint("UNIQUE(code)", "事件代码必须唯一。")

    @api.model
    def _ensure_builtin_event_types(self):
        for code in BUILTIN_EVENT_TYPES:
            if not self.search_count([("code", "=", code)]):
                self.create({"code": code, "name": code})


class ShopApiWebhook(models.Model):
    _name = "shop.api.webhook"
    _description = "Shop API Webhook Subscription"
    _order = "name"

    name = fields.Char(string="订阅名称", required=True)
    active = fields.Boolean(default=True)
    client_id = fields.Many2one("shop.api.client", string="客户端", required=True, ondelete="cascade")
    url = fields.Char(string="Webhook 地址", required=True)
    secret = fields.Char(
        string="签名密钥", required=True, default=lambda self: secrets.token_urlsafe(48),
        groups="shop_api.group_shop_api_admin",
    )
    event_type_ids = fields.Many2many("shop.api.event.type", string="订阅事件")
    timeout_seconds = fields.Integer(string="超时秒数", default=10)
    last_success_at = fields.Datetime(string="最后成功时间", readonly=True)
    last_failure_at = fields.Datetime(string="最后失败时间", readonly=True)
    last_error = fields.Text(string="最后错误", readonly=True)

    @api.constrains("url")
    def _check_url(self):
        for webhook in self:
            parsed = urlparse(webhook.url or "")
            is_https = parsed.scheme.lower() == "https" and bool(parsed.hostname)
            is_local_test = (
                parsed.scheme.lower() == "http"
                and parsed.hostname in {"127.0.0.1", "localhost"}
            )
            if not (is_https or is_local_test):
                raise ValidationError(_("Webhook 必须使用 HTTPS；本机测试仅允许 localhost 或 127.0.0.1。"))
