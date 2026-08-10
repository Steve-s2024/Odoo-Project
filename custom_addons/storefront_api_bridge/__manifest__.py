{
    "name": "Storefront ERP API Bridge",
    "summary": "Keep Odoo website presentation local while ERP remains transaction authority",
    "version": "19.0.2.3.2",
    "category": "Website/Website",
    "author": "Local",
    "license": "LGPL-3",
    "depends": ["stock_subwarehouse_hierarchy", "website_sale"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/payment_templates.xml",
        "views/remote_portal_templates.xml",
        "views/product_language_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "storefront_api_bridge/static/src/js/mandatory_erp_confirmation.js",
            "storefront_api_bridge/static/src/scss/mandatory_erp_confirmation.scss",
        ],
    },
    "installable": True,
}
