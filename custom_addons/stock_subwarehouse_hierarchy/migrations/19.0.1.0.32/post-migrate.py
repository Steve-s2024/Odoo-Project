from odoo import SUPERUSER_ID, api, fields


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Populate the delivery queue for already-paid website orders. The model
    # method is deliberately idempotent and creates reviewer activities only
    # for orders that genuinely enter the waiting queue.
    env["sale.order"].sudo().search([
        ("website_id", "!=", False),
        ("x_website_delivery_state", "=", False),
        ("transaction_ids.state", "=", "done"),
    ])._queue_paid_website_delivery()

    refund_model = env["stock.subwarehouse.website.refund.request"].sudo()
    for refund_request in refund_model.search([
        ("return_required", "=", True),
        ("x_return_delivery_state", "=", False),
    ]):
        active_pickings = refund_request.return_picking_ids.filtered(
            lambda picking: picking.state != "cancel"
        )
        completed_dates = [
            date_done for date_done in active_pickings.mapped("date_done") if date_done
        ]
        if active_pickings and all(picking.state == "done" for picking in active_pickings):
            refund_request.write({
                "x_return_delivery_state": "delivered",
                "x_return_delivery_started_at": min(completed_dates)
                if completed_dates else fields.Datetime.now(),
                "x_return_delivered_at": max(completed_dates)
                if completed_dates else fields.Datetime.now(),
            })
        elif active_pickings:
            refund_request.x_return_delivery_state = "awaiting_delivery"
