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

## Outgoing Email

Odoo needs a real SMTP account before it can send quotations, password reset
messages, notifications, and other email. Keep the SMTP password outside Git.

On the VPS, create a root-only secret file:

```bash
sudo nano /etc/odoo-mail.env
```

Example:

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=erp@example.com
SMTP_PASSWORD=your_app_password
SMTP_ENCRYPTION=starttls
SMTP_FROM_FILTER=erp@example.com
```

Secure it and apply it:

```bash
sudo chmod 600 /etc/odoo-mail.env
cd /opt/odoo/project
sudo bash deploy/configure-odoo-email.sh
```

Then test it in Odoo:

```text
设置 -> 技术 -> 电子邮件 -> 外发邮件服务器 -> Test Connection
```

Provider notes:

- For Gmail or Google Workspace, use an app password, not your normal login password.
- For Tencent/QQ/Exmail, use the provider's SMTP authorization code/password.
- For Alibaba Cloud mail or company mail, use the SMTP host, port, and encryption from the mail admin console.
- Many cloud providers block outbound port 25. Prefer port `587` with `starttls` or port `465` with `ssl`.

If Odoo still says it cannot contact the mail server, check:

```bash
sudo journalctl -u odoo -n 100 --no-pager
nc -vz "$SMTP_HOST" "$SMTP_PORT"
```
