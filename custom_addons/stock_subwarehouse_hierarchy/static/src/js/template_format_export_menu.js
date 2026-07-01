import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { STATIC_ACTIONS_GROUP_NUMBER } from "@web/search/action_menus/action_menus";

import { Component } from "@odoo/owl";

const cogMenuRegistry = registry.category("cogMenu");

const TEMPLATE_EXPORT_ROUTES = {
    "product.template": "/stock_subwarehouse_hierarchy/export/product_template.xlsx",
    "mrp.production": "/stock_subwarehouse_hierarchy/export/mrp_production.xlsx",
    "sale.order": "/stock_subwarehouse_hierarchy/export/sale_order.xlsx",
};

class TemplateFormatExportMenu extends Component {
    static template = "stock_subwarehouse_hierarchy.TemplateFormatExportMenu";
    static components = { DropdownItem };
    static props = {};

    async onExportTemplateFormat() {
        const root = this.env.model.root;
        const route = TEMPLATE_EXPORT_ROUTES[root.resModel];
        const selectedIds = !root.isDomainSelected && root.selection.length
            ? root.selection.map((record) => record.resId).join(",")
            : "";
        await download({
            url: route,
            data: {
                ids: selectedIds,
                domain: JSON.stringify(root.domain || []),
            },
        });
    }
}

const templateFormatExportItem = {
    Component: TemplateFormatExportMenu,
    groupNumber: STATIC_ACTIONS_GROUP_NUMBER,
    isDisplayed: (env) =>
        ["kanban", "list"].includes(env.config.viewType) &&
        Object.keys(TEMPLATE_EXPORT_ROUTES).includes(env.model.root.resModel),
};

cogMenuRegistry.add("stock-subwarehouse-template-format-export-menu", templateFormatExportItem, {
    sequence: 11,
});

