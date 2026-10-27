from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["shop.api.endpoint"]._ensure_builtin_endpoints()
