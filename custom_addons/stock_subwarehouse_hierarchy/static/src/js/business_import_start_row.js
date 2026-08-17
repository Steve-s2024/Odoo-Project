/** @odoo-module **/

import { ImportDataSidepanel } from "@base_import/import_data_sidepanel/import_data_sidepanel";
import { patch } from "@web/core/utils/patch";

// The side panel normally does not know which business model is being
// imported.  The inherited ImportAction template supplies this optional prop
// so the start-row control is only shown for the four requested import pages.
ImportDataSidepanel.props = {
    ...ImportDataSidepanel.props,
    resModel: { type: String, optional: true },
};

patch(ImportDataSidepanel.prototype, {
    onBusinessTopRowsChange(event) {
        const topRows = Math.max(1, Number.parseInt(event.target.value, 10) || 1);
        event.target.value = topRows;
        // Odoo removes the header independently, so its internal data offset
        // is one less than the user-facing count of top Excel rows.
        this.props.onOptionChanged("skip", topRows - 1);
    },
});
