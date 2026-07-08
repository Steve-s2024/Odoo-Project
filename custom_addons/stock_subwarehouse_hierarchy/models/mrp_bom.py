from io import BytesIO
from urllib.parse import urlencode

from odoo import _, api, fields, models
from odoo.fields import Command


BOM_IMPORT_TEMPLATE_ROUTE = "/stock_subwarehouse_hierarchy/import_template/mrp_bom.xlsx"
BOM_IMPORT_COMPONENT_SLOT_COUNT = 20


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    x_import_bom_component_product_1 = fields.Char(string="导入组件产品 1", copy=False)
    x_import_bom_component_qty_1 = fields.Float(string="导入组件数量 1", copy=False)
    x_import_bom_component_uom_1 = fields.Char(string="导入组件单位 1", copy=False)
    x_import_bom_component_product_2 = fields.Char(string="导入组件产品 2", copy=False)
    x_import_bom_component_qty_2 = fields.Float(string="导入组件数量 2", copy=False)
    x_import_bom_component_uom_2 = fields.Char(string="导入组件单位 2", copy=False)
    x_import_bom_component_product_3 = fields.Char(string="导入组件产品 3", copy=False)
    x_import_bom_component_qty_3 = fields.Float(string="导入组件数量 3", copy=False)
    x_import_bom_component_uom_3 = fields.Char(string="导入组件单位 3", copy=False)
    x_import_bom_component_product_4 = fields.Char(string="导入组件产品 4", copy=False)
    x_import_bom_component_qty_4 = fields.Float(string="导入组件数量 4", copy=False)
    x_import_bom_component_uom_4 = fields.Char(string="导入组件单位 4", copy=False)
    x_import_bom_component_product_5 = fields.Char(string="导入组件产品 5", copy=False)
    x_import_bom_component_qty_5 = fields.Float(string="导入组件数量 5", copy=False)
    x_import_bom_component_uom_5 = fields.Char(string="导入组件单位 5", copy=False)
    x_import_bom_component_product_6 = fields.Char(string="导入组件产品 6", copy=False)
    x_import_bom_component_qty_6 = fields.Float(string="导入组件数量 6", copy=False)
    x_import_bom_component_uom_6 = fields.Char(string="导入组件单位 6", copy=False)
    x_import_bom_component_product_7 = fields.Char(string="导入组件产品 7", copy=False)
    x_import_bom_component_qty_7 = fields.Float(string="导入组件数量 7", copy=False)
    x_import_bom_component_uom_7 = fields.Char(string="导入组件单位 7", copy=False)
    x_import_bom_component_product_8 = fields.Char(string="导入组件产品 8", copy=False)
    x_import_bom_component_qty_8 = fields.Float(string="导入组件数量 8", copy=False)
    x_import_bom_component_uom_8 = fields.Char(string="导入组件单位 8", copy=False)
    x_import_bom_component_product_9 = fields.Char(string="导入组件产品 9", copy=False)
    x_import_bom_component_qty_9 = fields.Float(string="导入组件数量 9", copy=False)
    x_import_bom_component_uom_9 = fields.Char(string="导入组件单位 9", copy=False)
    x_import_bom_component_product_10 = fields.Char(string="导入组件产品 10", copy=False)
    x_import_bom_component_qty_10 = fields.Float(string="导入组件数量 10", copy=False)
    x_import_bom_component_uom_10 = fields.Char(string="导入组件单位 10", copy=False)
    x_import_bom_component_product_11 = fields.Char(string="导入组件产品 11", copy=False)
    x_import_bom_component_qty_11 = fields.Float(string="导入组件数量 11", copy=False)
    x_import_bom_component_uom_11 = fields.Char(string="导入组件单位 11", copy=False)
    x_import_bom_component_product_12 = fields.Char(string="导入组件产品 12", copy=False)
    x_import_bom_component_qty_12 = fields.Float(string="导入组件数量 12", copy=False)
    x_import_bom_component_uom_12 = fields.Char(string="导入组件单位 12", copy=False)
    x_import_bom_component_product_13 = fields.Char(string="导入组件产品 13", copy=False)
    x_import_bom_component_qty_13 = fields.Float(string="导入组件数量 13", copy=False)
    x_import_bom_component_uom_13 = fields.Char(string="导入组件单位 13", copy=False)
    x_import_bom_component_product_14 = fields.Char(string="导入组件产品 14", copy=False)
    x_import_bom_component_qty_14 = fields.Float(string="导入组件数量 14", copy=False)
    x_import_bom_component_uom_14 = fields.Char(string="导入组件单位 14", copy=False)
    x_import_bom_component_product_15 = fields.Char(string="导入组件产品 15", copy=False)
    x_import_bom_component_qty_15 = fields.Float(string="导入组件数量 15", copy=False)
    x_import_bom_component_uom_15 = fields.Char(string="导入组件单位 15", copy=False)
    x_import_bom_component_product_16 = fields.Char(string="导入组件产品 16", copy=False)
    x_import_bom_component_qty_16 = fields.Float(string="导入组件数量 16", copy=False)
    x_import_bom_component_uom_16 = fields.Char(string="导入组件单位 16", copy=False)
    x_import_bom_component_product_17 = fields.Char(string="导入组件产品 17", copy=False)
    x_import_bom_component_qty_17 = fields.Float(string="导入组件数量 17", copy=False)
    x_import_bom_component_uom_17 = fields.Char(string="导入组件单位 17", copy=False)
    x_import_bom_component_product_18 = fields.Char(string="导入组件产品 18", copy=False)
    x_import_bom_component_qty_18 = fields.Float(string="导入组件数量 18", copy=False)
    x_import_bom_component_uom_18 = fields.Char(string="导入组件单位 18", copy=False)
    x_import_bom_component_product_19 = fields.Char(string="导入组件产品 19", copy=False)
    x_import_bom_component_qty_19 = fields.Float(string="导入组件数量 19", copy=False)
    x_import_bom_component_uom_19 = fields.Char(string="导入组件单位 19", copy=False)
    x_import_bom_component_product_20 = fields.Char(string="导入组件产品 20", copy=False)
    x_import_bom_component_qty_20 = fields.Float(string="导入组件数量 20", copy=False)
    x_import_bom_component_uom_20 = fields.Char(string="导入组件单位 20", copy=False)

    @api.model
    def get_import_templates(self):
        return [{
            "label": _("物料清单导入模板"),
            "template": BOM_IMPORT_TEMPLATE_ROUTE,
        }]

    @staticmethod
    def _get_bom_import_component_field_names():
        field_names = set()
        for slot_number in range(1, BOM_IMPORT_COMPONENT_SLOT_COUNT + 1):
            field_names.update({
                f"x_import_bom_component_product_{slot_number}",
                f"x_import_bom_component_qty_{slot_number}",
                f"x_import_bom_component_uom_{slot_number}",
            })
        return field_names

    def load(self, import_fields, import_data):
        component_rows = self._extract_bom_component_import_rows(import_fields, import_data)
        component_field_names = self._get_bom_import_component_field_names()
        kept_indexes = [
            index
            for index, field_name in enumerate(import_fields)
            if field_name not in component_field_names
        ]
        cleaned_fields = [import_fields[index] for index in kept_indexes]
        cleaned_data = [[row[index] for index in kept_indexes] for row in import_data]

        result = super().load(cleaned_fields, cleaned_data)
        if result.get("ids") and component_rows:
            boms = self.browse(result["ids"])
            for bom, component_values in zip(boms, component_rows):
                bom._write_imported_bom_components(component_values)
        return result

    def _extract_bom_component_import_rows(self, import_fields, import_data):
        if not any(field_name in import_fields for field_name in self._get_bom_import_component_field_names()):
            return []

        rows = []
        for row in import_data:
            components = []
            for slot_number in range(1, BOM_IMPORT_COMPONENT_SLOT_COUNT + 1):
                product_field = f"x_import_bom_component_product_{slot_number}"
                qty_field = f"x_import_bom_component_qty_{slot_number}"
                uom_field = f"x_import_bom_component_uom_{slot_number}"
                if product_field not in import_fields:
                    continue
                product_ref = str(row[import_fields.index(product_field)] or "").strip()
                if not product_ref:
                    continue
                quantity = 1.0
                if qty_field in import_fields:
                    raw_quantity = row[import_fields.index(qty_field)]
                    quantity = float(raw_quantity or 0.0) or 1.0
                uom_ref = ""
                if uom_field in import_fields:
                    uom_ref = str(row[import_fields.index(uom_field)] or "").strip()
                components.append((product_ref, quantity, uom_ref))
            rows.append(components)
        return rows

    def _write_imported_bom_components(self, component_values):
        commands = [Command.clear()]
        for product_ref, quantity, uom_ref in component_values:
            product = self._find_bom_product(product_ref)
            if not product:
                continue
            uom = self._find_bom_uom(uom_ref) or product.uom_id
            commands.append(Command.create({
                "product_id": product.id,
                "product_qty": quantity,
                "product_uom_id": uom.id,
            }))
        self.write({"bom_line_ids": commands})

    def _find_bom_product(self, product_ref):
        Product = self.env["product.product"]
        product = Product.search([("default_code", "=", product_ref)], limit=1)
        if product:
            return product
        matches = Product.name_search(name=product_ref, operator="=", limit=1)
        return Product.browse(matches[0][0]) if matches else Product

    def _find_bom_uom(self, uom_ref):
        if not uom_ref:
            return self.env["uom.uom"]
        Uom = self.env["uom.uom"]
        uom = Uom.search([("name", "=", uom_ref)], limit=1)
        if uom:
            return uom
        matches = Uom.name_search(name=uom_ref, operator="=", limit=1)
        return Uom.browse(matches[0][0]) if matches else Uom

    def _get_bom_import_template_columns(self):
        columns = [
            ("product_tmpl_id", "成品模板"),
            ("product_id", "成品变体"),
            ("product_qty", "成品数量"),
            ("product_uom_id", "成品单位"),
            ("type", "清单类型"),
            ("code", "物料清单编号"),
            ("company_id", "公司"),
        ]
        for slot_number in range(1, BOM_IMPORT_COMPONENT_SLOT_COUNT + 1):
            columns += [
                (f"x_import_bom_component_product_{slot_number}", f"组件产品 {slot_number}"),
                (f"x_import_bom_component_qty_{slot_number}", f"组件数量 {slot_number}"),
                (f"x_import_bom_component_uom_{slot_number}", f"组件单位 {slot_number}"),
            ]
        return columns

    def _generate_bom_import_template_xlsx(self):
        workbook = self._create_bom_workbook(include_records=False)
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def action_export_import_template_format(self):
        ids = ",".join(str(record_id) for record_id in self.ids)
        return {
            "type": "ir.actions.act_url",
            "url": f"/stock_subwarehouse_hierarchy/export/mrp_bom.xlsx?{urlencode({'ids': ids})}",
            "target": "self",
        }

    def _generate_bom_export_xlsx(self):
        workbook = self._create_bom_workbook(include_records=True)
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def _create_bom_workbook(self, include_records=False):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as error:
            raise ImportError(_("生成模板需要安装 openpyxl。")) from error

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "物料清单导入" if not include_records else "物料清单导出"
        columns = self._get_bom_import_template_columns()
        sheet.append([field_name for field_name, _label in columns])
        sheet.append([label for _field_name, label in columns])

        if include_records:
            for bom in self:
                sheet.append(bom._get_bom_export_row(columns))
        else:
            sheet.append([
                "示例成品",
                "",
                1,
                "件",
                "normal",
                "BOM-EXAMPLE-001",
                self.env.company.display_name,
                "COMPONENT-001",
                2,
                "件",
            ])

        product_sheet = workbook.create_sheet("产品列表")
        product_sheet.append(["产品ID", "产品名称", "内部编号", "物料类型", "单位"])
        for product in self.env["product.product"].search([], order="default_code, name, id"):
            product_sheet.append([
                product.id,
                product.display_name,
                product.default_code or "",
                product.product_tmpl_id.x_material_type or "",
                product.uom_id.display_name,
            ])

        field_sheet = workbook.create_sheet("导入字段")
        field_sheet.append(["字段", "中文说明"])
        for field_name, label in columns:
            field_sheet.append([field_name, label])

        for worksheet in workbook.worksheets:
            worksheet.freeze_panes = "A3" if worksheet == sheet else "A2"
            for cell in worksheet[1]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="D9EAF7")
            if worksheet == sheet and worksheet.max_row >= 2:
                for cell in worksheet[2]:
                    cell.font = Font(italic=True)
                    cell.fill = PatternFill("solid", fgColor="E2F0D9")
            for column_cells in worksheet.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                worksheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(max_length + 2, 12), 45)
        return workbook

    def _get_bom_export_row(self, columns):
        values = {
            "product_tmpl_id": self.product_tmpl_id.display_name,
            "product_id": self.product_id.display_name if self.product_id else "",
            "product_qty": self.product_qty,
            "product_uom_id": self.product_uom_id.display_name,
            "type": self.type,
            "code": self.code or "",
            "company_id": self.company_id.display_name if self.company_id else "",
        }
        for slot_number, line in enumerate(self.bom_line_ids[:BOM_IMPORT_COMPONENT_SLOT_COUNT], start=1):
            values[f"x_import_bom_component_product_{slot_number}"] = line.product_id.default_code or line.product_id.display_name
            values[f"x_import_bom_component_qty_{slot_number}"] = line.product_qty
            values[f"x_import_bom_component_uom_{slot_number}"] = line.product_uom_id.display_name
        return [values.get(field_name, "") for field_name, _label in columns]
