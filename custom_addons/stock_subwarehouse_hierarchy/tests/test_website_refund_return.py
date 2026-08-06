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

            return_picking.move_ids.quantity = 1.0
            return_picking.move_ids.picked = True
            return_picking.button_validate()
            self.assertEqual(return_picking.state, "done")
            self.assertEqual(refund_request.state, "return_received")
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
