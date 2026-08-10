from odoo import fields, models


class StorefrontProductTemplate(models.Model):
    _inherit = "product.template"

    shop_api_uuid = fields.Char(copy=False, index=True)


class StorefrontProductProduct(models.Model):
    _inherit = "product.product"

    shop_api_uuid = fields.Char(copy=False, index=True)


class StorefrontProductImage(models.Model):
    _inherit = "product.image"

    shop_api_uuid = fields.Char(copy=False, index=True)


class StorefrontDeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    shop_api_uuid = fields.Char(copy=False, index=True)


class StorefrontPartner(models.Model):
    _inherit = "res.partner"

    shop_api_uuid = fields.Char(copy=False, index=True)
