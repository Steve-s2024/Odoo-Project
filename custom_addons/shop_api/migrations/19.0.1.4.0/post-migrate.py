def migrate(cr, version):
    from odoo import Command
    from odoo.api import Environment, SUPERUSER_ID

    env = Environment(cr, SUPERUSER_ID, {})
    client = env["shop.api.client"].search([
        ("code", "=", "separated_shop"),
        ("active", "=", True),
    ], limit=1)
    scopes = env["shop.api.scope"].search([
        ("code", "in", ["site:read", "checkout:write"]),
    ])
    if client and scopes:
        client.write({"scope_ids": [Command.link(scope.id) for scope in scopes]})
