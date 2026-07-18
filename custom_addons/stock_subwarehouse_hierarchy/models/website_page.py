from odoo import api, models


class WebsitePage(models.Model):
    _inherit = "website.page"

    @api.model
    def action_make_all_pages_public(self):
        pages = self.sudo().search([])
        pages.write({
            "website_published": True,
            "visibility": False,
            "group_ids": [(5, 0, 0)],
        })
        self.env["website.menu"].sudo().search([]).write({
            "group_ids": [(5, 0, 0)],
        })
        self.env.registry.clear_cache("templates")
        return True
