{
    "name": "销售与外部销售仪表盘隔离",
    "summary": "防止销售与外部销售仪表盘复用过期的客户端数据模型",
    "version": "19.0.1.0.1",
    "category": "Sales",
    "author": "Local",
    "license": "LGPL-3",
    "depends": ["stock_subwarehouse_hierarchy", "spreadsheet_dashboard_sale"],
    "data": ["data/dashboard_data.xml"],
    "assets": {
        "web.assets_backend": [
            "sales_dashboard_separation/static/src/js/dashboard_loader.js",
        ],
    },
    "installable": True,
    "application": False,
}
