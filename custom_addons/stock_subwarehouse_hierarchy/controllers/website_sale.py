from odoo import _
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.exceptions import UserError


class WebsiteSaleStockSource(WebsiteSale):
    def _shop_lookup_products(self, options, post, search, website):
        fuzzy_search_term, _product_count, search_result = super()._shop_lookup_products(
            options,
            post,
            search,
            website,
        )
        grouped_products = search_result._get_shop_grouped_products()
        return fuzzy_search_term, len(grouped_products), grouped_products

    def _get_shop_payment_errors(self, order):
        errors = super()._get_shop_payment_errors(order)
        if order and order.state in ("draft", "sent"):
            try:
                order.sudo()._prepare_website_stock_for_payment()
            except UserError as error:
                errors.append((_("库存不足"), str(error)))
        return errors
