from io import BytesIO
from dateutil.relativedelta import relativedelta
from urllib.parse import urlencode

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


SALE_ORDER_IMPORT_TEMPLATE_ROUTE = "/stock_subwarehouse_hierarchy/import_template/sale_order.xlsx"


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
    x_website_stock_reserved_at = fields.Datetime(
        string="Website Stock Reserved At",
        copy=False,
        readonly=True,
    )

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
        return super().action_confirm()
