from odoo.addons.storefront_api_bridge.controllers.website_sale import (
    StorefrontCart,
    StorefrontWebsiteSale,
)
from odoo.exceptions import UserError
from odoo.http import request, route


def _contact_to_order():
    return bool(request.website and request.website.x_contact_to_order)


class ContactOrderCart(StorefrontCart):
    @route()
    def add_to_cart(self, *args, **kwargs):
        # The product page no longer offers the cart; refuse direct calls too.
        if _contact_to_order():
            raise UserError(request.env._("Please contact us to order."))
        return super().add_to_cart(*args, **kwargs)


class ContactOrderWebsiteSale(StorefrontWebsiteSale):
    @route()
    def shop_checkout(self, try_skip_step=None, **query_params):
        if _contact_to_order():
            return request.redirect("/shop/cart")
        return super().shop_checkout(try_skip_step=try_skip_step, **query_params)

    @route()
    def shop_payment(self, **post):
        if _contact_to_order():
            return request.redirect("/shop/cart")
        return super().shop_payment(**post)
