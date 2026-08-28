from odoo import http
from odoo.addons.spreadsheet_dashboard.controllers.dashboards_controllers import (
    DashboardDataRoute,
)


class SeparatedSalesDashboardDataRoute(DashboardDataRoute):
    @http.route()
    def get_dashboard_data(self, dashboard):
        response = super().get_dashboard_data(dashboard)
        xmlids = {
            dashboard.get_external_id().get(dashboard.id),
        }
        if xmlids & {
            "spreadsheet_dashboard_sale.spreadsheet_dashboard_sales",
            "stock_subwarehouse_hierarchy.spreadsheet_dashboard_external_sales",
        }:
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Vary"] = "Cookie"
        return response
