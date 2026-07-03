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

        self.with_context(active_test=False).search([]).write({
            "symbol": "￥",
            "position": "before",
        })

        if yuan:
            self.env["res.company"].search([]).write({"currency_id": yuan.id})
            if "product.pricelist" in self.env:
                self.env["product.pricelist"].search([]).write({"currency_id": yuan.id})
        return True
