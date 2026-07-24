import json

from odoo import _
from odoo.addons.website.controllers.main import QueryURL
from odoo.addons.website_sale.controllers.cart import Cart
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.exceptions import UserError
from odoo.http import request, route


class WebsiteCheckoutLanguageMixin:
    @staticmethod
    def _is_english_checkout():
        return bool(request.lang and request.lang.code == "en_US")

    def _sync_website_checkout_language(self):
        order = request.cart
        if not order and (last_order_id := request.session.get("sale_last_order_id")):
            order = request.env["sale.order"].sudo().browse(last_order_id).exists()
        # Do not rewrite a completed financial order merely because its confirmation
        # page is viewed in another language. Draft carts remain fully switchable.
        if order and order.state == "draft":
            order.sudo()._apply_website_checkout_language(self._is_english_checkout())


class WebsiteCartStockSource(WebsiteCheckoutLanguageMixin, Cart):
    @route()
    def cart(self, id=None, access_token=None, revive_method="", **post):
        self._sync_website_checkout_language()
        return super().cart(
            id=id,
            access_token=access_token,
            revive_method=revive_method,
            **post,
        )


class WebsiteSaleStockSource(WebsiteCheckoutLanguageMixin, WebsiteSale):

    @route()
    def shop_checkout(self, try_skip_step=None, **query_params):
        self._sync_website_checkout_language()
        return super().shop_checkout(try_skip_step=try_skip_step, **query_params)

    @route()
    def shop_address(
        self, partner_id=None, address_type="billing", use_delivery_as_billing=None, **query_params
    ):
        self._sync_website_checkout_language()
        return super().shop_address(
            partner_id=partner_id,
            address_type=address_type,
            use_delivery_as_billing=use_delivery_as_billing,
            **query_params,
        )

    def _prepare_address_form_values(self, *args, order_sudo=False, **kwargs):
        values = super()._prepare_address_form_values(*args, order_sudo=order_sudo, **kwargs)
        if not order_sudo:
            return values
        is_english = self._is_english_checkout()
        SaleOrder = request.env["sale.order"]
        values["countries"] = values["countries"].filtered(
            lambda country: SaleOrder._is_website_checkout_country_allowed(country, is_english)
        )
        values["x_website_checkout_is_english"] = is_english
        values["x_website_destination_warning"] = SaleOrder._website_checkout_country_message(is_english)
        return values

    @route()
    def shop_address_submit(
        self,
        partner_id=None,
        address_type="billing",
        use_delivery_as_billing=None,
        callback=None,
        **form_data,
    ):
        country_id = form_data.get("country_id")
        if country_id:
            country = request.env["res.country"].sudo().browse(int(country_id)).exists()
            if country and not request.env["sale.order"]._is_website_checkout_country_allowed(
                country, self._is_english_checkout()
            ):
                return json.dumps({
                    "invalid_fields": ["country_id"],
                    "messages": [request.env["sale.order"]._website_checkout_country_message(
                        self._is_english_checkout()
                    )],
                })
        return super().shop_address_submit(
            partner_id=partner_id,
            address_type=address_type,
            use_delivery_as_billing=use_delivery_as_billing,
            callback=callback,
            **form_data,
        )

    @route()
    def shop_payment(self, **post):
        self._sync_website_checkout_language()
        return super().shop_payment(**post)

    @route()
    def shop_payment_confirmation(self, **post):
        self._sync_website_checkout_language()
        return super().shop_payment_confirmation(**post)

    @route()
    def shop(self, page=0, category=None, search="", min_price=0.0, max_price=0.0, tags="", **post):
        normalized_path = request.httprequest.path.rstrip("/")
        path_parts = normalized_path.strip("/").split("/")
        if path_parts[-1:] == ["shop"] and len(path_parts) <= 2:
            language_prefix = ""
            if request.lang and request.lang != request.website.default_lang_id:
                language_prefix = request.lang.url_code
            return request.redirect(
                f"/{language_prefix}/collections" if language_prefix == "en" else "/collections"
            )
        return super().shop(
            page=page,
            category=category,
            search=search,
            min_price=min_price,
            max_price=max_price,
            tags=tags,
            **post,
        )

    @route(
        ["/ski-products", "/ski-products/page/<int:page>"],
        type="http",
        auth="public",
        website=True,
    )
    def ski_products(self, page=0, search="", min_price=0.0, max_price=0.0, tags="", **post):
        post["x_shop_product_family"] = "ski"
        post["x_segmented_shop_page"] = True
        post["x_segmented_shop_path"] = "/ski-products"
        response = self.shop(
            page=page,
            search=search,
            min_price=min_price,
            max_price=max_price,
            tags=tags,
            **post,
        )
        return self._with_segmented_shop_template(
            response,
            "stock_subwarehouse_hierarchy.ski_products_page",
        )

    @route(
        ["/ski-products-sale", "/ski-products-sale/page/<int:page>"],
        type="http",
        auth="public",
        website=True,
    )
    def ski_products_sale(self, page=0, search="", min_price=0.0, max_price=0.0, tags="", **post):
        return self._render_custom_product_family_page(
            family="ski",
            path="/ski-products-sale",
            builder_key="ski_sale",
            page=page,
            search=search,
            zh_title="滑雪产品特卖",
            en_title="Ski Products Sale",
            zh_subtitle="双板、雪鞋、雪杖与滑雪装备精选",
            en_subtitle="Selected skis, ski boots, poles, and alpine gear.",
        )

    @route(
        ["/_deleted-snowboard-products", "/_deleted-snowboard-products/page/<int:page>"],
        type="http",
        auth="public",
        website=True,
    )
    def snowboard_products(self, page=0, search="", min_price=0.0, max_price=0.0, tags="", **post):
        return self._render_custom_product_family_page(
            family="snowboard",
            path="/_deleted-snowboard-products",
            builder_key="snowboard",
            page=page,
            search=search,
            zh_title="单板产品",
            en_title="Snowboard Products",
            zh_subtitle="单板、单板鞋与相关装备",
            en_subtitle="Snowboards, snowboard boots, and related gear.",
        )

    @route(
        ["/_deleted-other-products", "/_deleted-other-products/page/<int:page>"],
        type="http",
        auth="public",
        website=True,
    )
    def other_products(self, page=0, search="", min_price=0.0, max_price=0.0, tags="", **post):
        return self._render_custom_product_family_page(
            family="other",
            path="/_deleted-other-products",
            builder_key="other",
            page=page,
            search=search,
            zh_title="其他产品",
            en_title="Other Products",
            zh_subtitle="护具、雪镜、手套、服装与更多配件",
            en_subtitle="Protection, goggles, gloves, apparel, and accessories.",
        )

    def _render_custom_product_family_page(
        self,
        family,
        path,
        builder_key,
        page=0,
        search="",
        zh_title="",
        en_title="",
        zh_subtitle="",
        en_subtitle="",
    ):
        Product = request.env["product.template"].sudo()
        products = Product.search([
            ("sale_ok", "=", True),
            ("website_published", "=", True),
        ], order="name, default_code, id")
        products = products._filter_shop_products_by_family(family)._get_shop_grouped_products()
        if search:
            search_text = search.casefold()
            products = products.filtered(
                lambda product: search_text in (product.name or "").casefold()
                or search_text in (product.default_code or "").casefold()
            )

        page_size = 24
        pager = request.website.pager(
            url=path,
            total=len(products),
            page=page,
            step=page_size,
            scope=5,
            url_args={"search": search} if search else {},
        )
        current_products = products[pager["offset"]:pager["offset"] + page_size]
        is_english = request.lang and request.lang.code == "en_US"
        return request.render("stock_subwarehouse_hierarchy.custom_product_family_page", {
            "products": current_products,
            "product_count": len(products),
            "pager": pager,
            "search": search,
            "page_path": path,
            "builder_key": builder_key,
            "page_title": en_title if is_english else zh_title,
            "page_subtitle": en_subtitle if is_english else zh_subtitle,
            "is_english": is_english,
        })

    def _with_segmented_shop_template(self, response, template):
        if getattr(response, "template", None):
            response.template = template
            response.qcontext["response_template"] = template
        return response

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
            segmented_path = kwargs.get("x_segmented_shop_path")
            query_kwargs = {
                key: value
                for key, value in kwargs.items()
                if key not in {
                    "x_shop_product_family",
                    "x_segmented_shop_page",
                    "x_segmented_shop_path",
                    "search",
                    "min_price",
                    "max_price",
                    "order",
                    "tags",
                }
            }
            additional_values["keep"] = QueryURL(
                segmented_path,
                **self._shop_get_query_url_kwargs(
                    values.get("search") or "",
                    values.get("min_price") or 0,
                    values.get("max_price") or 0,
                    order=values.get("order") or kwargs.get("order"),
                    tags=kwargs.get("tags"),
                    **query_kwargs,
                ),
            )
            additional_values["shop_path"] = segmented_path
        return additional_values

    def _get_shop_payment_errors(self, order):
        errors = super()._get_shop_payment_errors(order)
        if order and order._has_deliverable_products():
            country = order.partner_shipping_id.country_id or order.partner_invoice_id.country_id
            if not order._is_website_checkout_country_allowed(country, self._is_english_checkout()):
                errors.append((
                    _("Delivery destination is not available"),
                    order._website_checkout_country_message(self._is_english_checkout()),
                ))
        if order and order.state in ("draft", "sent"):
            try:
                order.sudo()._prepare_website_stock_for_payment()
            except UserError as error:
                errors.append((_("库存不足"), str(error)))
        return errors
