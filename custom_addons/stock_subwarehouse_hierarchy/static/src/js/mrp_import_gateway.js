/** @odoo-module **/

import { ImportRecords } from "@base_import/import_records/import_records";
import { patch } from "@web/core/utils/patch";

patch(ImportRecords.prototype, {
    importRecords() {
        const { context, resModel } = this.env.searchModel;
        if (resModel === "mrp.production") {
            return this.action.doAction({
                type: "ir.actions.act_window",
                name: "制造单标准导入",
                res_model: "stock.subwarehouse.mrp.import.gateway",
                view_mode: "form",
                views: [[false, "form"]],
                target: "current",
                context,
            });
        }
        return super.importRecords(...arguments);
    },
});
