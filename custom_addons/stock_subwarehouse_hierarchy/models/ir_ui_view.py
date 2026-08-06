from odoo import api, models


class IrUiView(models.Model):
    _inherit = "ir.ui.view"

    @api.model
    def action_sync_shop_group_selector_views(self):
        """Update website-specific translated copies left behind by the website editor."""
        views = self.sudo().with_context(active_test=False).search([
            ("key", "=", "stock_subwarehouse_hierarchy.product_page_shop_group_siblings"),
        ])
        languages = self.env["res.lang"].sudo().with_context(active_test=False).search([
            ("active", "=", True),
        ])
        replacements = (
            ("option_group['key'] == 'color' else 'px-3 py-2'", "option_group['key'] == 'type_color' else 'px-3 py-2'"),
            ("option_group['key'] == 'color' else None", "option_group['key'] == 'type_color' else None"),
            ("option_group['key'] == 'color' and color_image_product", "option_group['key'] == 'type_color' and color_image_product"),
            ("t-att-data-color=\"variant_row['values']['color']\"", "t-att-data-type-color=\"variant_row['values']['type_color']\""),
            ('const color = selectedValue("color");', 'const typeColor = selectedValue("type_color");'),
            ("entry.dataset.color === color", "entry.dataset.typeColor === typeColor"),
            ("findVariantForColor", "findVariantForTypeColor"),
            ("entry.dataset.color === color", "entry.dataset.typeColor === typeColor"),
            ('changedKey === "color"', 'changedKey === "type_color"'),
        )
        for view in views:
            for language in languages:
                localized_view = view.with_context(lang=language.code)
                arch = localized_view.arch_db or ""
                updated_arch = arch
                for old, new in replacements:
                    updated_arch = updated_arch.replace(old, new)
                if updated_arch != arch:
                    localized_view.write({"arch_db": updated_arch})
        return True
