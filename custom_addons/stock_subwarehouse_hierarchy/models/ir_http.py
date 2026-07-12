import werkzeug

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _match(cls, path):
        if path.rstrip("/") == "/shop" and request.httprequest.method != "POST":
            werkzeug.exceptions.abort(request.redirect("/product-categories"))
        return super()._match(path)

    @classmethod
    def _sun_format_website_title(cls, title):
        is_english = request.lang and request.lang.code == "en_US"
        if not title:
            return "Home | SUN" if is_english else "主页(home) | 思安奇SUN"

        page_title, separator, _brand = title.partition(" | ")
        page_title = (page_title or "").strip()
        if not page_title:
            page_title = "Home" if is_english else "主页"
        english_title_map = {
            "home": "Home",
            "shop": "Shop",
            "cart": "Cart",
            "shopping cart": "Shopping Cart",
            "checkout": "Checkout",
            "payment": "Payment",
            "product categories": "Product Categories",
            "ski products": "Ski Products",
            "ski products sale": "Ski Products Sale",
            "snowboard products": "Snowboard Products",
            "other products": "Other Products",
            "ski items": "Ski Products",
            "snowboard items": "Snowboard Products",
            "other items": "Other Products",
            "stores": "Stores",
            "contact us": "Contact Us",
            "contact": "Contact",
        }
        if is_english:
            formatted_page_title = english_title_map.get(page_title.casefold(), page_title)
        elif "(" in page_title and ")" in page_title:
            formatted_page_title = page_title
        else:
            title_map = {
                "home": "主页(home)",
                "shop": "商城(shop)",
                "cart": "购物车(cart)",
                "shopping cart": "购物车(shopping cart)",
                "checkout": "结账(checkout)",
                "payment": "支付(payment)",
                "product categories": "产品分类(product categories)",
                "ski products": "双板产品(ski products)",
                "ski products sale": "双板特卖(ski products sale)",
                "snowboard products": "单板产品(snowboard products)",
                "other products": "其他产品(other products)",
                "ski items": "双板产品(ski items)",
                "snowboard items": "单板产品(snowboard items)",
                "other items": "其他产品(other items)",
                "stores": "门店(stores)",
                "contact us": "联系我们(contact us)",
                "contact": "联系我们(contact)",
            }
            formatted_page_title = title_map.get(page_title.casefold(), page_title)
        if separator:
            return f"{formatted_page_title} | {'SUN' if is_english else '思安奇SUN'}"
        return f"{formatted_page_title} | {'SUN' if is_english else '思安奇SUN'}"
