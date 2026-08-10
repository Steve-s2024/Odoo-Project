{
    "name": "Shop APIs",
    "summary": "Versioned shop integration APIs, reservations, webhooks, and audit records",
    "version": "19.0.1.13.3",
    "category": "Sales",
    "author": "Local",
    "license": "LGPL-3",
    "depends": [
        "stock_subwarehouse_hierarchy",
    ],
    "data": [
        "security/shop_api_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/shop_api_catalog_views.xml",
        "views/shop_api_operations_views.xml",
        "views/shop_api_menu.xml",
    ],
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}
