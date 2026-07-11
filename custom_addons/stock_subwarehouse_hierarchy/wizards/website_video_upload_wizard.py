import mimetypes

from odoo import _, fields, models
from odoo.exceptions import UserError


class WebsiteVideoUploadWizard(models.TransientModel):
    _name = "stock.subwarehouse.website.video.upload.wizard"
    _description = "Website Video Upload"

    name = fields.Char(string="视频名称", required=True)
    video_file = fields.Binary(string="视频文件", required=True, attachment=False)
    filename = fields.Char(string="文件名")
    result_url = fields.Char(string="视频链接", readonly=True)
    embed_code = fields.Text(string="嵌入代码", readonly=True)

    def action_upload_video(self):
        self.ensure_one()
        if not self.filename:
            raise UserError(_("请先选择视频文件。"))

        extension = (self.filename.rsplit(".", 1)[-1] or "").lower()
        allowed_extensions = {"mp4", "webm", "ogg", "ogv"}
        if extension not in allowed_extensions:
            raise UserError(_("请上传 MP4、WebM 或 OGG 视频文件。"))

        mimetype = mimetypes.guess_type(self.filename)[0] or "video/mp4"
        attachment = self.env["ir.attachment"].sudo().create({
            "name": self.name or self.filename,
            "datas": self.video_file,
            "type": "binary",
            "mimetype": mimetype,
            "public": True,
        })
        self.result_url = f"/web/content/{attachment.id}?download=false"
        self.embed_code = (
            '<video controls playsinline preload="metadata" style="width:100%;height:auto;">'
            f'<source src="{self.result_url}" type="{mimetype}">'
            "您的浏览器不支持视频播放。"
            "</video>"
        )

        return {
            "name": _("网站视频链接"),
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }
