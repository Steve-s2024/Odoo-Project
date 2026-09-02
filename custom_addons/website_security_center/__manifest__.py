{
    "name": "网站安全中心",
    "summary": "商城 API、订单与支付安全监控和事件处置",
    "version": "19.0.1.1.0",
    "category": "Administration/Security",
    "author": "Local",
    "license": "LGPL-3",
    "depends": ["shop_api", "mail"],
    "data": [
        "security/website_security_security.xml",
        "security/ir.model.access.csv",
        "data/website_security_data.xml",
        "views/website_security_views.xml",
        "views/website_security_menu.xml",
    ],
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}
