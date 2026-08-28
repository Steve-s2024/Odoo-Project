# ERP relocation runbook

This repository is the source of truth for the custom ERP code.  A Git checkout
alone is not a complete Odoo migration: PostgreSQL, the Odoo filestore, the
upstream Odoo source, and host-only secrets must move with it.

## Locked live environment

Inventory captured from the production ERP on 2026-08-28:

- OS: TencentOS Server 4
- Odoo: 19.0
- Python: 3.11.11
- PostgreSQL client/server family: 15
- Node.js: 20.20.2; npm: 10.8.2
- wkhtmltopdf: 0.12.6.1 with patched Qt
- openpyxl: 3.0.9
- cryptography: 46.0.7
- alipay-sdk-python: 3.7.1160
- wechatpayv3: 2.0.2
- pyOpenSSL: 25.3.0
- urllib3: 1.26.20
- Odoo source `odoo-bin` SHA-256:
  `4d75e4c727e87a617f751c40bf4feb2cc745f4aec55b96c02046e2e7faea247f`

The production Odoo source tree has no Git metadata.  For an exact relocation,
copy the `odoo-source.tar.gz` produced by the export script rather than cloning
the moving `19.0` branch and assuming it is identical.

Installed custom modules that must be present before the restored database is
started:

```text
payment_alipay                 19.0.1.1.1
payment_lianlian               19.0.1.0.9
payment_wechatpay              19.0.1.1.0
sales_dashboard_separation     19.0.1.0.0
shop_api                       19.0.1.22.1
stock_subwarehouse_hierarchy   19.0.1.0.47
```

## What belongs in Git

- `custom_addons/` source code, tests, XML, and static assets
- deployment scripts and public dependency lock files
- this runbook

Never commit database dumps, filestore archives, `/etc/odoo.conf`, environment
files, TLS private keys, SSH keys, payment keys, API keys, or SMTP passwords.

## Create the relocation bundle on the old ERP

For a rehearsal backup while the ERP remains online:

```bash
sudo bash /opt/odoo/project/deploy/export-erp-relocation-bundle.sh
```

For the final consistent cutover, put the ERP into a maintenance window and
stop Odoo during the export:

```bash
sudo STOP_ODOO=1 \
  bash /opt/odoo/project/deploy/export-erp-relocation-bundle.sh
```

The command prints the root-only bundle directory and a SHA-256 manifest.  Copy
that directory to the replacement server through SSH/SCP or private object
storage.  Do not upload it to GitHub.

## Prepare the replacement server

1. Install the same OS/runtime family, PostgreSQL 15, Python 3.11.11, Node 20,
   and wkhtmltopdf 0.12.6.1 with patched Qt.
2. Clone this repository and check out the immutable commit supplied with the
   release, not merely the latest branch:

   ```bash
   git clone https://github.com/Steve-s2024/Odoo-Project.git /opt/odoo/project
   git -C /opt/odoo/project checkout --detach <ERP_PROJECT_COMMIT>
   ```

3. Extract `odoo-source.tar.gz` to `/opt/odoo/odoo-src` and verify the
   `odoo-bin` SHA-256 above.
4. Build the virtual environment from the Odoo requirements and then install
   `deploy/payment-sdk-requirements.txt`, `openpyxl==3.0.9`, and
   `cryptography==46.0.7`.
5. Run `npm ci --omit=dev` in
   `custom_addons/payment_lianlian/sdk`; `node_modules` is intentionally not in
   Git.
6. Restore the database dump as `odoo_prod` and restore the filestore to
   `/opt/odoo/data/filestore/odoo_prod` with ownership `odoo:odoo`.
7. Restore `/etc/odoo.conf`, the Odoo service definition, Nginx configuration,
   and root-owned environment/credential files from the secure bundle.  Review
   IP addresses and DNS names before starting services.
8. Upgrade the six custom modules once against the restored database with
   `--stop-after-init --no-http`, then start Odoo.

## Cutover verification

Before changing DNS or Shop API destinations, verify:

- Odoo and PostgreSQL services are active;
- `/api/v1/health` returns `status: ok`;
- the database UUID and attachment/filestore counts match the old ERP;
- internal users can sign in and open Products, Inventory, Sales, and Website
  API;
- product images and documents load;
- Shop authentication, stock reservation, order creation, payment callbacks,
  refund events, and product pushes work against the replacement ERP;
- scheduled jobs are disabled on one side during cutover so they cannot run on
  both ERP databases;
- a new encrypted backup of the replacement ERP succeeds.

Keep the old ERP stopped but recoverable until the verification is complete.
Only then change Shop API endpoints/DNS and retire the old host.
