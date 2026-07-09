from odoo import api, models


DEFAULT_CHINESE_LANG = "zh_CN"


class ResLang(models.Model):
    _inherit = "res.lang"

    @api.model
    def action_use_chinese_by_default(self):
        lang = self.sudo()._activate_lang(DEFAULT_CHINESE_LANG)
        if not lang:
            return False

        self.env["ir.default"].sudo().set("res.partner", "lang", DEFAULT_CHINESE_LANG)
        partners = self.env["res.partner"].with_context(active_test=False).sudo().search([])
        partners.write({"lang": DEFAULT_CHINESE_LANG})

        users = self.env["res.users"].with_context(active_test=False).sudo().search([])
        users.write({"lang": DEFAULT_CHINESE_LANG})

        if "website" in self.env:
            self.env["website"].sudo().search([]).write({"default_lang_id": lang.id})
        return True
