import werkzeug

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _match(cls, path):
        if request.httprequest.method != "POST":
            request.is_frontend_multilang = False
        if path.rstrip("/") == "/shop" and request.httprequest.method != "POST":
            werkzeug.exceptions.abort(request.redirect("/product-categories"))
        routing = super()._match(path)
        shop_lang = request.cookies.get("stock_shop_lang")
        if shop_lang:
            lang = request.env["res.lang"]._get_data(code=shop_lang)
            if lang:
                request.lang = lang
                request.update_context(lang=lang.code)
        return routing

    @classmethod
    def _sun_format_website_title(cls, title):
        if not title:
            return "主页(home) | 思安奇SUN"

        page_title, separator, _brand = title.partition(" | ")
        page_title = (page_title or "").strip()
        if not page_title:
            page_title = "主页"
        if "(" in page_title and ")" in page_title:
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
            return f"{formatted_page_title} | 思安奇SUN"
        return f"{formatted_page_title} | 思安奇SUN"
