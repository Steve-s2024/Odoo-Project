from odoo import models
from odoo.http import request


def site_display_name():
    """The Chinese shop (.cn) is named 思安奇 SUN; the international shop SUN Winter Sports."""
    if request and request.httprequest.host.endswith(".cn"):
        return "思安奇 SUN"
    return "SUN Winter Sports"


class WebsiteSeoMetadata(models.AbstractModel):
    _inherit = "website.seo.metadata"

    def _default_website_meta(self):
        meta = super()._default_website_meta()
        if "default_opengraph" in meta:
            meta["default_opengraph"]["og:site_name"] = site_display_name()
        return meta
