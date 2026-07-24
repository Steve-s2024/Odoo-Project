from io import BytesIO
import logging
from calendar import timegm
from dateutil.relativedelta import relativedelta
from urllib.parse import urlencode

from odoo import _, api, fields, models
from odoo.addons.base.models.ir_mail_server import MailDeliveryException
from odoo.exceptions import UserError
from odoo.tools import float_compare


SALE_ORDER_IMPORT_TEMPLATE_ROUTE = "/stock_subwarehouse_hierarchy/import_template/sale_order.xlsx"
_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_platform = fields.Char(string="平台")
    x_channel = fields.Char(string="渠道")
    x_sale_nature = fields.Selection(
        [
            ("retail", "零售"),
            ("trade_in", "以旧换新"),
            ("bulk_purchase", "批量采购"),
            ("other", "其他"),
        ],
        string="性质",
    )
    x_finance_remark = fields.Char(string="备注")
    x_website_checkout_language = fields.Selection(
        [("zh_CN", "中文"), ("en_US", "English")],
        string="网站结账语言",
        copy=False,
    )
    x_website_chinese_pricelist_id = fields.Many2one(
        "product.pricelist",
        string="中文网站价目表",
        copy=False,
        readonly=True,
    )
    x_website_stock_reserved_at = fields.Datetime(
        string="Website Stock Reserved At",
        copy=False,
        readonly=True,
    )
    x_website_stock_reserved_until = fields.Datetime(
        string="网站库存保留至",
        compute="_compute_website_stock_reservation_expiry",
    )
    x_website_stock_reservation_expiry_epoch = fields.Integer(
        string="网站库存保留到期时间戳",
        compute="_compute_website_stock_reservation_expiry",
    )
    x_website_payment_ids = fields.Many2many(
        "account.payment",
        string="网站收款",
        compute="_compute_website_payment_details",
    )
    x_website_payment_count = fields.Integer(
        string="网站收款数量",
        compute="_compute_website_payment_details",
    )
    x_website_payment_state = fields.Selection(
        [
            ("unpaid", "未支付"),
            ("pending", "支付处理中"),
            ("paid", "已支付"),
            ("error", "支付失败"),
        ],
        string="网站支付状态",
        compute="_compute_website_payment_details",
    )
    x_website_payment_reference = fields.Char(
        string="支付交易号",
        compute="_compute_website_payment_details",
    )
    x_website_refund_request_ids = fields.One2many(
        "stock.subwarehouse.website.refund.request", "order_id", string="网站退款申请"
    )
    x_website_refund_request_count = fields.Integer(
        compute="_compute_website_refund_request_count", string="退款申请数量"
    )

    @api.depends("x_website_refund_request_ids")
    def _compute_website_refund_request_count(self):
        for order in self:
            order.x_website_refund_request_count = len(order.x_website_refund_request_ids)

    @api.depends("order_line.x_website_stock_reserved_until")
    def _compute_website_stock_reservation_expiry(self):
        for order in self:
            expiries = [
                expiry
                for expiry in order.order_line.mapped("x_website_stock_reserved_until")
                if expiry
            ]
            expiry = max(expiries) if expiries else False
            order.x_website_stock_reserved_until = expiry
            order.x_website_stock_reservation_expiry_epoch = (
                timegm(expiry.timetuple()) * 1000 if expiry else 0
            )

    @api.depends(
        "transaction_ids.state",
        "transaction_ids.provider_reference",
        "transaction_ids.payment_id",
    )
    def _compute_website_payment_details(self):
        for order in self:
            payments = order.transaction_ids.mapped("payment_id")
            latest_tx = order.transaction_ids.sorted("id")[-1:]
            order.x_website_payment_ids = payments
            order.x_website_payment_count = len(payments)
            order.x_website_payment_reference = (
                latest_tx.provider_reference or latest_tx.reference
            ) if latest_tx else False
            if order.transaction_ids.filtered(lambda tx: tx.state == "done"):
                order.x_website_payment_state = "paid"
            elif order.transaction_ids.filtered(lambda tx: tx.state in ("draft", "pending", "authorized")):
                order.x_website_payment_state = "pending"
            elif order.transaction_ids:
                order.x_website_payment_state = "error"
            else:
                order.x_website_payment_state = "unpaid"

    def _get_website_payment_receipt(self):
        self.ensure_one()
        return self.transaction_ids.filtered(
            lambda tx: tx.state == "done" and tx.payment_id
        ).sorted("id")[-1:].payment_id

    @api.model
    def _is_website_checkout_country_allowed(self, country, is_english):
        chinese_region_codes = {"CN", "HK", "MO", "TW"}
        country_code = (country.code or "").upper()
        return bool(country_code) and (
            country_code not in chinese_region_codes if is_english else country_code in chinese_region_codes
        )

    @api.model
    def _website_checkout_country_message(self, is_english):
        return (
            _("Please switch the website language to Chinese; this delivery country or region is not supported.")
            if is_english
            else _("请更换网站语言至英文，否则不支持此收货国家/地域。")
        )

    def _get_website_usd_pricelist(self):
        self.ensure_one()
        usd = self.env.ref("base.USD")
        if usd.symbol != "$" or usd.position != "before" or not usd.active:
            usd.write({"symbol": "$", "position": "before", "active": True})
        pricelist = self.env["product.pricelist"].sudo().search([
            ("website_id", "=", self.website_id.id),
            ("name", "=", "SUN Website USD Checkout"),
        ], limit=1)
        if not pricelist:
            pricelist = self.env["product.pricelist"].sudo().create({
                "name": "SUN Website USD Checkout",
                "currency_id": usd.id,
                "company_id": self.company_id.id,
                "website_id": self.website_id.id,
                "selectable": False,
            })
        elif pricelist.currency_id != usd or pricelist.selectable:
            pricelist.write({"currency_id": usd.id, "selectable": False})
        return pricelist

    def _apply_website_checkout_language(self, is_english):
        for order in self.filtered("website_id"):
            target_language = "en_US" if is_english else "zh_CN"
            if is_english:
                missing_mappings = order.order_line.filtered(
                    lambda line: (
                        not line.display_type
                        and not line.is_delivery
                        and line.product_id
                        and not line.product_id.product_tmpl_id.x_website_code_mapping_id
                    )
                )
                if missing_mappings:
                    raise UserError(_(
                        "以下产品没有英文网站编号价格规则，无法使用英文结账：%s"
                    ) % ", ".join(missing_mappings.mapped("product_id.display_name")))
                if not order.x_website_chinese_pricelist_id:
                    order.x_website_chinese_pricelist_id = order.pricelist_id
                usd_pricelist = order._get_website_usd_pricelist()
                if order.pricelist_id != usd_pricelist:
                    order.pricelist_id = usd_pricelist
                for line in order.order_line.filtered(
                    lambda line: not line.display_type and not line.is_delivery and line.product_id
                ):
                    template = line.product_id.product_tmpl_id
                    line.write({
                        "name": template._get_website_display_name(True),
                        "price_unit": template.x_website_usd_price,
                    })
            else:
                chinese_pricelist = order.x_website_chinese_pricelist_id
                if chinese_pricelist and order.pricelist_id != chinese_pricelist:
                    order.pricelist_id = chinese_pricelist
                # The price recomputation hook checks this flag to decide whether
                # it must enforce USD mapping values.
                order.x_website_checkout_language = target_language
                order._recompute_prices()
                for line in order.order_line.filtered(
                    lambda line: not line.display_type and not line.is_delivery and line.product_id
                ):
                    line.name = line.product_id.with_context(lang="zh_CN").get_product_multiline_description_sale()
            order.x_website_checkout_language = target_language

    def _recompute_prices(self):
        """Keep code-mapped USD prices after Odoo refreshes a website cart."""
        super()._recompute_prices()
        for order in self.filtered(
            lambda order: order.website_id and order.x_website_checkout_language == "en_US"
        ):
            for line in order.order_line.filtered(
                lambda line: not line.display_type and not line.is_delivery and line.product_id
            ):
                template = line.product_id.product_tmpl_id
                line.write({
                    "name": template._get_website_display_name(True),
                    "price_unit": template.x_website_usd_price,
                })

    def action_view_website_payments(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("account.action_account_payments")
        payments = self.x_website_payment_ids
        if len(payments) == 1:
            action.update({
                "view_mode": "form",
                "res_id": payments.id,
                "views": [(False, "form")],
            })
        else:
            action["domain"] = [("id", "in", payments.ids)]
        return action

    def action_view_website_refund_requests(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "stock_subwarehouse_hierarchy.action_website_refund_requests"
        )
        action["domain"] = [("order_id", "=", self.id)]
        return action

    def action_refund_website_payment(self):
        self.ensure_one()
        payment = self._get_website_payment_receipt()
        if not payment:
            raise UserError(_("该网站订单没有可退款的已完成支付。"))
        transaction = payment.payment_transaction_id
        if transaction.provider_code != "wechatpay":
            raise UserError(_("该网站订单的支付方式暂不支持从此处退款。"))
        return {
            "type": "ir.actions.act_window",
            "name": _("微信退款"),
            "res_model": "stock.subwarehouse.website.payment.refund.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_order_id": self.id,
                "default_transaction_id": transaction.id,
            },
        }

    @api.model
    def get_import_templates(self):
        return [{
            "label": _("报价单导入模板（产品ID）"),
            "template": SALE_ORDER_IMPORT_TEMPLATE_ROUTE,
        }]

    def _get_sale_order_import_template_columns(self):
        return [
            ("Order Reference", "订单号"),
            ("Customer*", "客户"),
            ("Order Date", "下单时间"),
            ("x_platform", "平台"),
            ("x_channel", "渠道"),
            ("Salesperson", "销售人员"),
            ("x_sale_nature", "性质"),
            ("Order Lines/Products*", "产品ID"),
            ("Order Lines/x_import_product_name", "品名"),
            ("Order Lines/x_color", "颜色"),
            ("Order Lines/x_size", "尺码"),
            ("Order Lines/x_flex", "款型"),
            ("Order Lines/Quantity", "数量"),
            ("Order Lines/Unit Price", "单价"),
            ("Order Lines/x_source_location_id", "发货仓库"),
            ("x_finance_remark", "备注"),
        ]

    def _generate_sale_order_import_template_xlsx(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as error:
            raise ImportError(_("生成导入模板需要安装 openpyxl。")) from error

        workbook = Workbook()
        import_sheet = workbook.active
        import_sheet.title = "报价单导入"
        columns = self._get_sale_order_import_template_columns()
        import_sheet.append([field_name for field_name, _label in columns])
        import_sheet.append([label for _field_name, label in columns])
        import_sheet.append([
            "S00001",
            self.env.user.partner_id.display_name,
            fields.Date.today().strftime("%Y-%m-%d"),
            "有赞",
            "凌动雪具",
            self.env.user.display_name,
            "零售",
            "152410Yb-MK000-H001150",
            "双板鞋",
            "黑色",
            "260",
            "硬度100",
            10,
            111,
            "张家口/Stock",
            "",
        ])
        import_sheet.append([
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "072409Y-MA000-G001##S",
            "滑雪服",
            "绿色",
            "S",
            "",
            1,
            4000,
            "",
            "",
        ])

        field_sheet = workbook.create_sheet("导入字段")
        field_sheet.append(["字段", "中文说明"])
        for field_name, label in columns:
            field_sheet.append([field_name, label])

        for sheet in workbook.worksheets:
            sheet.freeze_panes = "A3"
            for cell in sheet[1]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="D9EAF7")
            if sheet.max_row >= 2:
                for cell in sheet[2]:
                    cell.font = Font(italic=True)
                    cell.fill = PatternFill("solid", fgColor="E2F0D9")
            for column_cells in sheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(max_length + 2, 12), 55)

        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def action_export_import_template_format(self):
        ids = ",".join(str(record_id) for record_id in self.ids)
        return {
            "type": "ir.actions.act_url",
            "url": f"/stock_subwarehouse_hierarchy/export/sale_order.xlsx?{urlencode({'ids': ids})}",
            "target": "self",
        }

    def _generate_sale_order_export_xlsx(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as error:
            raise ImportError(_("生成导出文件需要安装 openpyxl。")) from error

        workbook = Workbook()
        export_sheet = workbook.active
        export_sheet.title = "报价单导出"
        columns = self._get_sale_order_import_template_columns()
        export_sheet.append([field_name for field_name, _label in columns])
        export_sheet.append([label for _field_name, label in columns])

        for order in self:
            order_lines = order.order_line.filtered(lambda line: not line.display_type)
            if not order_lines:
                export_sheet.append(self._sale_order_export_row(order, self.env["sale.order.line"], include_order=True))
                continue
            for index, line in enumerate(order_lines):
                export_sheet.append(self._sale_order_export_row(order, line, include_order=index == 0))

        self._format_sale_order_export_workbook(workbook)
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def _sale_order_export_row(self, order, line, include_order=True):
        product = line.product_id if line else self.env["product.product"]
        return [
            order.name if include_order else "",
            order.partner_id.display_name if include_order else "",
            order.date_order.strftime("%Y-%m-%d") if include_order and order.date_order else "",
            order.x_platform if include_order else "",
            order.x_channel if include_order else "",
            order.user_id.display_name if include_order and order.user_id else "",
            dict(order._fields["x_sale_nature"].selection).get(order.x_sale_nature, "") if include_order else "",
            product.default_code or product.display_name or "",
            line.x_import_product_name if line else "",
            line.x_color if line else "",
            line.x_size if line else "",
            line.x_flex if line else "",
            line.product_uom_qty if line else "",
            line.price_unit if line else "",
            line.x_source_location_id.display_name if line and line.x_source_location_id else "",
            order.x_finance_remark if include_order else "",
        ]

    def _format_sale_order_export_workbook(self, workbook):
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        for sheet in workbook.worksheets:
            sheet.freeze_panes = "A3"
            for cell in sheet[1]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="D9EAF7")
            if sheet.max_row >= 2:
                for cell in sheet[2]:
                    cell.font = Font(italic=True)
                    cell.fill = PatternFill("solid", fgColor="E2F0D9")
            for column_cells in sheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(max_length + 2, 12), 55)

    def _check_source_inventory_availability(self):
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        demands = {}
        for line in self.order_line:
            if (
                line.display_type
                or not line.is_storable
                or not line.x_source_location_id
            ):
                continue
            key = (line.product_id, line.x_source_location_id)
            demands.setdefault(key, {
                "line": line,
                "requested": 0.0,
            })
            demands[key]["requested"] += line.product_uom_id._compute_quantity(
                line.product_uom_qty,
                line.product_id.uom_id,
            )

        shortages = []
        for line in self.order_line:
            if (
                line.display_type
                or not line.is_storable
                or float_compare(line.product_uom_qty, 0.0, precision_digits=precision) <= 0
                or line.x_source_location_id
            ):
                continue
            shortages.append(_(
                "%(product)s：请选择有足够现货的来源库存。",
                product=line.product_id.display_name,
            ))
        for (product, location), demand in demands.items():
            line = demand["line"]
            available = self.env["stock.quant"]._get_available_quantity(product, location, strict=True)
            if float_compare(
                demand["requested"],
                available,
                precision_digits=precision,
            ) > 0:
                shortages.append(_(
                    "%(product)s 来自 %(location)s：需要 %(requested)s %(uom)s，可用 %(available)s %(uom)s",
                    product=product.display_name,
                    location=location.display_name,
                    requested=demand["requested"],
                    available=available,
                    uom=product.uom_id.display_name,
                ))
        if shortages:
            raise UserError(_("所选来源库存无法满足此报价单：\n%s") % "\n".join(shortages))

    def _get_website_reserved_qty_for_source_location(self, product, location, exclude_order=False):
        now = fields.Datetime.now()
        domain = [
            ("product_id", "=", product.id),
            ("x_source_location_id", "=", location.id),
            ("x_website_stock_reserved_until", ">", now),
            ("order_id.state", "in", ["draft", "sent"]),
        ]
        if exclude_order:
            domain.append(("order_id", "!=", exclude_order.id))
        reserved_qty = 0.0
        reserved_lines = self.env["sale.order.line"].sudo().search(domain)
        for line in reserved_lines:
            reserved_qty += line.product_uom_id._compute_quantity(
                line.product_uom_qty,
                product.uom_id,
            )
        return reserved_qty

    def _get_available_qty_for_source_location(self, product, location, exclude_order=False):
        physical_qty = self.env["stock.quant"].sudo()._get_available_quantity(
            product,
            location,
            strict=True,
        )
        reserved_qty = self._get_website_reserved_qty_for_source_location(
            product,
            location,
            exclude_order=exclude_order,
        )
        return max(physical_qty - reserved_qty, 0.0)

    def _auto_assign_website_source_locations(self):
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        StockLocation = self.env["stock.location"]
        for order in self:
            planned_demands = {}
            lines = order.order_line.filtered(
                lambda line: (
                    not line.display_type
                    and line.product_id
                    and line.is_storable
                    and float_compare(line.product_uom_qty, 0.0, precision_digits=precision) > 0
                )
            )
            for line in lines:
                product = line.product_id
                required_qty = line._get_required_qty_in_product_uom()
                current_location = line.x_source_location_id
                if current_location:
                    planned_key = (product.id, current_location.id)
                    available_qty = order._get_available_qty_for_source_location(
                        product,
                        current_location,
                        exclude_order=order,
                    ) - planned_demands.get(planned_key, 0.0)
                    if float_compare(available_qty, required_qty, precision_digits=precision) >= 0:
                        planned_demands[planned_key] = planned_demands.get(planned_key, 0.0) + required_qty
                        continue

                best_location = StockLocation
                best_available_qty = False
                for location in line._get_source_location_candidates():
                    planned_key = (product.id, location.id)
                    available_qty = order._get_available_qty_for_source_location(
                        product,
                        location,
                        exclude_order=order,
                    ) - planned_demands.get(planned_key, 0.0)
                    if float_compare(available_qty, required_qty, precision_digits=precision) < 0:
                        continue
                    if best_available_qty is False or available_qty < best_available_qty:
                        best_location = location
                        best_available_qty = available_qty

                line.x_source_location_id = best_location
                if best_location:
                    planned_key = (product.id, best_location.id)
                    planned_demands[planned_key] = planned_demands.get(planned_key, 0.0) + required_qty

    def _get_source_inventory_shortages(self):
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        demands = {}
        shortages = []
        for line in self.order_line:
            if (
                line.display_type
                or not line.is_storable
                or float_compare(line.product_uom_qty, 0.0, precision_digits=precision) <= 0
            ):
                continue
            if not line.x_source_location_id:
                shortages.append(_("%(product)s：没有可满足数量的发货仓库。", product=line.product_id.display_name))
                continue
            key = (line.product_id, line.x_source_location_id)
            demands.setdefault(key, {
                "line": line,
                "requested": 0.0,
            })
            demands[key]["requested"] += line.product_uom_id._compute_quantity(
                line.product_uom_qty,
                line.product_id.uom_id,
            )

        for (product, location), demand in demands.items():
            available = self._get_available_qty_for_source_location(
                product,
                location,
                exclude_order=self,
            )
            if float_compare(demand["requested"], available, precision_digits=precision) > 0:
                shortages.append(_(
                    "%(product)s 来自 %(location)s：需要 %(requested)s %(uom)s，可用 %(available)s %(uom)s",
                    product=product.display_name,
                    location=location.display_name,
                    requested=demand["requested"],
                    available=available,
                    uom=product.uom_id.display_name,
                ))
        return shortages

    def _check_source_inventory_availability(self):
        shortages = self._get_source_inventory_shortages()
        if shortages:
            raise UserError(_("所选来源库存无法满足此报价单：\n%s") % "\n".join(shortages))

    def _prepare_website_stock_for_payment(self, hold_minutes=15):
        self._auto_assign_website_source_locations()
        self._check_source_inventory_availability()
        reserved_until = fields.Datetime.now() + relativedelta(minutes=hold_minutes)
        for order in self:
            reservable_lines = order.order_line.filtered(
                lambda line: (
                    not line.display_type
                    and line.product_id
                    and line.is_storable
                    and line.x_source_location_id
                    and line.product_uom_qty > 0
                )
            )
            reservable_lines.write({"x_website_stock_reserved_until": reserved_until})
            order.x_website_stock_reserved_at = fields.Datetime.now()

    def action_confirm(self):
        self._check_source_inventory_availability()
        website_orders = self.filtered(
            lambda order: order.website_id and self.env.context.get("send_email")
        )
        regular_orders = self - website_orders
        result = True
        if regular_orders:
            result = super(SaleOrder, regular_orders).action_confirm()
        if website_orders:
            result = super(SaleOrder, website_orders.with_context(send_email=False)).action_confirm()
            try:
                with self.env.cr.savepoint():
                    website_orders._send_order_confirmation_mail()
            except (UserError, MailDeliveryException):
                _logger.exception(
                    "Website orders %s were confirmed after payment, but their confirmation email failed.",
                    website_orders.mapped("name"),
                )
        return result


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    @api.depends("product_id.display_name", "order_id.x_website_checkout_language")
    def _compute_name_short(self):
        super()._compute_name_short()
        for line in self.filtered(
            lambda line: (
                line.order_id.website_id
                and line.order_id.x_website_checkout_language == "en_US"
                and line.product_id
                and not line.is_delivery
            )
        ):
            line.name_short = line.product_id.product_tmpl_id._get_website_display_name(True)

    def _get_line_header(self):
        self.ensure_one()
        if (
            self.order_id.website_id
            and self.order_id.x_website_checkout_language == "en_US"
            and self.product_id
            and not self.is_delivery
        ):
            return self.product_id.product_tmpl_id._get_website_display_name(True)
        return super()._get_line_header()
