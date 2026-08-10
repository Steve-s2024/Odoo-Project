import datetime

from odoo import _, fields, models
from odoo.exceptions import UserError


class ShopApiKeyWizard(models.TransientModel):
    _name = "shop.api.key.wizard"
    _description = "Shop API Key One-time Display"

    client_id = fields.Many2one(
        "shop.api.client", string="API 客户端", required=True, readonly=True,
    )
    key_name = fields.Char(string="密钥名称", required=True)
    expiration_date = fields.Datetime(string="失效时间", required=True)
    generated_key = fields.Char(string="API 密钥（仅显示一次）", readonly=True)
    generated = fields.Boolean(readonly=True)

    def action_generate(self):
        self.ensure_one()
        if self.generated:
            raise UserError(_("该向导已经生成过密钥，请关闭后重新打开。"))
        if self.expiration_date <= fields.Datetime.now():
            raise UserError(_("密钥失效时间必须晚于当前时间。"))
        secret = self.client_id.generate_api_key(
            name=self.key_name,
            expiration_date=self.expiration_date,
        )
        self.write({"generated_key": secret, "generated": True})
        return {
            "type": "ir.actions.act_window",
            "name": _("保存 API 密钥"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class ShopApiClient(models.Model):
    _inherit = "shop.api.client"

    def action_open_key_wizard(self):
        self.ensure_one()
        wizard = self.env["shop.api.key.wizard"].create({
            "client_id": self.id,
            "key_name": f"Shop API - {self.name}",
            "expiration_date": fields.Datetime.now() + datetime.timedelta(days=90),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("生成 API 密钥"),
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }


class ShopApiWebhook(models.Model):
    _inherit = "shop.api.webhook"

    def action_rotate_secret(self):
        for webhook in self:
            webhook.secret = __import__("secrets").token_urlsafe(48)
        return True


class ShopApiWebhookDelivery(models.Model):
    _inherit = "shop.api.webhook.delivery"

    def action_retry_delivery(self):
        for delivery in self:
            delivery._deliver()
        return True
