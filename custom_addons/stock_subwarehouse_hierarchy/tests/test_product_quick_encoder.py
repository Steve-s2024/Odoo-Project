import base64
from io import BytesIO

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestProductQuickEncoder(TransactionCase):
    def _wizard_with_lines(self, lines):
        return self.env["stock.subwarehouse.product.quick.encoder.wizard"].create({
            "line_ids": [(0, 0, line) for line in lines],
        })

    def test_generates_established_snowboard_boot_code(self):
        wizard = self._wizard_with_lines([{
            "product_name": "单板滑雪鞋",
            "manufacturer": "15",
            "production_yymm": "2410",
            "product_type": "S1",
            "finished_type": "M",
            "audience": "A",
            "flex": "7",
            "color": "黑",
            "size": "250",
        }])
        wizard.action_generate_codes()
        self.assertEqual(wizard.line_ids.generated_code, "152410S1-MA007-H001250")
        self.assertEqual(wizard.line_ids.status, "ready")

    def test_infers_the_most_specific_type_from_name(self):
        wizard = self._wizard_with_lines([{
            "product_name": "快穿单板鞋",
            "manufacturer": "15",
            "production_yymm": "2410",
            "finished_type": "M",
            "audience": "A",
            "flex": "10",
            "color": "H001",
            "size": "260",
        }])
        wizard.action_generate_codes()
        self.assertEqual(wizard.line_ids.generated_code, "152410SE-MA010-H001260")

    def test_matches_similar_names_by_common_characters_and_displays_mapping(self):
        wizard = self._wizard_with_lines([{
            "product_name": "五指滑雪手套",
            "manufacturer": "熙堃工厂",
            "production_yymm": "2410",
            "product_type": "五指滑雪手套",
            "finished_type": "M",
            "audience": "A",
            "flex": "0",
            "color": "黑颜色",
            "size": "M",
        }])
        wizard.action_generate_codes()
        line = wizard.line_ids
        self.assertEqual(line.generated_code, "152410T5-MA000-H001##M")
        self.assertEqual(line.status, "ready")
        self.assertIn("熙堃工厂 → 熙堃（15）", line.manufacturer_match)
        self.assertIn("五指滑雪手套 → 五指手套（T5）", line.product_type_match)
        self.assertIn("黑颜色 → 黑（H001）", line.color_match)

    def test_ambiguous_name_is_blocked_instead_of_guessed(self):
        wizard = self._wizard_with_lines([{
            "product_name": "滑雪手套",
            "manufacturer": "15",
            "production_yymm": "2410",
            "product_type": "滑雪手套",
            "finished_type": "M",
            "audience": "A",
            "flex": "0",
            "color": "白",
            "size": "M",
            "source_row": 7,
        }])
        wizard.action_generate_codes()
        line = wizard.line_ids
        self.assertEqual(line.status, "error")
        self.assertFalse(line.generated_code)
        self.assertIn("产品类型", line.remark)
        self.assertIn("Excel 第 7 行", wizard.failed_entries)

    def test_detects_existing_and_batch_duplicate_codes(self):
        self.env["product.template"].create({
            "name": "Existing encoded product",
            "default_code": "152410T5-MA000-W001##M",
        })
        base_line = {
            "product_name": "五指手套",
            "manufacturer": "15",
            "production_yymm": "2410",
            "product_type": "T5",
            "finished_type": "M",
            "audience": "A",
            "color": "白",
            "size": "M",
        }
        duplicate_line = {**base_line, "color": "黑", "size": "L"}
        wizard = self._wizard_with_lines([
            base_line,
            duplicate_line,
            duplicate_line,
        ])
        wizard.action_generate_codes()
        self.assertEqual(wizard.line_ids[0].status, "exists")
        self.assertEqual(wizard.line_ids[1].status, "ready")
        self.assertEqual(wizard.line_ids[2].status, "duplicate")

    def test_excel_import_preserves_row_order_and_reports_errors(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.append([
            "产品名称", "厂家代码/名称", "生产年月(YYMM)", "产品类型代码/名称",
            "成品类型(M/F)", "成人儿童(A/K)", "硬度", "颜色", "尺码",
        ])
        sheet.append(["单板滑雪鞋", "15", "2410", "S1", "M", "A", "10", "白", "255"])
        sheet.append(["缺少颜色", "15", "2410", "T5", "M", "A", "", "", "M"])
        sheet.append(["缺少成品与人群", "15", "2410", "T5", "", "", "", "白", "L"])
        stream = BytesIO()
        workbook.save(stream)
        wizard = self.env["stock.subwarehouse.product.quick.encoder.wizard"].create({
            "import_file": base64.b64encode(stream.getvalue()),
            "import_filename": "encoder.xlsx",
        })
        wizard.action_import_excel()
        self.assertEqual(wizard.line_ids.mapped("source_row"), [2, 3, 4])
        self.assertEqual(wizard.line_ids[0].generated_code, "152410S1-MA010-W001255")
        self.assertEqual(wizard.line_ids[0].status, "ready")
        self.assertEqual(wizard.line_ids[1].status, "error")
        self.assertFalse(wizard.line_ids[1].generated_code)
        self.assertIn("Excel 第 3 行", wizard.failed_entries)
        self.assertEqual(wizard.line_ids[2].status, "error")
        self.assertIn("成品类型", wizard.line_ids[2].remark)
        self.assertIn("成人/儿童", wizard.line_ids[2].remark)
        self.assertIn("Excel 第 4 行", wizard.failed_entries)
        result = wizard._generate_result_xlsx()
        self.assertTrue(result.startswith(b"PK"))
