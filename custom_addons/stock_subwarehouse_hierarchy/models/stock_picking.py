from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class StockPicking(models.Model):
    _inherit = "stock.picking"

    website_refund_request_id = fields.Many2one(
        "stock.subwarehouse.website.refund.request",
        string="网站退款申请",
        copy=False,
        index=True,
        ondelete="set null",
    )

    def button_validate(self):
        invalid_returns = self.filtered(
            lambda picking: picking.website_refund_request_id
            and picking.website_refund_request_id.x_return_delivery_state != "delivering"
            and picking.state != "done"
        )
        if invalid_returns:
            raise UserError(_(
                "客户退货必须先执行“开始退货配送”，确认商品处于运输中后才能收货入库。"
            ))
        self._check_exact_source_location_stock()
        result = super().button_validate()
        dispatched_orders = self.filtered(
            lambda picking: picking.state == "done"
            and picking.picking_type_code == "outgoing"
            and picking.sale_id
            and picking.sale_id.x_website_payment_state == "paid"
            and picking.sale_id.x_website_delivery_state == "awaiting_delivery"
        ).mapped("sale_id")
        if dispatched_orders:
            dispatched_orders.write({
                "x_website_delivery_state": "delivering",
                "x_website_delivery_started_at": fields.Datetime.now(),
            })
        if not self.env.context.get("skip_refund_delivery_sync"):
            refund_requests = self.filtered(
                lambda picking: picking.state == "done"
                and picking.website_refund_request_id
            ).mapped("website_refund_request_id")
            for refund_request in refund_requests:
                active_returns = refund_request.return_picking_ids.filtered(
                    lambda picking: picking.state != "cancel"
                )
                if active_returns and all(picking.state == "done" for picking in active_returns):
                    refund_request.write({
                        "x_return_delivery_state": "delivered",
                        "x_return_delivered_at": refund_request.x_return_delivered_at
                        or fields.Datetime.now(),
                    })
        return result

    def _check_exact_source_location_stock(self):
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        Quant = self.env["stock.quant"]
        shortages = []
        for picking in self:
            for move in picking.move_ids.filtered(
                lambda stock_move: stock_move.state not in ("done", "cancel")
                and stock_move.product_id
                and stock_move.product_id.is_storable
                and stock_move.location_id
                and stock_move.location_id.usage in ("internal", "view")
                and not stock_move.location_id.should_bypass_reservation()
            ):
                source_location = move.location_id
                product = move.product_id
                include_descendants = picking.picking_type_code == "internal"
                requested_qty = move.product_uom._compute_quantity(
                    move.product_uom_qty,
                    product.uom_id,
                )
                if float_compare(requested_qty, 0.0, precision_digits=precision) <= 0:
                    continue

                allowed_source_locations = source_location
                if include_descendants:
                    allowed_source_locations = self.env["stock.location"].search([
                        ("id", "child_of", source_location.id),
                        ("usage", "=", "internal"),
                    ])
                wrong_source_lines = move.move_line_ids.filtered(
                    lambda line: line.location_id
                    and line.location_id not in allowed_source_locations
                )
                if wrong_source_lines:
                    wrong_locations = ", ".join(wrong_source_lines.mapped("location_id.display_name"))
                    shortages.append(_(
                        "%(product)s：调拨来源为 %(source)s，但明细正在使用 %(locations)s。请从实际库存所在仓库/子仓库发货。",
                        product=product.display_name,
                        source=source_location.display_name,
                        locations=wrong_locations,
                    ))
                    continue

                current_move_reserved_qty = sum(
                    line.product_uom_id._compute_quantity(line.quantity, product.uom_id)
                    for line in move.move_line_ids
                    if line.location_id in allowed_source_locations
                )
                available_qty = Quant._get_available_quantity(
                    product,
                    source_location,
                    strict=not include_descendants,
                )
                if float_compare(
                    requested_qty,
                    available_qty + current_move_reserved_qty,
                    precision_digits=precision,
                ) > 0:
                    if include_descendants:
                        shortages.append(_(
                            "%(product)s 来自 %(source)s 及其下级库存：需要 %(requested)s %(uom)s，可用 %(available)s %(uom)s。",
                            product=product.display_name,
                            source=source_location.display_name,
                            requested=requested_qty,
                            available=available_qty + current_move_reserved_qty,
                            uom=product.uom_id.display_name,
                        ))
                    else:
                        shortages.append(_(
                            "%(product)s 来自 %(source)s：需要 %(requested)s %(uom)s，可用 %(available)s %(uom)s。不能使用下级仓库库存。",
                            product=product.display_name,
                            source=source_location.display_name,
                            requested=requested_qty,
                            available=available_qty + current_move_reserved_qty,
                            uom=product.uom_id.display_name,
                        ))

        if shortages:
            raise UserError(_("当前调拨的来源库存不足：\n%s") % "\n".join(shortages))
