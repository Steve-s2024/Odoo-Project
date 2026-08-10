{
    "name": "Payment Provider: Alipay",
    "version": "19.0.1.1.0",
    "category": "Accounting/Payment Providers",
    "summary": "Accept Alipay QR payments with signed asynchronous notifications.",
    "depends": ["payment", "account_payment"],
    "external_dependencies": {"python": ["alipay"]},
    "data": [
        "views/payment_alipay_templates.xml",
        "views/payment_provider_views.xml",
        "data/payment_provider_data.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "author": "Local",
    "installable": True,
    "license": "LGPL-3",
}
