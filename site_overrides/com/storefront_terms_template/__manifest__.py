{
    "name": "SUN Storefront Terms of Service",
    "summary": "Bilingual consumer terms for the SUN online shop",
    "version": "19.0.1.0.0",
    "category": "Website/Website",
    "author": "SUN",
    "license": "LGPL-3",
    "depends": ["account", "website_sale"],
    "data": [
        "views/terms_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "storefront_terms_template/static/src/scss/terms.scss",
        ],
    },
    "installable": True,
}
