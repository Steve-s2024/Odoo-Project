from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command


class ProductAttributeApplyAllWizard(models.TransientModel):
    _name = "stock.subwarehouse.product.attribute.apply.wizard"
    _description = "为所有产品添加属性"

    attribute_name = fields.Char(string="属性名称", required=True)
    value_name = fields.Char(string="默认值", required=True, default="默认")
    include_archived_products = fields.Boolean(string="包含已归档产品")

    def action_apply(self):
        self.ensure_one()
        attribute_name = self.attribute_name.strip()
        value_name = self.value_name.strip()
        if not attribute_name or not value_name:
            raise UserError(_("属性名称和值不能为空。"))

        ProductAttribute = self.env["product.attribute"]
        ProductAttributeValue = self.env["product.attribute.value"]
        ProductTemplate = self.env["product.template"].with_context(
            active_test=not self.include_archived_products,
        )
        AttributeLine = self.env["product.template.attribute.line"]

        attribute = ProductAttribute.search([("name", "=", attribute_name)], limit=1)
        if not attribute:
            attribute = ProductAttribute.create({
                "name": attribute_name,
                "create_variant": "no_variant",
                "x_apply_to_all_products": True,
                "x_default_custom_value": value_name,
            })
        else:
            attribute.write({
                "x_apply_to_all_products": True,
                "x_default_custom_value": value_name,
            })

        value = ProductAttributeValue.search([
            ("attribute_id", "=", attribute.id),
            ("name", "=", value_name),
        ], limit=1)
        if not value:
            value = ProductAttributeValue.create({
                "name": value_name,
                "attribute_id": attribute.id,
                "is_custom": True,
            })
        elif not value.is_custom:
            value.is_custom = True

        templates = ProductTemplate.search([])
        existing_lines = self.env["product.template.attribute.line"].with_context(
            active_test=False,
        ).search([
            ("product_tmpl_id", "in", templates.ids),
            ("attribute_id", "=", attribute.id),
        ])
        lines_by_template = {
            line.product_tmpl_id.id: line
            for line in existing_lines
        }

        for template in templates:
            line = lines_by_template.get(template.id)
            if line:
                write_values = {"active": True}
                if value not in line.value_ids:
                    write_values["value_ids"] = [Command.link(value.id)]
                line.write(write_values)
            else:
                AttributeLine.create({
                    "product_tmpl_id": template.id,
                    "attribute_id": attribute.id,
                    "value_ids": [Command.set([value.id])],
                })

        templates._ensure_global_attribute_lines()
        custom_values = self.env["product.template.custom.attribute.value"].search([
            ("product_tmpl_id", "in", templates.ids),
            ("attribute_id", "=", attribute.id),
            ("value_text", "in", [False, ""]),
        ])
        custom_values.write({"value_text": value_name})

        return {
            "type": "ir.actions.act_window",
            "name": _("产品"),
            "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [("id", "in", templates.ids)],
        }
