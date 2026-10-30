{
    "name": "SUN Storefront Contact to Order",
    # While ERP stock is not maintained and no payment provider is live,
    # customers order by contacting us.  Turn the switch off under
    # Website > Configuration > Websites > Contact to order to restore the cart.
    "summary": "Replace the online cart with phone, email and WeChat ordering",
    "version": "19.0.1.1.0",
    "category": "Website/Website",
    "author": "SUN",
    "license": "LGPL-3",
    "depends": ["website_sale", "stock_subwarehouse_hierarchy", "storefront_api_bridge"],
    "data": [
        "views/website_views.xml",
        "views/contact_order_templates.xml",
    ],
    "installable": True,
}
