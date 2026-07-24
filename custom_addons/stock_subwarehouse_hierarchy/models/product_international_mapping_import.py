import base64
from io import BytesIO

from odoo import _, fields, models
from odoo.exceptions import UserError


class ProductInternationalMappingImportWizard(models.TransientModel):
    _name = "stock.subwarehouse.product.international.mapping.import.wizard"
    _description = "Import Product International Mapping"

    import_file = fields.Binary(string="价格表文件", required=True)
    import_filename = fields.Char(string="文件名")

    @staticmethod
    def _normalized_header(value):
        return "".join(str(value or "").split()).replace("（", "(").replace("）", ")").casefold()

    @classmethod
    def _find_header_index(cls, headers, candidates):
        return next((headers[candidate] for candidate in candidates if candidate in headers), None)

    def action_import_mapping(self):
        self.ensure_one()
        try:
            from openpyxl import load_workbook
        except ImportError as error:
            raise UserError(_("导入价格表需要安装 openpyxl。")) from error

        try:
            workbook = load_workbook(
                BytesIO(base64.b64decode(self.import_file)),
                read_only=True,
                data_only=True,
            )
        except Exception as error:
            raise UserError(_("无法读取 Excel 价格表，请上传 .xlsx 文件。")) from error

        worksheet = next(
            (
                sheet for sheet in workbook.worksheets
                if any(
                    self._normalized_header(cell.value) == "product_code_pattern"
                    for row in sheet.iter_rows(max_row=10)
                    for cell in row
                )
            ),
            workbook.active,
        )
        header_row = None
        headers = {}
        for row_number, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
            normalized_cells = {
                self._normalized_header(value): index
                for index, value in enumerate(row)
                if value not in (None, "")
            }
            if "product_code_pattern" in normalized_cells:
                header_row = row_number
                headers = normalized_cells
                break
            if row_number >= 12:
                break

        pattern_index = self._find_header_index(headers, ("product_code_pattern",))
        english_name_index = self._find_header_index(headers, ("英文名称", "product", "englishname"))
        price_index = self._find_header_index(headers, ("usd价格", "usdprice", "retailprice(usd)", "retailpriceusd"))
        if header_row is None or pattern_index is None or english_name_index is None or price_index is None:
            raise UserError(_(
                "未找到国际价格映射标题行。文件必须包含“product_code_pattern”、“英文名称（或 PRODUCT）”及“USD 价格（或 RETAIL PRICE (USD)）”列。"
            ))

        mappings_by_pattern = {}
        for row_number, row in enumerate(worksheet.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
            pattern = str(row[pattern_index] or "").strip() if len(row) > pattern_index else ""
            if not pattern:
                continue
            english_name = str(row[english_name_index] or "").strip() if len(row) > english_name_index else ""
            price = row[price_index] if len(row) > price_index else False
            if not english_name:
                raise UserError(_("第 %s 行的英文名称为空。") % row_number)
            try:
                usd_price = float(price)
            except (TypeError, ValueError):
                raise UserError(_("第 %s 行的美元价格不是有效数字。") % row_number)
            values = {"english_name": english_name, "usd_price": usd_price}
            existing_values = mappings_by_pattern.get(pattern)
            if existing_values and existing_values != values:
                raise UserError(_("产品编号模式“%s”在文件中存在冲突的英文名称或美元价格。") % pattern)
            mappings_by_pattern[pattern] = values

        if not mappings_by_pattern:
            raise UserError(_("文件中没有可导入的产品编号模式。空白模式不会参与自动映射。"))

        Mapping = self.env["stock.subwarehouse.product.website.code.mapping"]
        created_count = 0
        updated_count = 0
        for pattern, values in mappings_by_pattern.items():
            mapping = Mapping.search([("product_code_pattern", "=", pattern)], limit=1)
            if mapping:
                mapping.write({**values, "active": True})
                updated_count += 1
            else:
                Mapping.create({"product_code_pattern": pattern, **values})
                created_count += 1

        applied_count = Mapping.action_apply_to_products()
        message = _(
            "已导入 %s 条产品编号模式（新增 %s 条、更新 %s 条），并更新 %s 个匹配产品的英文名称和美元价格。"
        ) % (len(mappings_by_pattern), created_count, updated_count, applied_count)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("国际网站编号价格映射导入完成"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }
