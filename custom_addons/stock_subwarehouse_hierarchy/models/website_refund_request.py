from collections import defaultdict

from odoo import _, Command, api, fields, models
from odoo.exceptions import ValidationError


class WebsiteRefundRequest(models.Model):
    _name = "stock.subwarehouse.website.refund.request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "网站退款申请"
    _order = "create_date desc, id desc"

    name = fields.Char(related="order_id.name", store=True, readonly=True)
    order_id = fields.Many2one("sale.order", required=True, ondelete="cascade")
    partner_id = fields.Many2one(related="order_id.partner_id", store=True)
    source_transaction_id = fields.Many2one(
        "payment.transaction", string="原支付交易", required=True, ondelete="restrict"
    )
    refund_transaction_id = fields.Many2one(
        "payment.transaction", string="支付退款交易", readonly=True, ondelete="restrict"
    )
    credit_note_id = fields.Many2one(
        "account.move", string="退款贷项通知单", readonly=True, copy=False, ondelete="restrict"
    )
    return_required = fields.Boolean(string="需要退货", readonly=True, copy=False)
    return_warehouse_id = fields.Many2one(
        "stock.warehouse", string="退货仓库", readonly=True, copy=False
    )
    return_location_id = fields.Many2one(
        "stock.location",
        string="退货目的库位",
        domain="[('usage', '=', 'internal')]",
        copy=False,
        help="默认使用商品原发货库位；审核前可改为其他内部库位。",
    )
    return_picking_ids = fields.One2many(
        "stock.picking",
        "website_refund_request_id",
        string="客户退货单",
        readonly=True,
    )
    return_picking_count = fields.Integer(
        string="退货单数量", compute="_compute_return_picking_count"
    )
    currency_id = fields.Many2one(related="source_transaction_id.currency_id")
    line_ids = fields.One2many(
        "stock.subwarehouse.website.refund.request.line", "request_id", string="退款商品"
    )
    amount_total = fields.Monetary(compute="_compute_amount_total", store=True)
    state = fields.Selection(
        [
            ("requested", "待审核"),
            ("returning", "等待客户退货"),
            ("return_received", "退货已收货"),
            ("return_cancelled", "退货单已取消"),
            ("processing", "退款处理中"),
            ("refunded", "已退款"),
            ("failed", "退款失败"),
            ("rejected", "已拒绝"),
        ],
        compute="_compute_state",
        store=True,
    )
    review_state = fields.Selection(
        [("requested", "待审核"), ("approved", "已通过"), ("rejected", "已拒绝")],
        default="requested",
        required=True,
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        refund_requests = super().create(vals_list)
        for refund_request in refund_requests.filtered(lambda request: not request.return_location_id):
            original_locations = refund_request._get_original_return_locations()
            if len(original_locations) == 1:
                refund_request.return_location_id = original_locations
        refund_requests._notify_refund_reviewers()
        return refund_requests

    def write(self, vals):
        result = super().write(vals)
        if "review_state" in vals:
            self.filtered(lambda request: request.review_state != "requested")._complete_review_activities()
        return result

    def _refund_reviewer_users(self):
        sales_managers = self.env.ref(
            "sales_team.group_sale_manager", raise_if_not_found=False,
        )
        users = sales_managers.all_user_ids if sales_managers else self.env["res.users"]
        users = users.filtered(lambda user: user.active and not user.share)
        if users:
            return users
        administrator = self.env.ref("base.user_admin", raise_if_not_found=False)
        return administrator if administrator and administrator.active else self.env.user

    def _notify_refund_reviewers(self):
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        if not activity_type:
            return
        summary = _("新退款申请待审核")
        for refund_request in self.filtered(lambda request: request.review_state == "requested"):
            reviewers = refund_request._refund_reviewer_users().filtered(
                lambda user: not user.company_ids or refund_request.order_id.company_id in user.company_ids
            )
            for reviewer in reviewers:
                existing = refund_request.activity_ids.filtered(
                    lambda activity: activity.activity_type_id == activity_type
                    and activity.user_id == reviewer
                    and activity.summary == summary
                )
                if not existing:
                    refund_request.activity_schedule(
                        act_type_xmlid="mail.mail_activity_data_todo",
                        user_id=reviewer.id,
                        summary=summary,
                        note=_(
                            "订单 %(order)s 收到一项新退款申请。请按申请时间顺序审核。",
                            order=refund_request.order_id.name,
                        ),
                    )
            refund_request.order_id.message_post(
                body=_(
                    "收到新的退款申请：%(refund)s。该申请已加入待处理退款队列。",
                    refund=refund_request.display_name,
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
                partner_ids=reviewers.partner_id.ids,
            )

    def _complete_review_activities(self):
        summary = _("新退款申请待审核")
        activities = self.activity_ids.filtered(
            lambda activity: activity.summary == summary
        )
        if activities:
            activities.sudo().action_feedback(feedback=_("退款申请已完成审核。"))

    @api.depends("return_picking_ids")
    def _compute_return_picking_count(self):
        for refund_request in self:
            refund_request.return_picking_count = len(refund_request.return_picking_ids)

    @api.depends("line_ids.amount")
    def _compute_amount_total(self):
        for refund_request in self:
            refund_request.amount_total = sum(refund_request.line_ids.mapped("amount"))

    @api.depends(
        "review_state",
        "return_required",
        "return_picking_ids.state",
        "refund_transaction_id.state",
    )
    def _compute_state(self):
        for refund_request in self:
            transaction = refund_request.refund_transaction_id
            if refund_request.review_state == "rejected":
                refund_request.state = "rejected"
            elif transaction and transaction.state == "done":
                refund_request.state = "refunded"
            elif transaction and transaction.state == "error":
                refund_request.state = "failed"
            elif transaction:
                refund_request.state = "processing"
            elif refund_request.review_state == "requested":
                refund_request.state = "requested"
            elif refund_request.return_required:
                active_returns = refund_request.return_picking_ids.filtered(
                    lambda picking: picking.state != "cancel"
                )
                if active_returns and all(picking.state == "done" for picking in active_returns):
                    refund_request.state = "return_received"
                elif active_returns:
                    refund_request.state = "returning"
                else:
                    refund_request.state = "return_cancelled"
            else:
                refund_request.state = "processing"

    def action_submit_wechat_refund(self):
        return_pickings = self.env["stock.picking"]
        for refund_request in self:
            if refund_request.state not in ("requested", "return_received"):
                raise ValidationError(_("该退款申请当前不能提交退款。"))
            refund_request._validate_payment_refund()

            if refund_request.state == "requested":
                quantities_by_picking = refund_request._get_return_quantities_by_picking()
                refund_request.review_state = "approved"
                if quantities_by_picking:
                    if not refund_request.return_location_id:
                        original_locations = refund_request._get_original_return_locations(
                            quantities_by_picking
                        )
                        if len(original_locations) == 1:
                            refund_request.return_location_id = original_locations
                    refund_request.return_required = True
                    return_pickings |= refund_request._create_customer_return_pickings(
                        quantities_by_picking
                    )
                    continue

            refund_request._submit_payment_refund()

        if return_pickings:
            return self._return_pickings_action(return_pickings)
        return True

    def _validate_payment_refund(self):
        self.ensure_one()
        if (
            self.source_transaction_id.provider_code not in ("wechatpay", "alipay")
            or not self.source_transaction_id.provider_id.support_refund
        ):
            raise ValidationError(_("该支付方式不支持原路退款。"))
        if self.amount_total <= 0:
            raise ValidationError(_("退款金额必须大于零。"))
        available = self.source_transaction_id.amount - sum(
            -transaction.amount
            for transaction in self.source_transaction_id.child_transaction_ids
            if transaction.operation == "refund"
            and transaction.state in ("draft", "pending", "authorized", "done")
        )
        if self.amount_total > available:
            raise ValidationError(_("退款金额超过当前可退款金额。"))

    def _submit_payment_refund(self):
        self.ensure_one()
        if self.refund_transaction_id:
            return self.refund_transaction_id
        if self.return_required and self.state != "return_received":
            raise ValidationError(_("必须先完成客户退货入库，才能提交支付退款。"))
        transaction = self.source_transaction_id._refund(self.amount_total)
        self.refund_transaction_id = transaction
        if transaction.state == "done":
            self._ensure_credit_note()
        return transaction

    def _ensure_credit_note(self):
        """Create one posted partial credit note after a successful payment refund."""
        for refund_request in self.filtered(
            lambda request: request.refund_transaction_id.state == "done"
            and not request.credit_note_id
        ):
            posted_invoices = refund_request.order_id.invoice_ids.filtered(
                lambda move: move.move_type == "out_invoice" and move.state == "posted"
            ).sorted(key=lambda move: (move.invoice_date or fields.Date.today(), move.id), reverse=True)
            if not posted_invoices:
                continue

            selected_sale_lines = refund_request.line_ids.mapped("sale_line_id")
            invoice = posted_invoices.filtered(
                lambda move: all(
                    sale_line.invoice_lines.filtered(
                        lambda invoice_line: invoice_line.move_id == move
                        and invoice_line.display_type == "product"
                    )
                    for sale_line in selected_sale_lines
                )
            )[:1] or posted_invoices[:1]

            credit_lines = []
            for refund_line in refund_request.line_ids:
                sale_line = refund_line.sale_line_id
                invoice_line = sale_line.invoice_lines.filtered(
                    lambda line: line.move_id == invoice and line.display_type == "product"
                )[:1]
                if not invoice_line:
                    continue
                quantity = sale_line.product_uom_id._compute_quantity(
                    refund_line.quantity, invoice_line.product_uom_id
                )
                credit_lines.append(Command.create({
                    "product_id": invoice_line.product_id.id,
                    "name": invoice_line.name,
                    "account_id": invoice_line.account_id.id,
                    "quantity": quantity,
                    "product_uom_id": invoice_line.product_uom_id.id,
                    "price_unit": invoice_line.price_unit,
                    "discount": invoice_line.discount,
                    "tax_ids": [Command.set(invoice_line.tax_ids.ids)],
                    "sale_line_ids": [Command.set(sale_line.ids)],
                }))
            if not credit_lines:
                continue

            credit_note = self.env["account.move"].sudo().create({
                "move_type": "out_refund",
                "partner_id": invoice.partner_id.id,
                "currency_id": invoice.currency_id.id,
                "journal_id": invoice.journal_id.id,
                "invoice_date": fields.Date.context_today(refund_request),
                "invoice_origin": refund_request.order_id.name,
                "ref": _("网站退款 %(refund)s", refund=refund_request.display_name),
                "reversed_entry_id": invoice.id,
                "invoice_line_ids": credit_lines,
            })
            credit_note.action_post()
            refund_request.credit_note_id = credit_note

    def action_view_credit_note(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("退款贷项通知单"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.credit_note_id.id,
        }

    def _get_return_quantities_by_picking(self):
        """Allocate delivered refund quantities to their original outgoing moves."""
        self.ensure_one()
        quantities_by_picking = defaultdict(dict)
        for refund_line in self.line_ids:
            remaining_qty = refund_line.quantity
            sale_uom = refund_line.sale_line_id.product_uom_id
            delivered_moves = refund_line.sale_line_id.move_ids.filtered(
                lambda move: move.state == "done"
                and move.picking_id.picking_type_code == "outgoing"
                and not move.origin_returned_move_id
            ).sorted(lambda move: (move.date, move.id))
            for move in delivered_moves:
                delivered_qty = move.product_uom._compute_quantity(move.quantity, sale_uom)
                returned_qty = sum(
                    returned_move.product_uom._compute_quantity(
                        returned_move.quantity
                        if returned_move.state == "done"
                        else returned_move.product_uom_qty,
                        sale_uom,
                    )
                    for returned_move in move.returned_move_ids
                    if returned_move.state != "cancel"
                )
                available_qty = max(delivered_qty - returned_qty, 0.0)
                quantity = min(remaining_qty, available_qty)
                if sale_uom.is_zero(quantity):
                    continue
                quantities_by_picking[move.picking_id][move] = sale_uom._compute_quantity(
                    quantity, move.product_uom
                )
                remaining_qty -= quantity
                if sale_uom.is_zero(remaining_qty):
                    break
        return quantities_by_picking

    def _get_original_return_locations(self, quantities_by_picking=None):
        self.ensure_one()
        quantities_by_picking = quantities_by_picking or self._get_return_quantities_by_picking()
        locations = self.env["stock.location"]
        for quantities_by_move in quantities_by_picking.values():
            for move in quantities_by_move:
                locations |= move.location_id
        if not locations:
            # A refund can be requested before the delivery move is completed or
            # linked.  The sale line is still authoritative for the exact source
            # subwarehouse selected during checkout.
            locations |= self.line_ids.mapped("sale_line_id.x_source_location_id")
        return locations

    def _create_customer_return_pickings(self, quantities_by_picking):
        self.ensure_one()
        return_pickings = self.env["stock.picking"]
        for delivery, quantities_by_move in quantities_by_picking.items():
            wizard = self.env["stock.return.picking"].create({"picking_id": delivery.id})
            for return_line in wizard.product_return_moves:
                return_line.quantity = quantities_by_move.get(return_line.move_id, 0.0)
            return_picking = wizard._create_return()
            destinations_by_move = {
                return_move: self.return_location_id
                or return_move.origin_returned_move_id.location_id
                for return_move in return_picking.move_ids
            }
            destinations = self.env["stock.location"]
            for destination in destinations_by_move.values():
                destinations |= destination
            if destinations:
                return_picking.location_dest_id = destinations[:1]
                for return_move, destination in destinations_by_move.items():
                    return_move.location_dest_id = destination
            return_picking.write({
                "website_refund_request_id": self.id,
                "origin": _(
                    "%(origin)s / 网站退款 %(refund)s",
                    origin=return_picking.origin,
                    refund=self.name,
                ),
            })
            return_pickings |= return_picking
            if not self.return_warehouse_id:
                self.return_warehouse_id = delivery.picking_type_id.warehouse_id
        return return_pickings

    def action_recreate_customer_return(self):
        self.ensure_one()
        if self.state != "return_cancelled":
            raise ValidationError(_("仅已取消的客户退货流程可以重新创建退货单。"))
        quantities_by_picking = self._get_return_quantities_by_picking()
        if not quantities_by_picking:
            raise ValidationError(_("没有可重新创建的已交付退货数量。"))
        return self._return_pickings_action(
            self._create_customer_return_pickings(quantities_by_picking)
        )

    def action_view_return_pickings(self):
        self.ensure_one()
        return self._return_pickings_action(self.return_picking_ids)

    def _return_pickings_action(self, pickings):
        action = {
            "type": "ir.actions.act_window",
            "name": _("客户退货单"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("id", "in", pickings.ids)],
        }
        if len(pickings) == 1:
            action.update({"view_mode": "form", "res_id": pickings.id})
        return action

    def action_reject(self):
        self.filtered(lambda refund_request: refund_request.state == "requested").write({
            "review_state": "rejected",
        })


class WebsiteRefundRequestLine(models.Model):
    _name = "stock.subwarehouse.website.refund.request.line"
    _description = "Website Refund Request Line"

    request_id = fields.Many2one(
        "stock.subwarehouse.website.refund.request", required=True, ondelete="cascade"
    )
    sale_line_id = fields.Many2one("sale.order.line", required=True, ondelete="restrict")
    product_id = fields.Many2one(related="sale_line_id.product_id", store=True)
    quantity = fields.Float(string="退款数量", required=True)
    currency_id = fields.Many2one(related="request_id.currency_id")
    amount = fields.Monetary(compute="_compute_amount", store=True)

    @api.depends("sale_line_id.price_total", "sale_line_id.product_uom_qty", "quantity")
    def _compute_amount(self):
        for line in self:
            ordered_qty = line.sale_line_id.product_uom_qty
            line.amount = (
                line.sale_line_id.price_total * line.quantity / ordered_qty
                if ordered_qty else 0.0
            )

    @api.constrains("quantity", "sale_line_id")
    def _check_quantity(self):
        for line in self:
            if not 0 < line.quantity <= line.sale_line_id.product_uom_qty:
                raise ValidationError(_("退款数量必须大于零且不超过订单数量。"))
