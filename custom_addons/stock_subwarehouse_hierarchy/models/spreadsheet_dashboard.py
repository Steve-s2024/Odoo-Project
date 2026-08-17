import json
import re

from odoo import api, models


class SpreadsheetDashboard(models.Model):
    _inherit = "spreadsheet.dashboard"

    @api.model
    def _add_external_domain(self, domain, is_external):
        marker = ["x_is_external_order", "=", is_external]
        def replace_marker(value):
            if (
                isinstance(value, list)
                and len(value) == 3
                and value[0] == "x_is_external_order"
                and value[1] == "="
            ):
                return list(marker)
            if isinstance(value, list):
                return [replace_marker(item) for item in value]
            return value

        domain = replace_marker(domain)
        if marker in domain or not domain:
            return domain or [marker]
        if not domain:
            return [marker]
        return ["&", marker, *domain]

    @api.model
    def _rewrite_dashboard_link(self, value, is_external, use_external_amount=False):
        if not isinstance(value, str) or "odoo://view/" not in value:
            return value
        match = re.search(r"odoo://view/(\{.*\})\)$", value)
        if not match:
            return value
        try:
            view_data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return value
        action = view_data.get("action", {})
        if action.get("modelName") not in ("sale.order", "sale.report"):
            return value
        action["domain"] = self._add_external_domain(
            action.get("domain", []), is_external
        )
        if use_external_amount:
            context = action.setdefault("context", {})
            if context.get("graph_measure") == "price_subtotal":
                context["graph_measure"] = "x_amount_received"
            if "price_subtotal" in context.get("pivot_measures", []):
                context["pivot_measures"] = [
                    "x_amount_received" if measure == "price_subtotal" else measure
                    for measure in context["pivot_measures"]
                ]
        prefix = value[:match.start(1)]
        return prefix + json.dumps(view_data, ensure_ascii=False, separators=(",", ":")) + ")"

    @api.model
    def _transform_sales_dashboard(self, snapshot, is_external):
        snapshot = json.loads(json.dumps(snapshot))
        for list_data in snapshot.get("lists", {}).values():
            if list_data.get("model") != "sale.order":
                continue
            list_data["domain"] = self._add_external_domain(
                list_data.get("domain", []), is_external
            )
            if is_external:
                list_data["columns"] = [
                    "x_amount_received" if field_name == "amount_untaxed" else field_name
                    for field_name in list_data.get("columns", [])
                ]
                for order_by in list_data.get("orderBy", []):
                    if order_by.get("name") == "amount_untaxed":
                        order_by["name"] = "x_amount_received"

        for pivot in snapshot.get("pivots", {}).values():
            if pivot.get("model") != "sale.report":
                continue
            pivot["domain"] = self._add_external_domain(
                pivot.get("domain", []), is_external
            )
            if is_external:
                for measure in pivot.get("measures", []):
                    if measure.get("fieldName") == "price_subtotal":
                        measure.update({
                            "id": "x_amount_received",
                            "fieldName": "x_amount_received",
                            "userDefinedName": "实收",
                        })
                sorted_column = pivot.get("sortedColumn")
                if sorted_column and sorted_column.get("measure") == "price_subtotal":
                    sorted_column["measure"] = "x_amount_received"

        for sheet in snapshot.get("sheets", []):
            for address, value in list(sheet.get("cells", {}).items()):
                if is_external and isinstance(value, str):
                    value = value.replace('"amount_untaxed"', '"x_amount_received"')
                    value = value.replace('_t("Revenue")', '"实收"')
                sheet["cells"][address] = self._rewrite_dashboard_link(
                    value, is_external, use_external_amount=is_external
                )
        return snapshot

    @api.model
    def _configure_separate_sales_dashboards(self):
        internal = self.env.ref(
            "spreadsheet_dashboard_sale.spreadsheet_dashboard_sales",
            raise_if_not_found=False,
        )
        external = self.env.ref(
            "stock_subwarehouse_hierarchy.spreadsheet_dashboard_external_sales",
            raise_if_not_found=False,
        )
        if not internal or not external or not internal.spreadsheet_data:
            return False

        # Preserve the stock dashboard layout/charts and change only their
        # authoritative data domains. The external copy additionally swaps the
        # revenue measure to the imported actual receipt amount.
        source = json.loads(internal.spreadsheet_data)
        internal.spreadsheet_data = json.dumps(
            self._transform_sales_dashboard(source, is_external=False),
            ensure_ascii=False,
        )
        external.spreadsheet_data = json.dumps(
            self._transform_sales_dashboard(source, is_external=True),
            ensure_ascii=False,
        )
        return True
