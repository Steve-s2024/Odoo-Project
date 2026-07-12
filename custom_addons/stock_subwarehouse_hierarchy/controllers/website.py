from urllib.parse import urlsplit, urlunsplit

from odoo.addons.website.controllers.main import Website
from odoo.http import request, route


class WebsiteLanguageNoPrefix(Website):
    @route("/stores", type="http", auth="public", website=True, sitemap=True)
    def stores(self, **kwargs):
        stores = [
            {
                "name": "思安奇北京旗舰店",
                "english_name": "SUN Beijing Flagship Store",
                "city": "北京",
                "address": "朝阳区雪具大道 88 号 A 座 1 层",
                "hours": "周一至周日 10:00-21:00",
                "phone": "010-6000-1888",
                "services": "新品试穿、雪鞋热塑、租赁归还、售后保养",
            },
            {
                "name": "思安奇张家口崇礼店",
                "english_name": "SUN Chongli Distributor",
                "city": "张家口",
                "address": "崇礼区雪场路 19 号游客中心旁",
                "hours": "雪季 08:30-22:00 / 非雪季 10:00-18:00",
                "phone": "0313-660-2026",
                "services": "雪场提货、快速换码、租赁套装、团队采购",
            },
            {
                "name": "思安奇上海体验店",
                "english_name": "SUN Shanghai Experience Store",
                "city": "上海",
                "address": "静安区运动生活广场 3 层 305",
                "hours": "周二至周日 11:00-20:00",
                "phone": "021-5100-7788",
                "services": "装备咨询、尺码测量、线上订单自提、维修登记",
            },
        ]
        return request.render(
            "stock_subwarehouse_hierarchy.stores_page",
            {
                "stores": stores,
                "additional_title": "门店(stores)",
            },
        )

    @route("/website/shop_lang/<lang>", type="http", auth="public", website=True, multilang=False)
    def change_shop_lang(self, lang, r="/", **kwargs):
        lang_code = request.env["res.lang"]._get_data(url_code=lang).code or lang
        redirect_url = self._strip_frontend_language_prefix(r or "/")
        response = request.redirect(redirect_url, local=False)
        response.set_cookie("stock_shop_lang", lang_code)
        response.delete_cookie("frontend_lang")
        return response

    @route("/website/lang/<lang>", type="http", auth="public", website=True, multilang=False)
    def change_lang(self, lang, r="/", **kwargs):
        if lang == "default":
            lang = request.website.default_lang_id.url_code
        lang_code = request.env["res.lang"]._get_data(url_code=lang).code or lang
        request.update_context(lang=lang_code)

        redirect_url = self._strip_frontend_language_prefix(r or "/")
        response = request.redirect(redirect_url, local=False)
        response.set_cookie("stock_shop_lang", lang_code)
        response.delete_cookie("frontend_lang")
        return response

    def _strip_frontend_language_prefix(self, url):
        parsed_url = urlsplit(url or "/")
        path = parsed_url.path or "/"
        path_parts = path.lstrip("/").split("/", 1)
        url_codes = {
            language.url_code
            for language in request.website.language_ids
            if language.url_code
        }
        if path_parts and path_parts[0] in url_codes:
            path = "/" + (path_parts[1] if len(path_parts) > 1 else "")
            if path == "/":
                path = "/"
        return urlunsplit(("", "", path, parsed_url.query, parsed_url.fragment))
