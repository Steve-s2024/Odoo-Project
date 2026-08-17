from odoo import fields, models


class SaleReport(models.Model):
    _inherit = "sale.report"

    x_is_external_order = fields.Boolean(string="外部订单", readonly=True)
    x_official_total = fields.Monetary(string="总金额", readonly=True)
    x_processing_fee = fields.Monetary(string="手续费", readonly=True)
    x_amount_received = fields.Monetary(string="实收", readonly=True)
    state = fields.Selection(
        selection_add=[("external_done", "外部订单已完成")],
        ondelete={"external_done": "set null"},
    )

    def _select_additional_fields(self):
        fields_info = super()._select_additional_fields()
        currency_rate = self._case_value_or_one("s.currency_rate")
        company_rate = self._case_value_or_one("account_currency_table.rate")
        line_share = """
            CASE COALESCE((
                SELECT SUM(all_lines.price_subtotal)
                  FROM sale_order_line all_lines
                 WHERE all_lines.order_id = s.id
                   AND all_lines.display_type IS NULL
            ), 0)
                WHEN 0 THEN 0
                ELSE SUM(l.price_subtotal) / (
                    SELECT SUM(all_lines.price_subtotal)
                      FROM sale_order_line all_lines
                     WHERE all_lines.order_id = s.id
                       AND all_lines.display_type IS NULL
                )
            END
        """
        fields_info.update({
            "x_is_external_order": "s.x_is_external_order",
            "x_official_total": (
                f"s.x_official_total * ({line_share}) / {currency_rate} * {company_rate}"
            ),
            # Order-level fee and receipt values are allocated to report lines
            # by their untaxed amount share. This keeps totals exact while
            # retaining meaningful product/customer/platform analysis.
            "x_processing_fee": (
                f"s.x_processing_fee * ({line_share}) / {currency_rate} * {company_rate}"
            ),
            "x_amount_received": (
                f"s.x_amount_received * ({line_share}) / {currency_rate} * {company_rate}"
            ),
        })
        return fields_info

    def _group_by_sale(self):
        return f"""{super()._group_by_sale()},
            s.x_is_external_order,
            s.x_official_total,
            s.x_processing_fee,
            s.x_amount_received
        """
