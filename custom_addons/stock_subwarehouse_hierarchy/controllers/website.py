from urllib.parse import urlsplit, urlunsplit

from odoo.addons.website.controllers.main import Website
from odoo.http import request, route


class WebsiteLanguageNoPrefix(Website):
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
