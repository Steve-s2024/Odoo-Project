from unittest.mock import patch

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "website_refund_return")
class TestWebsiteRefundReturn(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env["stock.warehouse"].create({
            "name": "Website Refund Return Warehouse",
            "code": "WRR",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Website Refund Return Product",
            "is_storable": True,
            "list_price": 100.0,
        })
        cls.customer = cls.env["res.partner"].create({
            "name": "Website Refund Return Customer",
        })
        cls.refund_reviewer = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Website Refund Reviewer",
            "login": "website-refund-reviewer@example.test",
            "group_ids": [Command.set([
                cls.env.ref("base.group_user").id,
                cls.env.ref("sales_team.group_sale_manager").id,
            ])],
        })
        cls.alternate_return_location = cls.env["stock.location"].create({
            "name": "Alternate Website Return Location",
            "usage": "internal",
            "location_id": cls.warehouse.view_location_id.id,
        })
        cls.provider = cls.env.ref("payment_wechatpay.payment_provider_wechatpay")
        cls.provider.write({
            "state": "test",
            "wechatpay_simulation_mode": True,
        })

    def _create_order(self, delivered=False):
        self.env.flush_all()
        self.env["stock.quant"]._update_available_quantity(
            self.product, self.warehouse.lot_stock_id, 10.0
        )
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
        })
        line = self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.product.id,
            "product_uom_qty": 2.0,
            "product_uom_id": self.product.uom_id.id,
            "price_unit": 100.0,
            "x_source_location_id": self.warehouse.lot_stock_id.id,
        })
        if delivered:
            order.action_confirm()
            delivery = order.picking_ids.filtered(
                lambda picking: picking.picking_type_code == "outgoing"
            )
            delivery.action_assign()
            delivery.move_ids.quantity = 2.0
            delivery.move_ids.picked = True
            delivery.button_validate()
            self.assertEqual(delivery.state, "done")
        return order, line

    def _create_paid_transaction(self, order, reference):
        transaction = self.env["payment.transaction"].create({
            "provider_id": self.provider.id,
            "payment_method_id": self.env.ref(
                "payment_wechatpay.payment_method_wechatpay"
            ).id,
            "reference": reference,
            "amount": order.amount_total,
            "currency_id": order.currency_id.id,
            "partner_id": order.partner_id.id,
            "operation": "online_redirect",
            "sale_order_ids": [Command.set(order.ids)],
        })
        transaction._set_done()
        return transaction

    def test_refund_history_hides_return_location_warning(self):
        arch = self.env.ref(
            "stock_subwarehouse_hierarchy.refund_history_section"
        ).arch_db
        self.assertNotIn("需要退回商品", arch)
        self.assertNotIn("Product return required", arch)
        self.assertNotIn("return_warehouse_id.partner_id.contact_address", arch)
        self.assertIn("refund_request_status", arch)

    def _create_refund_request(self, order, line, transaction):
        return self.env["stock.subwarehouse.website.refund.request"].create({
            "order_id": order.id,
            "source_transaction_id": transaction.id,
            "line_ids": [Command.create({
                "sale_line_id": line.id,
                "quantity": 1.0,
            })],
        })

    def test_undelivered_refund_submits_payment_without_return(self):
        order, line = self._create_order(delivered=False)
        transaction = self._create_paid_transaction(order, "REFUND-NOT-DELIVERED")
        refund_request = self._create_refund_request(order, line, transaction)
        self.assertEqual(refund_request.return_location_id, self.warehouse.lot_stock_id)

        with patch.object(
            self.env.registry["payment.transaction"],
            "_refund",
            autospec=True,
            return_value=transaction,
        ) as refund_mock:
            refund_request.action_submit_wechat_refund()

        refund_mock.assert_called_once()
        self.assertFalse(refund_request.return_required)
        self.assertFalse(refund_request.return_picking_ids)
        self.assertEqual(refund_request.state, "refunded")

    def test_lianlian_payment_is_accepted_by_shared_refund_workflow(self):
        order, line = self._create_order(delivered=False)
        provider = self.env.ref("payment_lianlian.payment_provider_lianlian")
        transaction = self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": self.env.ref(
                "payment_lianlian.payment_method_lianlian"
            ).id,
            "reference": "REFUND-LIANLIAN-ELIGIBILITY",
            "amount": order.amount_total,
            "currency_id": order.currency_id.id,
            "partner_id": order.partner_id.id,
            "operation": "online_redirect",
            "sale_order_ids": [Command.set(order.ids)],
        })
        transaction._set_done()
        refund_request = self._create_refund_request(order, line, transaction)

        with patch.object(
            self.env.registry["payment.transaction"],
            "_refund",
            autospec=True,
            return_value=transaction,
        ) as refund_mock:
            refund_request.action_submit_wechat_refund()

        refund_mock.assert_called_once()
        self.assertEqual(refund_request.review_state, "approved")
        self.assertEqual(refund_request.state, "refunded")

    def test_successful_paid_order_refund_posts_partial_credit_note(self):
        order, line = self._create_order(delivered=False)
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        transaction = self._create_paid_transaction(order, "REFUND-CREDIT-NOTE")
        refund_request = self._create_refund_request(order, line, transaction)

        with patch.object(
            self.env.registry["payment.transaction"],
            "_refund",
            autospec=True,
            return_value=transaction,
        ):
            refund_request.action_submit_wechat_refund()

        credit_note = refund_request.credit_note_id
        self.assertTrue(credit_note)
        self.assertEqual(credit_note.move_type, "out_refund")
        self.assertEqual(credit_note.state, "posted")
        self.assertEqual(credit_note.reversed_entry_id, invoice)
        self.assertEqual(credit_note.amount_total, refund_request.amount_total)
        self.assertEqual(credit_note.invoice_line_ids.sale_line_ids, line)

    def test_delivered_refund_waits_for_completed_customer_return(self):
        order, line = self._create_order(delivered=True)
        transaction = self._create_paid_transaction(order, "REFUND-AFTER-DELIVERY")
        refund_request = self._create_refund_request(order, line, transaction)

        with patch.object(
            self.env.registry["payment.transaction"],
            "_refund",
            autospec=True,
            return_value=transaction,
        ) as refund_mock:
            action = refund_request.action_submit_wechat_refund()
            refund_mock.assert_not_called()

            return_picking = refund_request.return_picking_ids
            self.assertTrue(refund_request.return_required)
            self.assertEqual(refund_request.state, "returning")
            self.assertEqual(action["res_id"], return_picking.id)
            self.assertEqual(return_picking.website_refund_request_id, refund_request)
            self.assertEqual(return_picking.picking_type_code, "incoming")
            self.assertEqual(return_picking.move_ids.product_uom_qty, 1.0)
            self.assertEqual(refund_request.return_location_id, self.warehouse.lot_stock_id)
            self.assertEqual(return_picking.move_ids.location_dest_id, self.warehouse.lot_stock_id)
            self.assertEqual(refund_request.x_return_delivery_state, "awaiting_delivery")
            before_return = self.env["stock.quant"]._get_available_quantity(
                self.product, self.warehouse.lot_stock_id, strict=True,
            )

            refund_request.action_start_customer_return_delivery()

            self.assertEqual(refund_request.x_return_delivery_state, "delivering")
            self.assertEqual(
                self.env["stock.quant"]._get_available_quantity(
                    self.product, self.warehouse.lot_stock_id, strict=True,
                ),
                before_return,
            )
            refund_request.action_mark_customer_return_delivered()
            self.assertEqual(return_picking.state, "done")
            self.assertEqual(refund_request.x_return_delivery_state, "delivered")
            self.assertEqual(refund_request.state, "return_received")
            self.assertEqual(
                self.env["stock.quant"]._get_available_quantity(
                    self.product, self.warehouse.lot_stock_id, strict=True,
                ),
                before_return + 1,
            )
            refund_mock.assert_not_called()

            refund_request.action_submit_wechat_refund()

        refund_mock.assert_called_once()
        self.assertEqual(refund_request.state, "refunded")

    def test_delivered_refund_allows_return_location_override(self):
        order, line = self._create_order(delivered=True)
        transaction = self._create_paid_transaction(order, "REFUND-ALTERNATE-LOCATION")
        refund_request = self._create_refund_request(order, line, transaction)
        refund_request.return_location_id = self.alternate_return_location

        with patch.object(
            self.env.registry["payment.transaction"],
            "_refund",
            autospec=True,
            return_value=transaction,
        ) as refund_mock:
            refund_request.action_submit_wechat_refund()

        refund_mock.assert_not_called()
        self.assertEqual(
            refund_request.return_picking_ids.move_ids.location_dest_id,
            self.alternate_return_location,
        )

    def test_new_refund_is_visible_to_reviewers_and_pending_queue(self):
        order, line = self._create_order(delivered=False)
        transaction = self._create_paid_transaction(order, "REFUND-REVIEW-NOTICE")

        refund_request = self._create_refund_request(order, line, transaction)

        reviewer_activity = self.env["mail.activity"].search([
            ("res_model", "=", refund_request._name),
            ("res_id", "=", refund_request.id),
            ("user_id", "=", self.refund_reviewer.id),
            ("summary", "=", "新退款申请待审核"),
        ])
        self.assertTrue(reviewer_activity)
        self.assertEqual(order.x_pending_website_refund_request_count, 1)
        self.assertTrue(order.message_ids.filtered(
            lambda message: "待处理退款队列" in (message.body or "")
        ))

        refund_request.review_state = "rejected"

        self.assertFalse(self.env["mail.activity"].search([
            ("res_model", "=", refund_request._name),
            ("res_id", "=", refund_request.id),
            ("summary", "=", "新退款申请待审核"),
        ]))
        self.assertEqual(order.x_pending_website_refund_request_count, 0)

    def test_pending_refund_queue_is_oldest_first(self):
        queue_arch = self.env.ref(
            "stock_subwarehouse_hierarchy.view_website_refund_request_queue"
        ).arch_db
        self.assertIn('default_order="create_date asc,id asc"', queue_arch)
