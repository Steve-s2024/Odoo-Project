def migrate(cr, version):
    from odoo.api import Environment, SUPERUSER_ID

    env = Environment(cr, SUPERUSER_ID, {})
    env["shop.api.configuration"]._ensure_default_configuration()
    env["shop.api.scope"]._ensure_builtin_scopes()
    env["shop.api.endpoint"]._ensure_builtin_endpoints()
    env["shop.api.event.type"]._ensure_builtin_event_types()
