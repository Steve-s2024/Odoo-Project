from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestContactOrder(TransactionCase):
    def test_contact_links(self):
        website = self.env["website"].create({
            "name": "Contact order test",
            "x_contact_order_phone": "+86 400-008-5355",
            "x_contact_order_email": "sun@example.com",
        })
        self.assertTrue(website.x_contact_to_order)
        self.assertEqual(website._contact_order_phone_href(), "tel:+864000085355")
        self.assertEqual(
            website._contact_order_mail_href(is_english=True),
            "mailto:sun@example.com?subject=Order%20enquiry",
        )
