# Odoo Project Backup

This repository stores the project customization layer for a local Odoo 19 ERP build.

The full Odoo framework is not committed here. Re-clone it from upstream:

```powershell
git clone --branch 19.0 --depth 1 https://github.com/odoo/odoo.git odoo-git
```

The custom addon lives in:

```text
custom_addons/stock_subwarehouse_hierarchy
```

## Local Startup

From this workspace:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\outputs\start-odoo.ps1"
```

Then open:

```text
http://127.0.0.1:8069/
```

## License Notes

Odoo is distributed under its upstream open-source licenses. This repository does not remove or replace those notices. The custom addon is declared as `LGPL-3` in its Odoo manifest.

Generated spreadsheets, logs, local database files, downloaded tools, and virtual environments are intentionally excluded from this backup.

## VPS Deployment

Deployment scripts are in:

```text
deploy/
```

For a fresh Ubuntu VPS, see:

```text
deploy/README.md
```
