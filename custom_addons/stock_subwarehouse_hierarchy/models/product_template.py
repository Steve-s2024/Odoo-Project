from io import BytesIO
from urllib.parse import urlencode

from odoo import _, api, fields, models
from odoo.fields import Command


PRODUCT_IMPORT_TEMPLATE_ROUTE = "/stock_subwarehouse_hierarchy/import_template/product_template.xlsx"
IMPORT_CUSTOM_ATTRIBUTE_SLOT_COUNT = 20


class ProductAttribute(models.Model):
    _inherit = "product.attribute"

    x_apply_to_all_products = fields.Boolean(
        string="应用到所有产品",
        help="通过全局产品属性工具创建的属性会添加到所有现有和未来创建的产品。",
    )
    x_default_custom_value = fields.Char(
        string="默认自定义值",
        help="应用到产品的此全局自定义属性默认自由文本值。",
    )


class ProductTemplateCustomAttributeValue(models.Model):
    _name = "product.template.custom.attribute.value"
    _description = "产品自定义属性值"
    _order = "attribute_id, id"

    product_tmpl_id = fields.Many2one(
        "product.template",
        required=True,
        ondelete="cascade",
        index=True,
    )
    attribute_id = fields.Many2one(
        "product.attribute",
        required=True,
        domain=[("x_apply_to_all_products", "=", True)],
        ondelete="cascade",
        index=True,
    )
    value_text = fields.Char(string="值")

    _unique_product_attribute = models.Constraint(
        "UNIQUE(product_tmpl_id, attribute_id)",
        "每个自定义属性在同一产品中只能出现一次。",
    )


class ProductTemplate(models.Model):
    _inherit = "product.template"

    x_custom_attribute_value_ids = fields.One2many(
        "product.template.custom.attribute.value",
        "product_tmpl_id",
        string="自定义属性",
        copy=True,
    )

    # Unique import-only columns. They avoid repeated one2many field paths, which
    # Odoo can pair incorrectly during spreadsheet imports.
    x_import_custom_attribute_1 = fields.Char(string="导入自定义属性 1", copy=False)
    x_import_custom_attribute_value_1 = fields.Char(string="导入自定义属性值 1", copy=False)
    x_import_custom_attribute_2 = fields.Char(string="导入自定义属性 2", copy=False)
    x_import_custom_attribute_value_2 = fields.Char(string="导入自定义属性值 2", copy=False)
    x_import_custom_attribute_3 = fields.Char(string="导入自定义属性 3", copy=False)
    x_import_custom_attribute_value_3 = fields.Char(string="导入自定义属性值 3", copy=False)
    x_import_custom_attribute_4 = fields.Char(string="导入自定义属性 4", copy=False)
    x_import_custom_attribute_value_4 = fields.Char(string="导入自定义属性值 4", copy=False)
    x_import_custom_attribute_5 = fields.Char(string="导入自定义属性 5", copy=False)
    x_import_custom_attribute_value_5 = fields.Char(string="导入自定义属性值 5", copy=False)
    x_import_custom_attribute_6 = fields.Char(string="导入自定义属性 6", copy=False)
    x_import_custom_attribute_value_6 = fields.Char(string="导入自定义属性值 6", copy=False)
    x_import_custom_attribute_7 = fields.Char(string="导入自定义属性 7", copy=False)
    x_import_custom_attribute_value_7 = fields.Char(string="导入自定义属性值 7", copy=False)
    x_import_custom_attribute_8 = fields.Char(string="导入自定义属性 8", copy=False)
    x_import_custom_attribute_value_8 = fields.Char(string="导入自定义属性值 8", copy=False)
    x_import_custom_attribute_9 = fields.Char(string="导入自定义属性 9", copy=False)
    x_import_custom_attribute_value_9 = fields.Char(string="导入自定义属性值 9", copy=False)
    x_import_custom_attribute_10 = fields.Char(string="导入自定义属性 10", copy=False)
    x_import_custom_attribute_value_10 = fields.Char(string="导入自定义属性值 10", copy=False)
    x_import_custom_attribute_11 = fields.Char(string="导入自定义属性 11", copy=False)
    x_import_custom_attribute_value_11 = fields.Char(string="导入自定义属性值 11", copy=False)
    x_import_custom_attribute_12 = fields.Char(string="导入自定义属性 12", copy=False)
    x_import_custom_attribute_value_12 = fields.Char(string="导入自定义属性值 12", copy=False)
    x_import_custom_attribute_13 = fields.Char(string="导入自定义属性 13", copy=False)
    x_import_custom_attribute_value_13 = fields.Char(string="导入自定义属性值 13", copy=False)
    x_import_custom_attribute_14 = fields.Char(string="导入自定义属性 14", copy=False)
    x_import_custom_attribute_value_14 = fields.Char(string="导入自定义属性值 14", copy=False)
    x_import_custom_attribute_15 = fields.Char(string="导入自定义属性 15", copy=False)
    x_import_custom_attribute_value_15 = fields.Char(string="导入自定义属性值 15", copy=False)
    x_import_custom_attribute_16 = fields.Char(string="导入自定义属性 16", copy=False)
    x_import_custom_attribute_value_16 = fields.Char(string="导入自定义属性值 16", copy=False)
    x_import_custom_attribute_17 = fields.Char(string="导入自定义属性 17", copy=False)
    x_import_custom_attribute_value_17 = fields.Char(string="导入自定义属性值 17", copy=False)
    x_import_custom_attribute_18 = fields.Char(string="导入自定义属性 18", copy=False)
    x_import_custom_attribute_value_18 = fields.Char(string="导入自定义属性值 18", copy=False)
    x_import_custom_attribute_19 = fields.Char(string="导入自定义属性 19", copy=False)
    x_import_custom_attribute_value_19 = fields.Char(string="导入自定义属性值 19", copy=False)
    x_import_custom_attribute_20 = fields.Char(string="导入自定义属性 20", copy=False)
    x_import_custom_attribute_value_20 = fields.Char(string="导入自定义属性值 20", copy=False)

    @api.model
    def _get_global_custom_attributes(self):
        return self.env["product.attribute"].search([
            ("x_apply_to_all_products", "=", True),
        ], order="sequence, id")

    @api.model
    def _get_global_attribute_line_commands(self):
        commands = []
        for attribute in self._get_global_custom_attributes():
            value = attribute.value_ids.sorted(lambda record: (record.sequence, record.id))[:1]
            if value:
                commands.append(Command.create({
                    "attribute_id": attribute.id,
                    "value_ids": [Command.set(value.ids)],
                }))
        return commands

    @api.model
    def _get_global_custom_value_commands(self):
        return [
            Command.create({
                "attribute_id": attribute.id,
                "value_text": attribute.x_default_custom_value or "",
            })
            for attribute in self._get_global_custom_attributes()
        ]

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if "taxes_id" in fields_list:
            defaults["taxes_id"] = [Command.clear()]
        if "supplier_taxes_id" in fields_list:
            defaults["supplier_taxes_id"] = [Command.clear()]
        if "attribute_line_ids" in fields_list:
            defaults["attribute_line_ids"] = (
                defaults.get("attribute_line_ids", [])
                + self._get_global_attribute_line_commands()
            )
        if "x_custom_attribute_value_ids" in fields_list:
            defaults["x_custom_attribute_value_ids"] = (
                defaults.get("x_custom_attribute_value_ids", [])
                + self._get_global_custom_value_commands()
            )
        return defaults

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("taxes_id", [Command.clear()])
            vals.setdefault("supplier_taxes_id", [Command.clear()])
        products = super().create(vals_list)
        products._ensure_global_attribute_lines()
        return products

    @api.model
    def load(self, fields, data):
        custom_pairs = self._extract_custom_attribute_import_pairs(fields, data)
        if not custom_pairs:
            return super().load(fields, data)

        custom_field_names = {
            "x_custom_attribute_value_ids/attribute_id",
            "x_custom_attribute_value_ids/value_text",
        }
        for slot_number in range(1, IMPORT_CUSTOM_ATTRIBUTE_SLOT_COUNT + 1):
            custom_field_names.add(f"x_import_custom_attribute_{slot_number}")
            custom_field_names.add(f"x_import_custom_attribute_value_{slot_number}")

        kept_indexes = [
            index
            for index, field_name in enumerate(fields)
            if field_name not in custom_field_names
        ]
        cleaned_fields = [fields[index] for index in kept_indexes]
        cleaned_data = [
            [row[index] for index in kept_indexes]
            for row in data
        ]

        result = super().load(cleaned_fields, cleaned_data)
        if result.get("ids"):
            products = self.browse(result["ids"])
            for product, row_pairs in zip(products, custom_pairs):
                product._write_imported_custom_attribute_values(row_pairs)
        return result

    @api.model
    def _extract_custom_attribute_import_pairs(self, import_fields, import_data):
        legacy_attribute_field = "x_custom_attribute_value_ids/attribute_id"
        legacy_value_field = "x_custom_attribute_value_ids/value_text"
        legacy_attribute_indexes = [
            index
            for index, field_name in enumerate(import_fields)
            if field_name == legacy_attribute_field
        ]
        legacy_value_indexes = [
            index
            for index, field_name in enumerate(import_fields)
            if field_name == legacy_value_field
        ]

        slot_pairs = []
        for slot_number in range(1, IMPORT_CUSTOM_ATTRIBUTE_SLOT_COUNT + 1):
            attribute_slot = f"x_import_custom_attribute_{slot_number}"
            value_slot = f"x_import_custom_attribute_value_{slot_number}"
            if attribute_slot in import_fields and value_slot in import_fields:
                slot_pairs.append((
                    import_fields.index(attribute_slot),
                    import_fields.index(value_slot),
                ))

        if (
            (not legacy_attribute_indexes or not legacy_value_indexes)
            and not slot_pairs
        ):
            return []

        pairs_by_row = []
        for row in import_data:
            pairs = []
            for attribute_index, value_index in zip(legacy_attribute_indexes, legacy_value_indexes):
                attribute_name = str(row[attribute_index] or "").strip()
                value_text = str(row[value_index] or "").strip()
                if attribute_name:
                    pairs.append((attribute_name, value_text))
            for attribute_index, value_index in slot_pairs:
                attribute_name = str(row[attribute_index] or "").strip()
                value_text = str(row[value_index] or "").strip()
                if attribute_name:
                    pairs.append((attribute_name, value_text))
            pairs_by_row.append(pairs)
        return pairs_by_row

    def _write_imported_custom_attribute_values(self, row_pairs):
        ProductAttribute = self.env["product.attribute"]
        CustomValue = self.env["product.template.custom.attribute.value"]
        for attribute_name, value_text in row_pairs:
            attribute = ProductAttribute.search([
                ("x_apply_to_all_products", "=", True),
                ("name", "=", attribute_name),
            ], limit=1)
            if not attribute:
                matches = ProductAttribute.name_search(
                    name=attribute_name,
                    domain=[("x_apply_to_all_products", "=", True)],
                    operator="=",
                    limit=1,
                )
                attribute = ProductAttribute.browse(matches[0][0]) if matches else ProductAttribute
            if not attribute:
                continue

            custom_value = self.x_custom_attribute_value_ids.filtered(
                lambda record: record.attribute_id == attribute
            )
            if custom_value:
                custom_value[:1].value_text = value_text
            else:
                CustomValue.create({
                    "product_tmpl_id": self.id,
                    "attribute_id": attribute.id,
                    "value_text": value_text,
                })

    def _ensure_global_attribute_lines(self):
        AttributeLine = self.env["product.template.attribute.line"]
        CustomValue = self.env["product.template.custom.attribute.value"]
        global_attributes = self._get_global_custom_attributes()
        for product in self:
            existing_attributes = product.attribute_line_ids.attribute_id
            for attribute in global_attributes - existing_attributes:
                value = attribute.value_ids.sorted(lambda record: (record.sequence, record.id))[:1]
                if value:
                    AttributeLine.create({
                        "product_tmpl_id": product.id,
                        "attribute_id": attribute.id,
                        "value_ids": [Command.set(value.ids)],
                    })
            existing_custom_attributes = product.x_custom_attribute_value_ids.attribute_id
            for attribute in global_attributes - existing_custom_attributes:
                CustomValue.create({
                    "product_tmpl_id": product.id,
                    "attribute_id": attribute.id,
                    "value_text": attribute.x_default_custom_value or "",
                })

    def _remove_global_custom_attribute(self, attribute):
        self.mapped("x_custom_attribute_value_ids").filtered(
            lambda value: value.attribute_id == attribute
        ).unlink()
        self.mapped("attribute_line_ids").filtered(
            lambda line: line.attribute_id == attribute
        ).unlink()

    @api.model
    def get_import_templates(self):
        return [{
            "label": _("产品导入模板（当前自定义属性）"),
            "template": PRODUCT_IMPORT_TEMPLATE_ROUTE,
        }]

    @api.model
    def _get_dynamic_product_import_columns(self):
        columns = [
            ("name", "\u4ea7\u54c1\u540d\u79f0"),
            ("default_code", "\u5185\u90e8\u7f16\u53f7"),
            ("type", "\u4ea7\u54c1\u7c7b\u578b"),
            ("categ_id", "\u4ea7\u54c1\u7c7b\u522b"),
            ("list_price", "\u9500\u552e\u4ef7\u683c"),
            ("standard_price", "\u6210\u672c"),
            ("uom_id", "\u8ba1\u91cf\u5355\u4f4d"),
            ("uom_po_id", "\u91c7\u8d2d\u5355\u4f4d"),
            ("sale_ok", "\u53ef\u9500\u552e"),
            ("purchase_ok", "\u53ef\u91c7\u8d2d"),
            ("barcode", "\u6761\u7801"),
        ]
        for slot_number, _attribute in enumerate(
            self._get_global_custom_attributes()[:IMPORT_CUSTOM_ATTRIBUTE_SLOT_COUNT],
            start=1,
        ):
            columns += [
                (f"x_import_custom_attribute_{slot_number}", f"\u81ea\u5b9a\u4e49\u5c5e\u6027{slot_number}"),
                (f"x_import_custom_attribute_value_{slot_number}", f"\u81ea\u5b9a\u4e49\u5c5e\u6027\u503c{slot_number}"),
            ]
        return columns

    @api.model
    def _generate_dynamic_product_import_template_xlsx(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as error:
            raise ImportError(_("生成导入模板需要安装 openpyxl。")) from error

        workbook = Workbook()
        import_sheet = workbook.active
        import_sheet.title = "\u4ea7\u54c1\u5bfc\u5165"
        columns = self._get_dynamic_product_import_columns()
        import_sheet.append([field_name for field_name, _label in columns])
        import_sheet.append([label for _field_name, label in columns])

        sample_row = [
            "\u793a\u4f8b\u4ea7\u54c1",
            "EXAMPLE-001",
            "consu",
            "\u5168\u90e8",
            0,
            0,
            "\u4ef6",
            "\u4ef6",
            True,
            True,
            "",
        ]
        for attribute in self._get_global_custom_attributes()[:IMPORT_CUSTOM_ATTRIBUTE_SLOT_COUNT]:
            sample_row += [
                attribute.display_name,
                attribute.x_default_custom_value or "\u4efb\u610f\u6587\u672c",
            ]
        import_sheet.append(sample_row)

        attribute_sheet = workbook.create_sheet("\u81ea\u5b9a\u4e49\u5c5e\u6027\u5217\u8868")
        attribute_sheet.append(["\u5c5e\u6027ID", "\u5c5e\u6027", "\u9ed8\u8ba4\u503c", "\u5141\u8bb8\u4efb\u610f\u503c"])
        for attribute in self._get_global_custom_attributes():
            attribute_sheet.append([
                attribute.id,
                attribute.display_name,
                attribute.x_default_custom_value or "",
                "\u662f",
            ])

        field_sheet = workbook.create_sheet("\u5bfc\u5165\u5b57\u6bb5")
        field_sheet.append(["\u5b57\u6bb5", "\u4e2d\u6587\u8bf4\u660e"])
        for field_name, label in columns:
            field_sheet.append([field_name, label])

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
            "url": f"/stock_subwarehouse_hierarchy/export/product_template.xlsx?{urlencode({'ids': ids})}",
            "target": "self",
        }

    def _generate_dynamic_product_export_xlsx(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as error:
            raise ImportError(_("生成导出文件需要安装 openpyxl。")) from error

        workbook = Workbook()
        export_sheet = workbook.active
        export_sheet.title = "\u4ea7\u54c1\u5bfc\u51fa"
        columns = self._get_dynamic_product_import_columns()
        export_sheet.append([field_name for field_name, _label in columns])
        export_sheet.append([label for _field_name, label in columns])
        for product in self:
            export_sheet.append(product._get_dynamic_product_export_row(columns))

        attribute_sheet = workbook.create_sheet("\u81ea\u5b9a\u4e49\u5c5e\u6027\u5217\u8868")
        attribute_sheet.append(["\u5c5e\u6027ID", "\u5c5e\u6027", "\u9ed8\u8ba4\u503c", "\u5141\u8bb8\u4efb\u610f\u503c"])
        for attribute in self._get_global_custom_attributes():
            attribute_sheet.append([
                attribute.id,
                attribute.display_name,
                attribute.x_default_custom_value or "",
                "\u662f",
            ])

        field_sheet = workbook.create_sheet("\u5bfc\u51fa\u5b57\u6bb5")
        field_sheet.append(["\u5b57\u6bb5", "\u4e2d\u6587\u8bf4\u660e"])
        for field_name, label in columns:
            field_sheet.append([field_name, label])

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

    def _get_dynamic_product_export_row(self, columns):
        custom_values = {
            value.attribute_id.id: value.value_text
            for value in self.x_custom_attribute_value_ids
        }
        row = []
        for field_name, _label in columns:
            if field_name.startswith("x_import_custom_attribute_value_"):
                slot_number = int(field_name.rsplit("_", 1)[1])
                attributes = self._get_global_custom_attributes()
                attribute = attributes[slot_number - 1] if len(attributes) >= slot_number else self.env["product.attribute"]
                row.append(custom_values.get(attribute.id, "") if attribute else "")
            elif field_name.startswith("x_import_custom_attribute_"):
                slot_number = int(field_name.rsplit("_", 1)[1])
                attributes = self._get_global_custom_attributes()
                attribute = attributes[slot_number - 1] if len(attributes) >= slot_number else self.env["product.attribute"]
                row.append(attribute.display_name if attribute else "")
            else:
                row.append(self._get_product_export_value(field_name))
        return row

    def _get_product_export_value(self, field_name):
        if field_name == "type" and field_name not in self._fields:
            return "consu"
        if field_name not in self._fields:
            return ""
        value = self[field_name]
        field = self._fields[field_name]
        if field.type == "many2one":
            return value.display_name if value else ""
        if field.type in ("many2many", "one2many"):
            return ", ".join(value.mapped("display_name"))
        return value
