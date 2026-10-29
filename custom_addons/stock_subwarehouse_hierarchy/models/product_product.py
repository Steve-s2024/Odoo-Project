from odoo import api, models
from odoo.fields import Domain
from odoo.http import request
from odoo.tools import float_compare


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model
    def _internal_transfer_stocked_product_ids(self, source_location, company=None):
        """Products with positive unreserved quantity below the selected source."""
        source_location = source_location.exists()
        if not source_location or source_location.usage not in ("view", "internal"):
            return []

        quant_domain = [
            ("location_id", "child_of", source_location.id),
            ("location_id.usage", "=", "internal"),
        ]
        company = company.exists() if company else self.env.company
        if company:
            quant_domain.append(("company_id", "=", company.id))

        precision = self.env["decimal.precision"].precision_get("Product Unit")
        grouped = self.env["stock.quant"]._read_group(
            quant_domain,
            ["product_id"],
            ["quantity:sum", "reserved_quantity:sum"],
        )
        return [
            product.id
            for product, quantity, reserved_quantity in grouped
            if product
            and float_compare(
                quantity - reserved_quantity,
                0.0,
                precision_digits=precision,
            ) > 0
        ]

    @api.model
    def _get_internal_transfer_stock_filter_domain(self):
        if not self.env.context.get("internal_transfer_stock_filter"):
            return None

        location_id = self._context_record_id("internal_transfer_source_location_id")
        company_id = self._context_record_id("internal_transfer_company_id")
        source_location = self.env["stock.location"].browse(location_id).exists()
        company = self.env["res.company"].browse(company_id).exists() if company_id else self.env.company
        product_ids = self._internal_transfer_stocked_product_ids(source_location, company)
        return [("id", "in", product_ids)] if product_ids else [("id", "=", 0)]

    def _to_markup_data(self, website):
        """Describe the product to search engines exactly as the page shows it.

        Visible prices follow the page language (English pages show the USD
        price, Chinese pages CNY), and the name is the language's website
        name.  Odoo's default always uses the website currency and the ERP
        (Chinese) name, which makes English pages disagree with their markup.
        """
        markup_data = super()._to_markup_data(website)
        lang_code = (request and request.lang and request.lang.code) or ""
        is_english = lang_code.lower().startswith("en")
        template = self.product_tmpl_id
        markup_data["name"] = template._get_website_display_name(is_english=is_english)
        if is_english:
            if template.x_website_usd_price:
                markup_data["offers"].update({
                    "price": template.x_website_usd_price,
                    "priceCurrency": "USD",
                })
            else:
                # The page says "Price on request"; a CNY offer would contradict it.
                markup_data.pop("offers", None)
        return markup_data

    @api.model
    def _context_record_id(self, key):
        value = self.env.context.get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else False
        return value or False

    @api.model
    def _search(
        self,
        domain,
        offset=0,
        limit=None,
        order=None,
        *,
        active_test=True,
        bypass_access=False,
    ):
        """Apply the same descendant-stock filter to autocomplete and Search More."""
        stock_domain = self._get_internal_transfer_stock_filter_domain()
        if stock_domain is not None:
            domain = list(Domain(domain or []) & Domain(stock_domain))
        return super()._search(
            domain=domain,
            offset=offset,
            limit=limit,
            order=order,
            active_test=active_test,
            bypass_access=bypass_access,
        )
