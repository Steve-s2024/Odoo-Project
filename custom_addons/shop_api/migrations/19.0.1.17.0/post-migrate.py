from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["shop.api.event.type"]._ensure_builtin_event_types()

    # Preserve the known in-transit state for returns that customers had
    # already marked as shipped before this explicit lifecycle was introduced.
    shipped_returns = env["stock.subwarehouse.website.refund.request"].sudo().search([
        ("return_required", "=", True),
        ("shop_api_return_shipped_at", "!=", False),
        ("x_return_delivery_state", "=", "awaiting_delivery"),
    ])
    for refund_request in shipped_returns:
        refund_request.write({
            "x_return_delivery_state": "delivering",
            "x_return_delivery_started_at": refund_request.shop_api_return_shipped_at,
        })

    # Expire both standalone holds and holds already converted into unpaid
    # orders, then backfill the paid-delivery event queue.
    env["shop.api.reservation"]._cron_expire_reservations()
