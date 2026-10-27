from odoo import api, fields, models


class ProductImportResultBatch(models.TransientModel):
    _name = "stock.subwarehouse.product.import.result.batch"
    _description = "产品导入结果"
    _order = "create_date desc, id desc"

    import_job_id = fields.Integer(string="导入任务 ID", required=True, index=True)
    user_id = fields.Many2one(
        "res.users",
        string="导入人",
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
    )
    line_ids = fields.One2many(
        "stock.subwarehouse.product.import.result.line",
        "batch_id",
        string="导入明细",
        readonly=True,
    )
    success_count = fields.Integer(string="成功", compute="_compute_counts")
    failed_count = fields.Integer(string="未导入", compute="_compute_counts")

    @api.depends("line_ids.status")
    def _compute_counts(self):
        for batch in self:
            batch.success_count = len(batch.line_ids.filtered(lambda line: line.status == "success"))
            batch.failed_count = len(batch.line_ids.filtered(lambda line: line.status == "failed"))

    @api.autovacuum
    def _gc_product_import_results(self):
        self.search([
            ("create_date", "<", fields.Datetime.subtract(fields.Datetime.now(), days=30)),
        ]).unlink()

    def action_open_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "产品导入结果",
            "res_model": "stock.subwarehouse.product.import.result.line",
            "view_mode": "list,form",
            "views": [
                (
                    self.env.ref(
                        "stock_subwarehouse_hierarchy.view_product_import_result_line_list"
                    ).id,
                    "list",
                ),
                (False, "form"),
            ],
            "domain": [("batch_id", "=", self.id)],
            "context": {
                "create": False,
                "edit": False,
                "delete": False,
                "search_default_group_by_status": 1,
            },
            "target": "current",
        }


class ProductImportResultLine(models.TransientModel):
    _name = "stock.subwarehouse.product.import.result.line"
    _description = "产品导入结果明细"
    _order = "source_row, id"

    batch_id = fields.Many2one(
        "stock.subwarehouse.product.import.result.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    source_row = fields.Integer(string="原始行号", required=True, index=True)
    status = fields.Selection(
        [("success", "成功"), ("failed", "未导入")],
        string="状态",
        required=True,
        index=True,
    )
    product_name = fields.Char(string="产品名称")
    default_code = fields.Char(string="产品编码", index=True)
    product_tmpl_id = fields.Many2one(
        "product.template",
        string="已导入产品",
        readonly=True,
        ondelete="set null",
    )
    reason = fields.Char(string="结果说明")

    @api.autovacuum
    def _gc_orphan_product_import_result_lines(self):
        self.search([
            ("create_date", "<", fields.Datetime.subtract(fields.Datetime.now(), days=30)),
        ]).unlink()
