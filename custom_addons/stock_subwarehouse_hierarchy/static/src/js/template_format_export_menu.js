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

const TEMPLATE_EXPORT_MODELS = new Set(Object.keys(TEMPLATE_EXPORT_ROUTES));

function getSupportedListRoot(env) {
    if (!["kanban", "list"].includes(env.config?.viewType)) {
        return null;
    }
    return env.model?.root || null;
}

// Keep Odoo's generic exporter everywhere else, but replace it on the models
// that have a controlled, import-compatible template export.
const standardExportAllItem = cogMenuRegistry.get("export-all-menu");
cogMenuRegistry.remove("export-all-menu");
cogMenuRegistry.add(
    "export-all-menu",
    {
        ...standardExportAllItem,
        isDisplayed: async (env) => {
            // Odoo's original predicate first excludes graph, pivot,
            // hierarchy, and other controllers that do not expose a list
            // model root.  Preserve that short-circuit before inspecting the
            // model used by our template exporter.
            if (!(await standardExportAllItem.isDisplayed(env))) {
                return false;
            }
            return !TEMPLATE_EXPORT_MODELS.has(env.model?.root?.resModel);
        },
    },
    { sequence: 10 }
);

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

class PartnerChannelExportAllMenu extends Component {
    static template = "stock_subwarehouse_hierarchy.PartnerChannelExportAllMenu";
    static components = { DropdownItem };
    static props = {};

    async onExportAll() {
        const root = this.env.model.root;
        await download({
            url: "/stock_subwarehouse_hierarchy/export/partner_channel.xlsx",
            data: {
                channel: root.context.partner_channel_import_type,
                domain: JSON.stringify(root.domain || []),
            },
        });
    }
}

const templateFormatExportItem = {
    Component: TemplateFormatExportMenu,
    groupNumber: STATIC_ACTIONS_GROUP_NUMBER,
    isDisplayed: (env) => {
        const root = getSupportedListRoot(env);
        return Boolean(
            root &&
            TEMPLATE_EXPORT_MODELS.has(root.resModel) &&
            !root.selection.length
        );
    },
};

cogMenuRegistry.add("stock-subwarehouse-template-format-export-menu", templateFormatExportItem, {
    sequence: 11,
});

cogMenuRegistry.add("stock-subwarehouse-product-bom-import-menu", {
    Component: ProductBomImportMenu,
    groupNumber: STATIC_ACTIONS_GROUP_NUMBER,
    isDisplayed: (env) =>
        getSupportedListRoot(env)?.resModel === "product.template",
}, {
    sequence: 12,
});

cogMenuRegistry.add("stock-subwarehouse-partner-channel-export-all", {
    Component: PartnerChannelExportAllMenu,
    groupNumber: STATIC_ACTIONS_GROUP_NUMBER,
    isDisplayed: (env) => {
        const root = getSupportedListRoot(env);
        return Boolean(
            root &&
            root.resModel === "res.partner" &&
            ["distributor", "supplier"].includes(
                root.context.partner_channel_import_type
            ) &&
            !root.selection.length
        );
    },
}, {
    sequence: 10,
});
