def migrate(cr, version):
    from odoo.api import Environment, SUPERUSER_ID

    env = Environment(cr, SUPERUSER_ID, {})
    env["shop.api.endpoint"]._ensure_builtin_endpoints()
    endpoint = env["shop.api.endpoint"].search([
        ("code", "=", "customer_authenticate"),
    ], limit=1)
    if endpoint:
        endpoint.write({
            "log_request_body": False,
            "log_response_body": False,
        })
