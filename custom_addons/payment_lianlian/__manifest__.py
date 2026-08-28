{
    "name": "Payment Provider: LianLian Checkout",
    "version": "19.0.1.0.9",
    "category": "Accounting/Payment Providers",
    "summary": "Accept card and local payments through LianLian hosted Checkout.",
    "depends": ["payment", "account_payment", "sale"],
    "data": [
        "views/payment_lianlian_templates.xml",
        "views/payment_provider_views.xml",
        "data/payment_method_data.xml",
        "data/payment_provider_data.xml",
        "data/refund_reconciliation_cron.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "author": "Local",
    "installable": True,
    "license": "LGPL-3",
}
