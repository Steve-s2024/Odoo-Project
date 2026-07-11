from odoo import _
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.exceptions import UserError
from odoo.http import route


class WebsiteSaleStockSource(WebsiteSale):
    @route(
        ["/ski-products", "/ski-products/page/<int:page>"],
        type="http",
        auth="public",
        website=True,
    )
    def ski_products(self, page=0, search="", min_price=0.0, max_price=0.0, tags="", **post):
        post["x_shop_product_family"] = "ski"
        post["x_segmented_shop_page"] = True
        return self.shop(
            page=page,
            search=search,
            min_price=min_price,
            max_price=max_price,
            tags=tags,
            **post,
        )

    @route(
        ["/snowboard-products", "/snowboard-products/page/<int:page>"],
        type="http",
        auth="public",
        website=True,
    )
    def snowboard_products(self, page=0, search="", min_price=0.0, max_price=0.0, tags="", **post):
        post["x_shop_product_family"] = "snowboard"
        post["x_segmented_shop_page"] = True
        return self.shop(
            page=page,
            search=search,
            min_price=min_price,
            max_price=max_price,
            tags=tags,
            **post,
        )

    @route(
        ["/other-products", "/other-products/page/<int:page>"],
        type="http",
        auth="public",
        website=True,
    )
    def other_products(self, page=0, search="", min_price=0.0, max_price=0.0, tags="", **post):
        post["x_shop_product_family"] = "other"
        post["x_segmented_shop_page"] = True
        return self.shop(
            page=page,
            search=search,
            min_price=min_price,
            max_price=max_price,
            tags=tags,
            **post,
        )

    def _shop_lookup_products(self, options, post, search, website):
        fuzzy_search_term, _product_count, search_result = super()._shop_lookup_products(
            options,
            post,
            search,
            website,
        )
        grouped_products = search_result._get_shop_grouped_products()
        product_family = post.get("x_shop_product_family")
        if product_family:
            grouped_products = grouped_products._filter_shop_products_by_family(product_family)
        return fuzzy_search_term, len(grouped_products), grouped_products

    def _get_additional_shop_values(self, values, **kwargs):
        additional_values = super()._get_additional_shop_values(values, **kwargs)
        attributes = values.get("attributes")
        if attributes:
            additional_values["attributes"] = attributes.filtered(
                lambda attribute: not attribute.x_apply_to_all_products
            )
        additional_values["x_segmented_shop_page"] = bool(kwargs.get("x_segmented_shop_page"))
        if additional_values["x_segmented_shop_page"]:
            additional_values["attributes"] = self.env["product.attribute"]
        return additional_values

    def _get_shop_payment_errors(self, order):
        errors = super()._get_shop_payment_errors(order)
        if order and order.state in ("draft", "sent"):
            try:
                order.sudo()._prepare_website_stock_for_payment()
            except UserError as error:
                errors.append((_("库存不足"), str(error)))
        return errors
