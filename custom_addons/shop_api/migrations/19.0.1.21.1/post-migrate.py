def migrate(cr, version):
    """Register native auth v2 when upgrading an existing ERP database."""
    from odoo.api import Environment

    env = Environment(cr, 1, {})
    env["shop.api.endpoint"]._ensure_builtin_endpoints()
