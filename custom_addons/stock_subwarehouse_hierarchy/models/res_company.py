import base64

from odoo import api, models
from odoo.tools import file_open


SUN_LOGO_PATH = "stock_subwarehouse_hierarchy/static/img/logo.png"


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model
    def _get_sun_logo_binary(self):
        with file_open(SUN_LOGO_PATH, "rb") as logo_file:
            return base64.b64encode(logo_file.read())

    @api.model
    def action_apply_sun_logo(self):
        logo = self._get_sun_logo_binary()
        self.search([]).write({"logo": logo})
        return True
