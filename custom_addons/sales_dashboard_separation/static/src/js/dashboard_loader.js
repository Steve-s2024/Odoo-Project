/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import {
    DashboardLoader,
    Status,
} from "@spreadsheet_dashboard/bundle/dashboard_action/dashboard_loader_service";


const SEPARATED_SALES_DASHBOARD_NAMES = new Set([
    "销售",
    "Sales",
    "外部销售",
    "External Sales",
]);

function isSeparatedSalesDashboard(dashboard) {
    return Boolean(
        dashboard && SEPARATED_SALES_DASHBOARD_NAMES.has(dashboard.data?.name)
    );
}

function resetDashboard(dashboard) {
    return {
        data: dashboard.data,
        status: Status.NotLoaded,
    };
}

patch(DashboardLoader.prototype, {
    restoreFromState(state) {
        super.restoreFromState(...arguments);
        for (const [dashboardId, dashboard] of Object.entries(this.dashboards)) {
            if (isSeparatedSalesDashboard(dashboard)) {
                this.dashboards[dashboardId] = resetDashboard(dashboard);
            }
        }
    },

    activateDashboard(dashboardId) {
        const dashboard = this.dashboards[dashboardId];
        if (
            this.activeDashboardId !== dashboardId &&
            isSeparatedSalesDashboard(dashboard) &&
            dashboard.status !== Status.NotLoaded
        ) {
            this.dashboards[dashboardId] = resetDashboard(dashboard);
        }
        return super.activateDashboard(...arguments);
    },
});
