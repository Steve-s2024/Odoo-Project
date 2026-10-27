/** @odoo-module **/

import { BaseImportModel } from "@base_import/import_model";
import { ImportAction } from "@base_import/import_action/import_action";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { MrpImportDestinationDialog } from "./mrp_import_destination_dialog";

patch(BaseImportModel.prototype, {
    async executeImport() {
        this.mrpDestinationChoice = null;
        this.productImportResultAction = null;
        this.businessImportResultAction = null;
        return super.executeImport(...arguments);
    },

    async _callImport(dryrun, args) {
        if (dryrun) {
            this.productImportResultAction = null;
            this.businessImportResultAction = null;
        }
        let importArgs = args;
        if (this.resModel === "mrp.production" && this.mrpDestinationChoice) {
            importArgs = [...args.slice(0, 3), { ...args[3], ...this.mrpDestinationChoice }];
        }
        let result = await super._callImport(dryrun, importArgs);
        while (this.resModel === "mrp.production" && result?.x_mrp_missing_destinations) {
            const destinations = result.x_mrp_missing_destinations;
            this.unblock();
            let decision;
            try {
                decision = await new Promise((resolve) => {
                    this.env.services.dialog.add(MrpImportDestinationDialog, {
                        title: "制造单导入：发现不存在的成品库位",
                        destinations,
                        dryrun,
                        confirmLabel: "创建全部并继续",
                        cancelLabel: "跳过这些行",
                        confirm: () => resolve("create"),
                        cancel: () => resolve("skip"),
                        dismiss: () => resolve(null),
                    }, { onClose: () => resolve(null) });
                });
            } finally {
                this.block(dryrun ? "测试导入" : "正在导入");
            }
            if (!decision) {
                return { ids: [], messages: [], nextrow: 0 };
            }
            this.mrpDestinationChoice = {
                mrp_destination_decision: decision,
                mrp_destination_keys: destinations.map((destination) => destination.key),
            };
            importArgs = [...args.slice(0, 3), { ...args[3], ...this.mrpDestinationChoice }];
            result = await super._callImport(dryrun, importArgs);
        }
        if (this.resModel === "sale.order" && result) {
            const skippedRows = result.x_sale_import_skipped_rows || [];
            if (skippedRows.length) {
                this._addMessage("warning", [
                    "以下销售资料行已跳过，其余有效资料仍会继续导入：",
                    ...skippedRows.map((row) => {
                        const product = row.product_id ? `（产品ID：${row.product_id}）` : "";
                        return `第 ${row.source_row} 行${product}：${row.reason}`;
                    }),
                ]);
                if (!dryrun && !(result.ids || []).length) {
                    this.notificationService.add(
                        "所有销售资料行均已跳过，没有导入任何资料。",
                        { type: "warning", sticky: true }
                    );
                }
            }
        }
        if (["sale.order", "stock.quant", "mrp.production"].includes(this.resModel) && result) {
            const duplicateFailures = result.x_business_import_failures || [];
            if (duplicateFailures.length) {
                this._addMessage("warning", [
                    "以下资料行未通过校验，其余有效资料仍会继续导入：",
                    ...duplicateFailures.map((failure) =>
                        `第 ${failure.source_row} 行（${failure.identifier || "空"}）：${failure.reason}`
                    ),
                ]);
            }
            if (!dryrun && result.x_business_import_result_action?.x_open_when_complete) {
                this.businessImportResultAction = result.x_business_import_result_action;
                delete this.businessImportResultAction.x_open_when_complete;
            }
        }
        if (this.resModel === "sale.order" && !dryrun && result) {
            const hasBlockingError = (result.messages || []).some((message) =>
                ["danger", "error"].includes(message.type)
            );
            if (hasBlockingError) {
                this.notificationService.add(
                    "销售导入未完成。请查看红色错误信息，修正表格后重新导入。",
                    {
                        type: "danger",
                        sticky: true,
                        title: "销售导入失败",
                    }
                );
            }
        }
        if (this.resModel !== "product.template" || !result) {
            return result;
        }

        const failures = result.x_product_import_failures || [];
        if (failures.length) {
            this._addMessage("warning", [
                "以下产品资料行因产品编码不唯一而未导入：",
                ...failures.map((failure) =>
                    _t("Row %(row)s — %(code)s: %(reason)s", {
                        row: failure.source_row,
                        code: failure.default_code || "（空）",
                        reason: failure.reason,
                    })
                ),
            ]);
        }
        if (
            !dryrun &&
            result.x_product_import_result_action?.x_open_when_complete
        ) {
            this.productImportResultAction = result.x_product_import_result_action;
            delete this.productImportResultAction.x_open_when_complete;
        }
        return result;
    },
});

patch(ImportAction.prototype, {
    async handleImport(isTest) {
        await super.handleImport(...arguments);
        if (
            !isTest &&
            !this.state.isPaused &&
            this.model.businessImportResultAction &&
            !this.businessImportResultActionOpened
        ) {
            this.businessImportResultActionOpened = true;
            return this.actionService.doAction(this.model.businessImportResultAction);
        }
        if (
            !isTest &&
            !this.state.isPaused &&
            this.model.productImportResultAction &&
            !this.productImportResultActionOpened
        ) {
            this.productImportResultActionOpened = true;
            return this.actionService.doAction(this.model.productImportResultAction);
        }
    },

    openRecords(resIds) {
        const businessAction = this.model.businessImportResultAction;
        if (
            ["sale.order", "stock.quant", "mrp.production"].includes(this.model.resModel) &&
            businessAction
        ) {
            this.businessImportResultActionOpened = true;
            return this.actionService.doAction(businessAction);
        }
        const action = this.model.productImportResultAction;
        if (this.model.resModel === "product.template" && action) {
            this.productImportResultActionOpened = true;
            return this.actionService.doAction(action);
        }
        return super.openRecords(...arguments);
    },
});
