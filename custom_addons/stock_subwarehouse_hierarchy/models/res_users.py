from odoo import models


class ResUsers(models.Model):
    _inherit = "res.users"

    def _on_webclient_bootstrap(self):
        super()._on_webclient_bootstrap()
        dashboard = self.env.ref(
            "spreadsheet_dashboard.ir_actions_dashboard_action",
            raise_if_not_found=False,
        )
        discuss = self.env.ref("mail.action_discuss", raise_if_not_found=False)
        discuss_id = discuss.id if discuss else False
        if dashboard and (not self.action_id or self.action_id.id == discuss_id):
            self.sudo().action_id = dashboard.id
