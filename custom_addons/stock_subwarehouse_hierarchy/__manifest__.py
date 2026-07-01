{
    "name": "库存子仓库层级",
    "summary": "使用库存库位树跟踪仓库和子仓库。",
    "version": "19.0.1.0.0",
    "category": "库存/库存",
    "author": "Local",
    "license": "LGPL-3",
    "depends": ["stock", "web_hierarchy", "mrp", "sale_stock"],
    "data": [
        "security/ir.model.access.csv",
        "views/descendant_inventory_total_views.xml",
        "views/product_attribute_apply_views.xml",
        "views/import_format_export_actions.xml",
        "views/stock_quant_views.xml",
        "views/stock_location_views.xml",
        "views/stock_warehouse_views.xml",
        "views/sale_order_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "stock_subwarehouse_hierarchy/static/src/js/template_format_export_menu.js",
            "stock_subwarehouse_hierarchy/static/src/xml/template_format_export_menu.xml",
        ],
    },
    "installable": True,
    "application": False,
}
