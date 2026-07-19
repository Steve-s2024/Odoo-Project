from werkzeug.exceptions import NotFound

from odoo.http import content_disposition, request, route
from odoo.addons.portal.controllers.portal import CustomerPortal


class WebsitePaymentReceipt(CustomerPortal):
    @route(
        "/shop/payment/receipt/<int:order_id>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
    )
    def payment_receipt(self, order_id, access_token=None, **kwargs):
        order = self._document_check_access("sale.order", order_id, access_token)
        payment = order._get_website_payment_receipt()
        if not payment:
            raise NotFound()

        pdf, _report_type = request.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "stock_subwarehouse_hierarchy.action_report_website_payment_receipt",
            res_ids=payment.ids,
        )
        filename = f"payment-receipt-{payment.name}.pdf"
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
                ("Content-Length", str(len(pdf))),
            ("Content-Disposition", content_disposition(filename)),
        ])
