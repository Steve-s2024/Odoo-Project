def migrate(cr, version):
    from odoo.api import Environment

    env = Environment(cr, 1, {})
    env["shop.api.endpoint"]._ensure_builtin_endpoints()
