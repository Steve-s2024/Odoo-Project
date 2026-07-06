from io import BytesIO
from urllib.parse import urlencode

from odoo import _, api, fields, models


IMPORT_TEMPLATE_ROUTE = "/stock_subwarehouse_hierarchy/import_template/mrp_production.xlsx"


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    def _get_subwarehouse_manufacturing_location(self):
        location_id = self.env.context.get("subwarehouse_manufacturing_location_id")
        if not location_id:
            return self.env["stock.location"]
        location = self.env["stock.location"].browse(location_id).exists()
        return location if location.usage == "internal" else self.env["stock.location"]

    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        location = self._get_subwarehouse_manufacturing_location()
        if location:
            if "location_src_id" in fields_list:
                defaults["location_src_id"] = location.id
            if "location_dest_id" in fields_list:
                defaults["location_dest_id"] = location.id
        return defaults

    @api.depends("picking_type_id")
    def _compute_locations(self):
        super()._compute_locations()
        location = self._get_subwarehouse_manufacturing_location()
        if location:
            for production in self:
                production.location_src_id = location
                production.location_dest_id = location

    @api.model_create_multi
    def create(self, vals_list):
        location = self._get_subwarehouse_manufacturing_location()
        for vals in vals_list:
            if vals.get("product_id") and not vals.get("bom_id"):
                product = self.env["product.product"].browse(vals["product_id"])
                picking_type = (
                    vals.get("picking_type_id")
                    and self.env["stock.picking.type"].browse(vals["picking_type_id"])
                )
                bom = self.env["mrp.bom"].with_context(active_test=True)._bom_find(
                    product,
                    picking_type=picking_type,
                    company_id=vals.get("company_id") or self.env.company.id,
                    bom_type="normal",
                )[product]
                if bom:
                    vals["bom_id"] = bom.id
            if location:
                vals.setdefault("location_src_id", location.id)
                vals.setdefault("location_dest_id", location.id)
        return super().create(vals_list)

    @api.model
    def get_import_templates(self):
        return [{
            "label": _("制造单导入模板（当前产品属性）"),
            "template": IMPORT_TEMPLATE_ROUTE,
        }]

    @api.model
    def _get_dynamic_import_template_columns(self):
        return [
            ("product_id", _("产品")),
            ("product_qty", _("数量")),
            ("product_uom_id", _("计量单位")),
            ("bom_id", _("物料清单")),
            ("origin", _("源单据")),
            ("date_start", _("计划日期")),
            ("location_src_id", _("组件库位")),
            ("location_dest_id", _("成品库位")),
            ("picking_type_id", _("作业类型")),
            ("company_id", _("公司")),
            ("never_product_template_attribute_value_ids", _("产品属性值")),
        ]

    @api.model
    def _generate_dynamic_import_template_xlsx(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as error:
            raise ImportError(_("生成导入模板需要安装 openpyxl。")) from error

        workbook = Workbook()
        import_sheet = workbook.active
        import_sheet.title = "制造单导入"
        field_columns = self._get_dynamic_import_template_columns()
        headers = [field_name for field_name, _label in field_columns]
        import_sheet.append(headers)
        import_sheet.append([
            "产品显示名称或外部 ID",
            1,
            "件",
            "",
            "示例制造单",
            fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "WH/Stock",
            "WH/Stock",
            "制造",
            self.env.company.display_name,
            "如需要，使用逗号分隔产品属性值",
        ])

        attribute_sheet = workbook.create_sheet("产品属性")
        attribute_sheet.append(["属性ID", "属性", "变体创建方式", "值ID", "值"])
        for attribute in self.env["product.attribute"].search([], order="sequence, id"):
            if attribute.value_ids:
                for value in attribute.value_ids.sorted(lambda record: (record.sequence, record.id)):
                    attribute_sheet.append([
                        attribute.id,
                        attribute.display_name,
                        attribute.create_variant,
                        value.id,
                        value.display_name,
                    ])
            else:
                attribute_sheet.append([
                    attribute.id,
                    attribute.display_name,
                    attribute.create_variant,
                    "",
                    "",
                ])

        field_sheet = workbook.create_sheet("导入字段")
        field_sheet.append(["字段", "标签", "类型", "关联模型"])
        fields_get = self.fields_get([field_name for field_name, _label in field_columns])
        for field_name, label in field_columns:
            metadata = fields_get.get(field_name, {})
            field_sheet.append([
                field_name,
                metadata.get("string") or label,
                metadata.get("type", ""),
                metadata.get("relation", ""),
            ])

        for sheet in workbook.worksheets:
            sheet.freeze_panes = "A2"
            for cell in sheet[1]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="D9EAF7")
            for column_cells in sheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(max_length + 2, 12), 45)

        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def action_export_import_template_format(self):
        ids = ",".join(str(record_id) for record_id in self.ids)
        return {
            "type": "ir.actions.act_url",
            "url": f"/stock_subwarehouse_hierarchy/export/mrp_production.xlsx?{urlencode({'ids': ids})}",
            "target": "self",
        }

    def _generate_dynamic_export_xlsx(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as error:
            raise ImportError(_("生成导出文件需要安装 openpyxl。")) from error

        workbook = Workbook()
        export_sheet = workbook.active
        export_sheet.title = "制造单导出"
        field_columns = self._get_dynamic_import_template_columns()
        export_sheet.append([field_name for field_name, _label in field_columns])
        export_sheet.append([label for _field_name, label in field_columns])
        for production in self:
            export_sheet.append(production._get_dynamic_export_row(field_columns))

        attribute_sheet = workbook.create_sheet("产品属性")
        attribute_sheet.append(["属性ID", "属性", "变体创建方式", "值ID", "值"])
        for attribute in self.env["product.attribute"].search([], order="sequence, id"):
            if attribute.value_ids:
                for value in attribute.value_ids.sorted(lambda record: (record.sequence, record.id)):
                    attribute_sheet.append([
                        attribute.id,
                        attribute.display_name,
                        attribute.create_variant,
                        value.id,
                        value.display_name,
                    ])
            else:
                attribute_sheet.append([
                    attribute.id,
                    attribute.display_name,
                    attribute.create_variant,
                    "",
                    "",
                ])

        field_sheet = workbook.create_sheet("导出字段")
        field_sheet.append(["字段", "标签", "类型", "关联模型"])
        fields_get = self.fields_get([field_name for field_name, _label in field_columns])
        for field_name, label in field_columns:
            metadata = fields_get.get(field_name, {})
            field_sheet.append([
                field_name,
                metadata.get("string") or label,
                metadata.get("type", ""),
                metadata.get("relation", ""),
            ])

        for sheet in workbook.worksheets:
            sheet.freeze_panes = "A3" if sheet == export_sheet else "A2"
            for cell in sheet[1]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="D9EAF7")
            if sheet == export_sheet and sheet.max_row >= 2:
                for cell in sheet[2]:
                    cell.font = Font(italic=True)
                    cell.fill = PatternFill("solid", fgColor="E2F0D9")
            for column_cells in sheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(max_length + 2, 12), 45)

        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def _get_dynamic_export_row(self, field_columns):
        return [self._get_mrp_export_value(field_name) for field_name, _label in field_columns]

    def _get_mrp_export_value(self, field_name):
        if field_name == "never_product_template_attribute_value_ids":
            return ", ".join(self.never_product_template_attribute_value_ids.mapped("display_name"))
        if field_name not in self._fields:
            return ""
        value = self[field_name]
        field = self._fields[field_name]
        if field.type == "many2one":
            return value.display_name if value else ""
        if field.type in ("many2many", "one2many"):
            return ", ".join(value.mapped("display_name"))
        if field.type == "datetime" and value:
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if field.type == "date" and value:
            return value.strftime("%Y-%m-%d")
        return value
