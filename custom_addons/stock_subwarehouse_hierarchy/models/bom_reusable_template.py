from odoo import api, fields, models


class BomReusableTemplate(models.Model):
    _name = "stock.subwarehouse.bom.template"
    _description = "可复用BOM模板"
    _order = "name, id"

    name = fields.Char(string="模板名称", required=True)
    code = fields.Char(string="模板编号")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="公司",
        required=True,
        default=lambda self: self.env.company,
    )
    line_ids = fields.One2many(
        "stock.subwarehouse.bom.template.line",
        "template_id",
        string="组件",
        copy=True,
    )


class BomReusableTemplateLine(models.Model):
    _name = "stock.subwarehouse.bom.template.line"
    _description = "可复用BOM模板组件"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "stock.subwarehouse.bom.template",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(related="template_id.company_id", store=True)
    product_id = fields.Many2one(
        "product.product",
        string="组件产品",
        required=True,
        check_company=True,
    )
    product_qty = fields.Float(string="组件数量", required=True, default=1.0)
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="组件单位",
        required=True,
    )

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self:
            if line.product_id:
                line.product_uom_id = line.product_id.uom_id
