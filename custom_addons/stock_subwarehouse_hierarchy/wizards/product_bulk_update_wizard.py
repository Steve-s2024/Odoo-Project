from odoo import _, fields, models
from odoo.exceptions import UserError


class ProductBulkUpdateImage(models.TransientModel):
    _name = "stock.subwarehouse.product.bulk.update.image"
    _description = "批量产品图片"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        "stock.subwarehouse.product.bulk.update.wizard",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="图片名称", required=True, default="产品图片")
    image_1920 = fields.Image(string="图片")
    video_url = fields.Char(string="视频链接")


class ProductBulkUpdateWizard(models.TransientModel):
    _name = "stock.subwarehouse.product.bulk.update.wizard"
    _description = "批量修改产品"

    selected_product_ids = fields.Many2many(
        "product.template",
        relation="product_bulk_update_product_rel",
        column1="wizard_id",
        column2="product_tmpl_id",
        string="已选择产品",
        required=True,
        default=lambda self: self.env.context.get("active_ids", []),
    )

    apply_main_image = fields.Boolean(string="应用主图")
    main_image = fields.Image(string="共享主图")
    gallery_mode = fields.Selection(
        selection=[
            ("keep", "不修改图片集"),
            ("append", "添加到现有图片集"),
            ("replace", "替换现有图片集"),
        ],
        string="图片集处理方式",
        required=True,
        default="keep",
    )
    gallery_image_ids = fields.One2many(
        "stock.subwarehouse.product.bulk.update.image",
        "wizard_id",
        string="共享图片集",
    )

    apply_description_zh = fields.Boolean(string="应用中文网站描述")
    description_zh = fields.Html(string="中文网站描述")
    apply_description_en = fields.Boolean(string="应用英文网站描述")
    description_en = fields.Html(string="英文网站描述")
    apply_english_name = fields.Boolean(string="应用英文网站名称")
    website_english_name = fields.Char(string="英文网站名称")
    apply_category = fields.Boolean(string="应用产品类别")
    categ_id = fields.Many2one("product.category", string="产品类别")
    apply_material_type = fields.Boolean(string="应用物料类型")
    material_type = fields.Selection(
        selection=lambda self: self.env["product.template"]._fields["x_material_type"].selection,
        string="物料类型",
    )
    apply_list_price = fields.Boolean(string="应用销售价格")
    list_price = fields.Float(string="销售价格", digits="Product Price")

    def _validate_requested_changes(self):
        self.ensure_one()
        if not self.selected_product_ids:
            raise UserError(_("请先在产品列表中选择至少一个产品。"))
        if self.apply_main_image and not self.main_image:
            raise UserError(_("已勾选“应用主图”，请上传共享主图。"))
        if self.gallery_mode != "keep" and not self.gallery_image_ids:
            raise UserError(_("请选择至少一张共享图片集图片。"))
        if not any((
            self.apply_main_image,
            self.gallery_mode != "keep",
            self.apply_description_zh,
            self.apply_description_en,
            self.apply_english_name,
            self.apply_category,
            self.apply_material_type,
            self.apply_list_price,
        )):
            raise UserError(_("请至少选择一项需要应用的内容。"))

    def _apply_gallery_images(self, product):
        if self.gallery_mode == "keep":
            return
        if self.gallery_mode == "replace":
            product.product_template_image_ids.unlink()

        existing_media = {
            (image.image_1920, image.video_url or "")
            for image in product.product_template_image_ids
            if image.image_1920 or image.video_url
        }
        create_values = []
        for image in self.gallery_image_ids.sorted(lambda record: (record.sequence, record.id)):
            media_key = (image.image_1920, image.video_url or "")
            if media_key in existing_media:
                continue
            create_values.append({
                "name": image.name,
                "sequence": image.sequence,
                "image_1920": image.image_1920,
                "video_url": image.video_url,
                "product_tmpl_id": product.id,
            })
            existing_media.add(media_key)
        if create_values:
            self.env["product.image"].create(create_values)

    def action_apply(self):
        self.ensure_one()
        self._validate_requested_changes()

        common_values = {}
        if self.apply_main_image:
            common_values["image_1920"] = self.main_image
        if self.apply_description_zh:
            common_values["x_website_description_zh"] = self.description_zh or ""
        if self.apply_description_en:
            common_values["x_website_description_en"] = self.description_en or ""
        if self.apply_english_name:
            common_values["x_website_english_name"] = self.website_english_name or ""
        if self.apply_category:
            common_values["categ_id"] = self.categ_id.id
        if self.apply_material_type:
            common_values["x_material_type"] = self.material_type
        if self.apply_list_price:
            common_values["list_price"] = self.list_price

        if common_values:
            self.selected_product_ids.write(common_values)
        for product in self.selected_product_ids:
            self._apply_gallery_images(product)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("批量修改完成"),
                "message": _("已更新 %s 个产品。") % len(self.selected_product_ids),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
