from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tools import float_compare


class SaleProductSelectionBatch(models.TransientModel):
    _name = "stock.subwarehouse.sale.product.selection.batch"
    _description = "销售订单库存列表选品批次"

    order_id = fields.Many2one(
        "sale.order",
        string="报价单",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="order_id.company_id",
        string="公司",
        readonly=True,
    )
    line_ids = fields.One2many(
        "stock.subwarehouse.sale.product.selection.line",
        "batch_id",
        string="可选产品",
    )

    @api.model
    def action_open_for_order(self, order):
        order.ensure_one()
        if order.state not in ("draft", "sent"):
            raise UserError(_("只有草稿或已发送的报价单可以使用库存列表选品。"))
        if order.x_is_external_order:
            raise UserError(_("外部订单不参与 ERP 库存选品。"))

        self.search([
            ("create_uid", "=", self.env.uid),
            ("order_id", "=", order.id),
        ]).unlink()
        batch = self.create({"order_id": order.id})

        warehouse_roots = self.env["stock.warehouse"].search([
            ("company_id", "=", order.company_id.id),
        ]).mapped("view_location_id")
        quant_domain = [
            ("product_id.active", "=", True),
            ("product_id.sale_ok", "=", True),
            ("product_id.is_storable", "=", True),
            ("location_id.usage", "=", "internal"),
            ("quantity", ">", 0),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", order.company_id.id),
        ]
        if warehouse_roots:
            quant_domain.append(("location_id", "child_of", warehouse_roots.ids))
        quants = self.env["stock.quant"].sudo().search(quant_domain)

        candidate_locations = defaultdict(lambda: self.env["stock.location"])
        for quant in quants:
            candidate_locations[quant.product_id.id] |= quant.location_id

        line_commands = []
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        Order = self.env["sale.order"]
        for product_id in sorted(candidate_locations):
            product = self.env["product.product"].browse(product_id)
            options = Order._get_common_fulfillment_stock_options(
                product,
                company=order.company_id,
                candidate_locations=candidate_locations[product_id],
                exclude_order=order,
            )
            available_quantity = max((quantity for _location, quantity in options), default=0.0)
            if float_compare(
                available_quantity,
                0.0,
                precision_digits=precision,
            ) <= 0:
                continue
            line_commands.append(Command.create({
                "product_id": product.id,
                "available_quantity": available_quantity,
                "quantity": min(1.0, available_quantity),
                "inventory_locations": " · ".join(
                    f"{location.display_name}：{quantity:g}"
                    for location, quantity in options
                ),
            }))

        if not line_commands:
            raise UserError(_("当前没有可加入报价单的可用库存产品。"))
        batch.line_ids = line_commands
        return {
            "type": "ir.actions.act_window",
            "name": _("库存列表选品 — %(order)s", order=order.display_name),
            "res_model": "stock.subwarehouse.sale.product.selection.line",
            "view_mode": "list",
            "views": [(
                self.env.ref(
                    "stock_subwarehouse_hierarchy.view_sale_product_selection_line_list"
                ).id,
                "list",
            )],
            "search_view_id": self.env.ref(
                "stock_subwarehouse_hierarchy.view_sale_product_selection_line_search"
            ).id,
            "domain": [("batch_id", "=", batch.id)],
            "context": {
                "default_batch_id": batch.id,
                "create": False,
                "delete": False,
            },
            "target": "current",
        }


class SaleProductSelectionLine(models.TransientModel):
    _name = "stock.subwarehouse.sale.product.selection.line"
    _description = "销售订单库存列表选品行"
    _order = "product_default_code, product_id, id"

    batch_id = fields.Many2one(
        "stock.subwarehouse.sale.product.selection.batch",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    order_id = fields.Many2one(related="batch_id.order_id", readonly=True)
    product_id = fields.Many2one(
        "product.product",
        string="产品",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    product_default_code = fields.Char(
        related="product_id.default_code",
        string="产品ID",
        readonly=True,
        store=True,
    )
    product_category_id = fields.Many2one(
        related="product_id.categ_id",
        string="产品类别",
        readonly=True,
        store=True,
    )
    product_uom_id = fields.Many2one(
        related="product_id.uom_id",
        string="计量单位",
        readonly=True,
    )
    available_quantity = fields.Float(
        string="最多可选数量",
        digits="Product Unit",
        required=True,
        readonly=True,
    )
    quantity = fields.Float(
        string="加入数量",
        digits="Product Unit",
        required=True,
        default=1.0,
    )
    inventory_locations = fields.Char(string="可用库存位置", readonly=True)

    @api.constrains("quantity", "available_quantity")
    def _check_quantity_within_snapshot(self):
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        for line in self:
            if float_compare(line.quantity, 0.0, precision_digits=precision) <= 0:
                raise ValidationError(_("加入数量必须大于零。"))
            if float_compare(
                line.quantity,
                line.available_quantity,
                precision_digits=precision,
            ) > 0:
                raise ValidationError(_(
                    "%(product)s 最多可选择 %(available)s %(uom)s。",
                    product=line.product_id.display_name,
                    available=line.available_quantity,
                    uom=line.product_uom_id.display_name,
                ))

    def action_add_selected_to_order(self):
        if not self:
            raise UserError(_("请至少选择一个产品。"))
        batches = self.mapped("batch_id")
        if len(batches) != 1:
            raise UserError(_("一次只能向同一张报价单添加产品。"))
        order = batches.order_id.exists()
        if not order or order.state not in ("draft", "sent"):
            raise UserError(_("报价单已不存在或已不允许修改。"))
        if order.x_is_external_order:
            raise UserError(_("外部订单不参与 ERP 库存选品。"))

        precision = self.env["decimal.precision"].precision_get("Product Unit")
        products = self.mapped("product_id")
        order._lock_common_fulfillment_products(products)
        selected_sources = {}
        shortages = []
        for line in self.sorted("id"):
            options = order._get_common_fulfillment_stock_options(
                line.product_id,
                company=order.company_id,
                exclude_order=order,
            )
            satisfying = [
                option for option in options
                if float_compare(
                    option[1],
                    line.quantity,
                    precision_digits=precision,
                ) >= 0
            ]
            selected = sorted(satisfying, key=lambda option: (option[1], option[0].id))[:1]
            if not selected:
                shortages.append(_(
                    "%(product)s：需要 %(requested)s %(uom)s，当前没有单一来源库存可以满足。",
                    product=line.product_id.display_name,
                    requested=line.quantity,
                    uom=line.product_uom_id.display_name,
                ))
                continue
            selected_sources[line.id] = selected[0][0]
        if shortages:
            raise UserError(_("库存已经发生变化，以下产品未加入：\n%s") % "\n".join(shortages))

        for selection in self.sorted("id"):
            source_location = selected_sources[selection.id]
            existing_line = order.order_line.filtered(
                lambda line: (
                    not line.display_type
                    and not line.is_delivery
                    and line.product_id == selection.product_id
                    and line.product_uom_id == selection.product_uom_id
                    and line.x_source_location_id == source_location
                )
            )[:1]
            if existing_line:
                existing_line.product_uom_qty += selection.quantity
                continue
            values = order._prepare_common_fulfillment_order_line_values(
                selection.product_id,
                selection.quantity,
                source_location,
                product_uom=selection.product_uom_id,
            )
            values["order_id"] = order.id
            self.env["sale.order.line"].create(values)

        order._ensure_common_delivery_instruction()
        batches.unlink()
        return {
            "type": "ir.actions.act_window",
            "name": _("报价单"),
            "res_model": "sale.order",
            "res_id": order.id,
            "view_mode": "form",
            "views": [(self.env.ref("sale.view_order_form").id, "form")],
            "target": "current",
        }
