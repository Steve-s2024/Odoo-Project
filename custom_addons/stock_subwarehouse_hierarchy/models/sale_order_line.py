from odoo import api, fields, models
from odoo.tools import float_compare


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    x_eligible_source_location_ids = fields.Many2many(
        "stock.location",
        string="可选来源库存",
        compute="_compute_x_eligible_source_location_ids",
    )
    x_source_available_qty = fields.Float(
        string="来源可用数量",
        compute="_compute_x_source_available_qty",
        digits="Product Unit",
    )
    x_source_can_fulfill = fields.Boolean(
        string="可满足",
        compute="_compute_x_source_available_qty",
    )

    @api.depends(
        "order_id.warehouse_id",
        "product_id",
        "product_uom_id",
        "product_uom_qty",
        "display_type",
        "is_storable",
    )
    def _compute_x_eligible_source_location_ids(self):
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        Quant = self.env["stock.quant"]
        Location = self.env["stock.location"]
        for line in self:
            if line.display_type or not line.product_id or not line.is_storable:
                line.x_eligible_source_location_ids = False
                continue

            candidates = line._get_source_location_candidates()
            required_qty = line._get_required_qty_in_product_uom()
            if float_compare(required_qty, 0.0, precision_digits=precision) <= 0:
                line.x_eligible_source_location_ids = False
                continue

            eligible_location_ids = []
            for location in candidates:
                available_qty = Quant._get_available_quantity(line.product_id, location)
                if float_compare(available_qty, required_qty, precision_digits=precision) >= 0:
                    eligible_location_ids.append(location.id)
            line.x_eligible_source_location_ids = Location.browse(eligible_location_ids)

    @api.depends(
        "order_id.warehouse_id",
        "product_id",
        "product_uom_id",
        "product_uom_qty",
        "x_eligible_source_location_ids",
        "display_type",
        "is_storable",
    )
    def _compute_x_source_location_id(self):
        for line in self:
            if line.display_type or not line.product_id or not line.is_storable:
                line.x_source_location_id = False
                continue

            eligible_locations = line.x_eligible_source_location_ids
            if line.x_source_location_id in eligible_locations:
                continue
            line.x_source_location_id = eligible_locations[:1]

    @api.onchange(
        "order_id.warehouse_id",
        "product_id",
        "product_uom_id",
        "product_uom_qty",
        "x_eligible_source_location_ids",
    )
    def _onchange_x_source_location_domain(self):
        domain = [("id", "in", self.x_eligible_source_location_ids.ids)]
        if self.x_source_location_id and self.x_source_location_id not in self.x_eligible_source_location_ids:
            self.x_source_location_id = False
        return {"domain": {"x_source_location_id": domain}}

    x_source_location_id = fields.Many2one(
        "stock.location",
        string="来源库存",
        compute="_compute_x_source_location_id",
        store=True,
        readonly=False,
        precompute=True,
        check_company=True,
        domain="[('usage', 'in', ['view', 'internal']), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="用于供应此报价单明细的内部仓库或子仓库库位。",
    )

    @api.depends("product_id", "product_uom_id", "product_uom_qty", "x_source_location_id", "is_storable")
    def _compute_x_source_available_qty(self):
        Quant = self.env["stock.quant"]
        for line in self:
            line.x_source_available_qty = 0.0
            line.x_source_can_fulfill = True
            if (
                line.display_type
                or not line.product_id
                or not line.is_storable
                or not line.x_source_location_id
            ):
                continue
            available_qty = Quant._get_available_quantity(
                line.product_id,
                line.x_source_location_id,
            )
            if line.product_uom_id and line.product_id.uom_id != line.product_uom_id:
                available_qty = line.product_id.uom_id._compute_quantity(
                    available_qty,
                    line.product_uom_id,
                )
            line.x_source_available_qty = available_qty
            line.x_source_can_fulfill = line.product_uom_qty <= available_qty

    def _get_source_location_candidates(self):
        self.ensure_one()
        domain = [
            ("usage", "in", ["view", "internal"]),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", (self.company_id or self.order_id.company_id or self.env.company).id),
        ]
        warehouse_roots = self.env["stock.warehouse"].search([
            "|",
            ("company_id", "=", False),
            ("company_id", "=", (self.company_id or self.order_id.company_id or self.env.company).id),
        ]).mapped("view_location_id")
        if warehouse_roots:
            domain.append(("id", "child_of", warehouse_roots.ids))
        return self.env["stock.location"].search(domain)

    def _get_required_qty_in_product_uom(self):
        self.ensure_one()
        if self.product_uom_id and self.product_id.uom_id != self.product_uom_id:
            return self.product_uom_id._compute_quantity(
                self.product_uom_qty,
                self.product_id.uom_id,
            )
        return self.product_uom_qty

    def _prepare_procurement_values(self):
        values = super()._prepare_procurement_values()
        self.ensure_one()
        if self.x_source_location_id:
            values["x_source_location_id"] = self.x_source_location_id
        return values
