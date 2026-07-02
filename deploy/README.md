# VPS Deployment

Use `setup-odoo-vps.sh` on a fresh Ubuntu VPS.

Minimum recommended test server:

```text
1 vCPU / 2 GB RAM / 50 GB disk
```

Run without a domain first:

```bash
sudo PROJECT_REPO=https://github.com/Steve-s2024/Odoo-Project.git \
  bash deploy/setup-odoo-vps.sh
```

Run with a domain and HTTPS after DNS points to the VPS:

```bash
sudo DOMAIN=erp.example.com ADMIN_EMAIL=admin@example.com INSTALL_SSL=1 \
  PROJECT_REPO=https://github.com/Steve-s2024/Odoo-Project.git \
  bash deploy/setup-odoo-vps.sh
```

What it installs:

- PostgreSQL on the same VPS
- Odoo 19 source from upstream GitHub
- This project repo under `/opt/odoo/project`
- Custom addons from `/opt/odoo/project/custom_addons`
- Python virtual environment
- Nginx reverse proxy
- Optional Let's Encrypt HTTPS
- `systemd` service named `odoo`

Useful commands:

```bash
sudo systemctl status odoo
sudo journalctl -u odoo -f
sudo systemctl restart odoo
sudo nginx -t
```
