from odoo import _, fields, models
from odoo.exceptions import UserError


class ProductAttributeRemoveAllWizard(models.TransientModel):
    _name = "stock.subwarehouse.product.attribute.remove.wizard"
    _description = "从所有产品移除自定义属性"

    attribute_id = fields.Many2one(
        "product.attribute",
        required=True,
        domain=[("x_apply_to_all_products", "=", True)],
        string="自定义属性",
    )
    include_archived_products = fields.Boolean(string="包含已归档产品")

    def action_remove(self):
        self.ensure_one()
        attribute = self.attribute_id
        if not attribute.x_apply_to_all_products:
            raise UserError(_("此工具只能移除全局自定义属性。"))

        templates = self.env["product.template"].with_context(
            active_test=not self.include_archived_products,
        ).search([])
        templates._remove_global_custom_attribute(attribute)
        attribute.write({
            "x_apply_to_all_products": False,
            "x_default_custom_value": False,
        })

        return {
            "type": "ir.actions.act_window",
            "name": _("产品"),
            "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [("id", "in", templates.ids)],
        }
