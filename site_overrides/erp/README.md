# ERP-only files

These files are what the ERP (erp.sunwintersports.cn) runs live and differ from `custom_addons/` on purpose:

- `stock_subwarehouse_hierarchy/controllers/__init__.py`: does not load `erp_auth`. The ERP has its own login setup
  (`/` opens dashboards, title "思安奇ERP系统登录", sign-up off); loading `erp_auth` would send `/` to Discuss and
  restyle the login page, because `sun.erp_login.enabled` is True on the ERP.
- `stock_subwarehouse_hierarchy/models/ir_http.py`: older page-title rules (ERP tab titles end in "| 思安奇").

Everything else on the ERP equals `custom_addons/` (checked 2026-11-08). Odoo does not load this folder.
To change the ERP: change the repo, back up, patch the server, then keep this copy in step.
