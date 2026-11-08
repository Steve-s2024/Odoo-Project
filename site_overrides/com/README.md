# .com-only files

These files are what www.sunwintersports.com runs live and differ from `custom_addons/` on purpose:

- `storefront_terms_template/`: .com terms of service.
- `storefront_video_upload_access/`: .com video upload button.

Odoo does not load this folder. Since 2026-11-08 both shops run `custom_addons/` from main exactly,
and .com additionally runs these files on top (paths match `custom_addons/` on the .com server).
`.cn` uses the versions in `custom_addons/`. Keep this copy in step with any .com change.
