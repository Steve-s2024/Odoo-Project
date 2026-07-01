import json

from odoo.http import Controller, content_disposition, request, route


class StockSubwarehouseImportTemplateController(Controller):
    def _parse_ids(self, ids):
        return [
            int(record_id)
            for record_id in (ids or "").split(",")
            if record_id.strip().isdigit()
        ]

    def _records_from_request(self, model_name, ids="", domain="[]"):
        Model = request.env[model_name]
        record_ids = self._parse_ids(ids)
        if record_ids:
            return Model.browse(record_ids).exists()
        try:
            parsed_domain = json.loads(domain or "[]")
        except json.JSONDecodeError:
            parsed_domain = []
        return Model.search(parsed_domain)

    @route(
        "/stock_subwarehouse_hierarchy/import_template/mrp_production.xlsx",
        type="http",
        auth="user",
    )
    def download_mrp_production_import_template(self, **kwargs):
        content = request.env["mrp.production"]._generate_dynamic_import_template_xlsx()
        headers = [
            (
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            (
                "Content-Disposition",
                content_disposition("manufacturing_import_template_zh.xlsx"),
            ),
        ]
        return request.make_response(content, headers=headers)

    @route(
        "/stock_subwarehouse_hierarchy/import_template/product_template.xlsx",
        type="http",
        auth="user",
    )
    def download_product_template_import_template(self, **kwargs):
        content = request.env["product.template"]._generate_dynamic_product_import_template_xlsx()
        headers = [
            (
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            (
                "Content-Disposition",
                content_disposition("product_import_template_zh.xlsx"),
            ),
        ]
        return request.make_response(content, headers=headers)

    @route(
        "/stock_subwarehouse_hierarchy/import_template/sale_order.xlsx",
        type="http",
        auth="user",
    )
    def download_sale_order_import_template(self, **kwargs):
        content = request.env["sale.order"]._generate_sale_order_import_template_xlsx()
        headers = [
            (
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            (
                "Content-Disposition",
                content_disposition("sale_order_import_template_zh.xlsx"),
            ),
        ]
        return request.make_response(content, headers=headers)

    @route(
        "/stock_subwarehouse_hierarchy/export/product_template.xlsx",
        type="http",
        auth="user",
    )
    def export_product_template_import_format(self, ids="", domain="[]", **kwargs):
        records = self._records_from_request("product.template", ids=ids, domain=domain)
        content = records._generate_dynamic_product_export_xlsx()
        headers = [
            (
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            (
                "Content-Disposition",
                content_disposition("product_export_import_format_zh.xlsx"),
            ),
        ]
        return request.make_response(content, headers=headers)

    @route(
        "/stock_subwarehouse_hierarchy/export/mrp_production.xlsx",
        type="http",
        auth="user",
    )
    def export_mrp_production_import_format(self, ids="", domain="[]", **kwargs):
        records = self._records_from_request("mrp.production", ids=ids, domain=domain)
        content = records._generate_dynamic_export_xlsx()
        headers = [
            (
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            (
                "Content-Disposition",
                content_disposition("manufacturing_export_import_format_zh.xlsx"),
            ),
        ]
        return request.make_response(content, headers=headers)

    @route(
        "/stock_subwarehouse_hierarchy/export/sale_order.xlsx",
        type="http",
        auth="user",
    )
    def export_sale_order_import_format(self, ids="", domain="[]", **kwargs):
        records = self._records_from_request("sale.order", ids=ids, domain=domain)
        content = records._generate_sale_order_export_xlsx()
        headers = [
            (
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            (
                "Content-Disposition",
                content_disposition("sale_order_export_import_format_zh.xlsx"),
            ),
        ]
        return request.make_response(content, headers=headers)
