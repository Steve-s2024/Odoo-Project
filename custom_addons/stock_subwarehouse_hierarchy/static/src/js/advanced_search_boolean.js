import { useState } from "@odoo/owl";
import { getDefaultDomain } from "@web/core/domain_selector/utils";
import { DomainSelectorDialog } from "@web/core/domain_selector_dialog/domain_selector_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { SearchModel } from "@web/search/search_model";


patch(SearchBar.prototype, {
    setup() {
        super.setup();
        this.erpBooleanState = useState({ connector: "&" });
        this.env.searchModel.erpQuickSearchConnector = "&";
    },

    toggleErpQuickSearchConnector() {
        this.erpBooleanState.connector = this.erpBooleanState.connector === "&" ? "|" : "&";
        this.env.searchModel.erpQuickSearchConnector = this.erpBooleanState.connector;
    },
});


patch(SearchModel.prototype, {
    addAutoCompletionValues(searchItemId, autocompleteValue) {
        const searchItem = this.searchItems[searchItemId];
        if (!searchItem || !["field", "field_property"].includes(searchItem.type)) {
            return super.addAutoCompletionValues(searchItemId, autocompleteValue);
        }

        const domain = this._getFieldDomain(searchItem, [autocompleteValue]).toString();
        const quickKey = `${searchItemId}:${domain}`;
        const duplicate = Object.values(this.searchItems).some(
            (item) => item.erpQuickSearchKey === quickKey
                && this.query.some((element) => element.searchItemId === item.id)
        );
        if (duplicate) {
            return;
        }

        const previousGroupExists = this.query.some((element) => {
            const item = this.searchItems[element.searchItemId];
            return item && item.groupId === this.erpLastQuickSearchGroupId;
        });
        const appendWithOr = this.erpQuickSearchConnector === "|" && previousGroupExists;
        const groupId = appendWithOr ? this.erpLastQuickSearchGroupId : this.nextGroupId++;
        const groupNumber = appendWithOr
            ? this.erpLastQuickSearchGroupNumber
            : this.nextGroupNumber++;
        const id = this.nextId++;
        const label = autocompleteValue.label;

        this.searchItems[id] = {
            id,
            groupId,
            groupNumber,
            type: "filter",
            invisible: "True",
            description: `${searchItem.description}: ${label}`,
            tooltip: `${searchItem.description}: ${label}`,
            domain,
            erpQuickSearchKey: quickKey,
        };
        this.query.push({ searchItemId: id });
        this.erpLastQuickSearchGroupId = groupId;
        this.erpLastQuickSearchGroupNumber = groupNumber;
        this._notify();
    },

    async spawnCustomFilterDialog() {
        const domain = getDefaultDomain(this.searchViewFields);
        this.dialog.add(DomainSelectorDialog, {
            resModel: this.resModel,
            defaultConnector: "&",
            domain,
            context: this.globalContext,
            onConfirm: (nextDomain) => this.splitAndAddDomain(nextDomain),
            disableConfirmButton: (nextDomain) => nextDomain === `[]`,
            title: _t("高级条件（支持括号）"),
            confirmButtonText: _t("搜索"),
            discardButtonText: _t("取消"),
            isDebugMode: this.isDebugMode,
        });
    },
});
