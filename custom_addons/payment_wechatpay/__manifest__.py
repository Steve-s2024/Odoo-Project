{
    "name": "Payment Provider: WeChat Pay",
    "version": "19.0.1.0.0",
    "category": "Accounting/Payment Providers",
    "summary": "Accept WeChat Pay Native QR payments in Odoo.",
    "depends": ["payment", "account_payment"],
    "data": [
        "views/payment_wechatpay_templates.xml",
        "views/payment_provider_views.xml",
        "data/payment_method_data.xml",
        "data/payment_provider_data.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "author": "Local",
    "installable": True,
    "license": "LGPL-3",
}
