import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductWebsiteCodeMapping(models.Model):
    _name = "stock.subwarehouse.product.website.code.mapping"
    _description = "Website Product Code Mapping"
    _order = "specificity desc, id"

    product_code_pattern = fields.Char(string="产品编号模式", required=True, index=True)
    english_name = fields.Char(string="英文网站名称", required=True)
    usd_price = fields.Float(string="英文网站价格 (USD)", required=True, digits="Product Price")
    active = fields.Boolean(default=True)
    specificity = fields.Integer(compute="_compute_specificity", store=True, index=True)

    _unique_product_code_pattern = models.Constraint(
        "UNIQUE(product_code_pattern)",
        "每个产品编号模式只能创建一次。",
    )

    @api.depends("product_code_pattern")
    def _compute_specificity(self):
        for mapping in self:
            mapping.specificity = len((mapping.product_code_pattern or "").replace("*", ""))

    @api.constrains("product_code_pattern")
    def _check_product_code_pattern(self):
        allowed_pattern = re.compile(r"^[A-Za-z0-9#\-\[\]*]+$")
        for mapping in self:
            pattern = (mapping.product_code_pattern or "").strip()
            if not pattern or not allowed_pattern.fullmatch(pattern):
                raise ValidationError(_("产品编号模式只能包含字母、数字、连字符、#、方括号和单字符通配符 *。"))

    @staticmethod
    def _matches_pattern(product_code, pattern):
        expression = re.escape(pattern).replace(r"\*", ".")
        return bool(re.fullmatch(expression, product_code, flags=re.IGNORECASE))

    @api.model
    def find_matching_mapping(self, product_code):
        if not product_code:
            return self
        matches = self.search([("active", "=", True)]).filtered(
            lambda mapping: self._matches_pattern(product_code, mapping.product_code_pattern)
        )
        if not matches:
            return self
        highest_specificity = max(matches.mapped("specificity"))
        best_matches = matches.filtered(lambda mapping: mapping.specificity == highest_specificity)
        if len(best_matches) > 1:
            raise ValidationError(_(
                "产品编号“%s”同时匹配多个同等优先级的国际网站价格规则：%s。"
            ) % (product_code, ", ".join(best_matches.mapped("product_code_pattern"))))
        return best_matches

    def action_apply_to_products(self):
        Product = self.env["product.template"]
        updated_count = 0
        for product in Product.search([("default_code", "!=", False)]):
            mapping = self.find_matching_mapping(product.default_code)
            if not mapping:
                continue
            values = {
                "x_website_code_mapping_id": mapping.id,
                "x_website_english_name": mapping.english_name,
                "x_website_usd_price": mapping.usd_price,
            }
            if (
                product.x_website_code_mapping_id.id != mapping.id
                or product.x_website_english_name != mapping.english_name
                or product.x_website_usd_price != mapping.usd_price
            ):
                product.write(values)
                updated_count += 1
        return updated_count
