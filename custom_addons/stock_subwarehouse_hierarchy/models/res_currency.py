from odoo import api, models


class ResCurrency(models.Model):
    _inherit = "res.currency"

    @api.model
    def action_use_yuan_symbol_everywhere(self):
        yuan = self.env.ref("base.CNY", raise_if_not_found=False)
        if not yuan:
            yuan = self.search([("name", "=", "CNY")], limit=1)
        if yuan:
            yuan.write({
                "active": True,
                "symbol": "￥",
                "position": "before",
            })

        usd = self.env.ref("base.USD", raise_if_not_found=False)
        if usd:
            usd.write({
                "active": True,
                "symbol": "$",
                "position": "before",
            })

        if yuan:
            self.env["res.company"].search([]).write({"currency_id": yuan.id})
        return True
