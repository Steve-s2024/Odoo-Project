/** @odoo-module **/

import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

export class MrpImportDestinationDialog extends ConfirmationDialog {
    static template = "stock_subwarehouse_hierarchy.MrpImportDestinationDialog";
    static props = {
        ...ConfirmationDialog.props,
        destinations: Array,
        dryrun: Boolean,
    };
}
