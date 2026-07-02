import { Many2One } from "@web/views/fields/many2one/many2one";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";
import { patch } from "@web/core/utils/patch";

const SOURCE_LOCATION_SEARCH_LIMIT = 500;

patch(Many2One.prototype, {
    get many2XAutocompleteProps() {
        const props = super.many2XAutocompleteProps;
        if (this.props?.record?.resModel !== "sale.order.line" || this.props?.name !== "x_source_location_id") {
            return props;
        }
        return {
            ...props,
            searchLimit: SOURCE_LOCATION_SEARCH_LIMIT,
            searchThreshold: 0,
        };
    },
});

patch(Many2XAutocomplete.prototype, {
    addSearchMoreSuggestion(params) {
        if (
            this.props?.resModel === "stock.location" &&
            this.props?.context?.sale_source_inventory_filter
        ) {
            return false;
        }
        return super.addSearchMoreSuggestion(params);
    },
});
