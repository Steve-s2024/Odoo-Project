import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { STATIC_ACTIONS_GROUP_NUMBER } from "@web/search/action_menus/action_menus";

import { Component } from "@odoo/owl";

const cogMenuRegistry = registry.category("cogMenu");

const TEMPLATE_EXPORT_ROUTES = {
    "product.template": "/stock_subwarehouse_hierarchy/export/product_template.xlsx",
    "mrp.bom": "/stock_subwarehouse_hierarchy/export/mrp_bom.xlsx",
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

class ProductBomImportMenu extends Component {
    static template = "stock_subwarehouse_hierarchy.ProductBomImportMenu";
    static components = { DropdownItem };
    static props = {};

    setup() {
        this.action = useService("action");
    }

    async onImportProductBom() {
        await this.action.doAction({
            type: "ir.actions.client",
            name: "导入产品BOM",
            tag: "import",
            target: "current",
            params: {
                model: "mrp.bom",
                active_model: "mrp.bom",
                context: {},
            },
            context: {},
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

cogMenuRegistry.add("stock-subwarehouse-product-bom-import-menu", {
    Component: ProductBomImportMenu,
    groupNumber: STATIC_ACTIONS_GROUP_NUMBER,
    isDisplayed: (env) =>
        ["kanban", "list"].includes(env.config.viewType) &&
        env.model.root.resModel === "product.template",
}, {
    sequence: 12,
});
