# .com-only files

These files are what www.sunwintersports.com runs live and differ from `custom_addons/` on purpose:

- `storefront_terms_template/`: .com terms of service.
- `storefront_video_upload_access/`: .com video upload button.

They are a record only. Odoo does not load this folder, and `.cn` uses the versions in `custom_addons/`.
Paths match `custom_addons/` on the .com server. Change .com by patching exact lines on the server, then update the copy here.
