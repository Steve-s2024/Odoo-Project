from odoo import api, fields, models


BUSINESS_IMPORT_MODELS = [
    ("sale.order", "销售订单"),
    ("stock.quant", "库存记录"),
    ("mrp.production", "制造单"),
]


class BusinessImportResultBatch(models.TransientModel):
    _name = "stock.subwarehouse.business.import.result.batch"
    _description = "业务导入结果"
    _order = "create_date desc, id desc"

    import_job_id = fields.Integer(string="导入任务 ID", required=True, index=True)
    user_id = fields.Many2one(
        "res.users",
        string="导入人",
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
    )
    res_model = fields.Selection(
        BUSINESS_IMPORT_MODELS,
        string="导入类型",
        required=True,
        readonly=True,
        index=True,
    )
    line_ids = fields.One2many(
        "stock.subwarehouse.business.import.result.line",
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
    def _gc_business_import_results(self):
        self.search([
            ("create_date", "<", fields.Datetime.subtract(fields.Datetime.now(), days=30)),
        ]).unlink()
    def action_open_lines(self):
        self.ensure_one()
        model_label = dict(BUSINESS_IMPORT_MODELS).get(self.res_model, "业务")
        return {
            "type": "ir.actions.act_window",
            "name": f"{model_label}导入结果",
            "res_model": "stock.subwarehouse.business.import.result.line",
            "view_mode": "list,form",
            "views": [
                (
                    self.env.ref(
                        "stock_subwarehouse_hierarchy.view_business_import_result_line_list"
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


class BusinessImportResultLine(models.TransientModel):
    _name = "stock.subwarehouse.business.import.result.line"
    _description = "业务导入结果明细"
    _order = "source_row, id"

    batch_id = fields.Many2one(
        "stock.subwarehouse.business.import.result.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    source_row = fields.Integer(string="Excel 行号", required=True, index=True)
    status = fields.Selection(
        [("success", "成功"), ("failed", "未导入")],
        string="状态",
        required=True,
        index=True,
    )
    identifier = fields.Char(string="记录 ID", index=True)
    record_ref = fields.Reference(
        selection=BUSINESS_IMPORT_MODELS,
        string="已导入记录",
        readonly=True,
    )
    reason = fields.Char(string="结果说明")

    @api.autovacuum
    def _gc_orphan_business_import_result_lines(self):
        self.search([
            ("create_date", "<", fields.Datetime.subtract(fields.Datetime.now(), days=30)),
        ]).unlink()
