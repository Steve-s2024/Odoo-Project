from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStorefrontVideoUploadAccess(TransactionCase):
    def test_video_upload_is_visible_to_website_designers_without_system_admin(self):
        menu = self.env.ref(
            "stock_subwarehouse_hierarchy.menu_website_video_upload"
        )
        website_root = self.env.ref("website.menu_website_configuration")
        designer_group = self.env.ref("website.group_website_designer")
        system_group = self.env.ref("base.group_system")

        self.assertEqual(menu.parent_id, website_root)
        self.assertIn(designer_group, menu.group_ids)
        self.assertNotIn(system_group, menu.group_ids)

        wizard_model = self.env["ir.model"]._get(
            "stock.subwarehouse.website.video.upload.wizard"
        )
        access = self.env["ir.model.access"].search([
            ("model_id", "=", wizard_model.id),
            ("group_id", "=", self.env.ref("base.group_user").id),
        ])
        self.assertTrue(access)
        self.assertTrue(all(access.mapped("perm_create")))

        # The backend must send the leaf action to the web client.  A small
        # Website navbar patch then keeps this specific action from being
        # discarded with other top-level Website leaves.
        loaded_menus = self.env["ir.ui.menu"].load_menus(debug=False)
        loaded_menu = loaded_menus.get(str(menu.id)) or loaded_menus.get(menu.id)
        self.assertTrue(loaded_menu)
        self.assertEqual(
            loaded_menu["xmlid"],
            "stock_subwarehouse_hierarchy.menu_website_video_upload",
        )
        self.assertFalse(loaded_menu["children"])
