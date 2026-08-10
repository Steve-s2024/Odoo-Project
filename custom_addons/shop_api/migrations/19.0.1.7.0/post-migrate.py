def migrate(cr, version):
    from odoo.api import Environment, SUPERUSER_ID

    env = Environment(cr, SUPERUSER_ID, {})
    env["shop.api.configuration"]._ensure_default_configuration()
