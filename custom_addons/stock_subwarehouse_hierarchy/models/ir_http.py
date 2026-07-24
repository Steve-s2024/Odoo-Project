import werkzeug

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _match(cls, path):
        raw_path = request.httprequest.path.rstrip("/")
        if raw_path == "/en/product-categories" and request.httprequest.method != "POST":
            werkzeug.exceptions.abort(request.redirect("/en/collections"))
        if path.rstrip("/") == "/shop" and request.httprequest.method != "POST":
            werkzeug.exceptions.abort(request.redirect("/collections"))
        return super()._match(path)

    @classmethod
    def _sun_format_website_title(cls, title):
        is_english = request.lang and request.lang.code == "en_US"
        if not title:
            return "Home | SUN" if is_english else "主页 | 思安奇"

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
            "product categories": "Collections",
            "collections": "Collections",
            "details": "Details",
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
        else:
            if "(" in page_title and ")" in page_title:
                page_title = page_title.split("(", 1)[0].strip()
            title_map = {
                "home": "主页",
                "shop": "商城",
                "cart": "购物车",
                "shopping cart": "购物车",
                "checkout": "结账",
                "payment": "支付",
                "product categories": "产品系列",
                "产品分类": "产品系列",
                "产品系列": "产品系列",
                "collections": "产品系列",
                "details": "详情",
                "ski products": "双板产品",
                "ski products sale": "双板特卖",
                "snowboard products": "单板产品",
                "other products": "其他产品",
                "ski items": "双板产品",
                "snowboard items": "单板产品",
                "other items": "其他产品",
                "stores": "门店",
                "contact us": "联系我们",
                "contact": "联系我们",
            }
            formatted_page_title = title_map.get(page_title.casefold(), page_title)
        if separator:
            return f"{formatted_page_title} | {'SUN' if is_english else '思安奇'}"
        return f"{formatted_page_title} | {'SUN' if is_english else '思安奇'}"
