from odoo import _
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.exceptions import UserError


class WebsiteSaleStockSource(WebsiteSale):
    def _get_shop_payment_errors(self, order):
        errors = super()._get_shop_payment_errors(order)
        if order and order.state in ("draft", "sent"):
            try:
                order.sudo()._prepare_website_stock_for_payment()
            except UserError as error:
                errors.append((_("库存不足"), str(error)))
        return errors
