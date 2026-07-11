from collections import OrderedDict

from odoo import models


class ProductTemplateAttributeLine(models.Model):
    _inherit = "product.template.attribute.line"

    def _without_managed_custom_attributes(self):
        return self.filtered(lambda line: not line.attribute_id.x_apply_to_all_products)

    def _prepare_single_value_for_display(self):
        single_value_attributes = super(
            ProductTemplateAttributeLine,
            self._without_managed_custom_attributes(),
        )._prepare_single_value_for_display()
        return OrderedDict(
            (attribute, lines)
            for attribute, lines in single_value_attributes.items()
            if not attribute.x_apply_to_all_products
        )

    def _prepare_single_value_including_multi_type_for_display(self):
        single_value_attributes = super(
            ProductTemplateAttributeLine,
            self._without_managed_custom_attributes(),
        )._prepare_single_value_including_multi_type_for_display()
        return OrderedDict(
            (attribute, lines)
            for attribute, lines in single_value_attributes.items()
            if not attribute.x_apply_to_all_products
        )
