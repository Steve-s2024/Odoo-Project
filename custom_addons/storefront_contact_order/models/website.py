from urllib.parse import quote

from odoo import fields, models


class Website(models.Model):
    _inherit = "website"

    x_contact_to_order = fields.Boolean(
        string="Contact to order",
        default=True,
        help="Product pages show how to contact us instead of the cart, and "
             "the cart and checkout are closed. Turn off once online payment "
             "is ready.",
    )
    x_contact_order_phone = fields.Char(string="Order phone", default="+86 400-008-5355")
    x_contact_order_email = fields.Char(string="Order email", default="sun@snowlandholdings.com")
    x_contact_order_wechat = fields.Char(
        string="Order WeChat ID",
        help="Shown on product pages only when filled in.",
    )
    x_contact_order_wechat_english = fields.Boolean(
        string="Show WeChat on English pages",
        default=True,
    )

    def _contact_order_phone_href(self):
        self.ensure_one()
        digits = "".join(char for char in self.x_contact_order_phone or "" if char.isdigit() or char == "+")
        return "tel:%s" % digits

    def _contact_order_mail_href(self, product=None, is_english=False):
        """mailto: link whose subject already names the product the customer is viewing."""
        self.ensure_one()
        subject = "Order enquiry" if is_english else "订购咨询"
        if product:
            name = product._get_website_display_name(is_english=is_english)
            subject = "%s: %s" % (subject, name)
            if product.default_code:
                subject = "%s (%s)" % (subject, product.default_code)
        return "mailto:%s?subject=%s" % (self.x_contact_order_email or "", quote(subject))
