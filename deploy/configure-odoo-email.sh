#!/usr/bin/env bash
set -euo pipefail

# Configure Odoo's outgoing email server from environment variables.
#
# Keep secrets out of Git by writing them to /etc/odoo-mail.env:
#
#   SMTP_HOST=smtp.example.com
#   SMTP_PORT=587
#   SMTP_USER=erp@example.com
#   SMTP_PASSWORD=your_app_password
#   SMTP_ENCRYPTION=starttls
#   SMTP_FROM_FILTER=erp@example.com
#
# Then run:
#
#   sudo bash /opt/odoo/project/deploy/configure-odoo-email.sh

ODOO_USER="${ODOO_USER:-odoo}"
ODOO_HOME="${ODOO_HOME:-/opt/odoo}"
ODOO_SRC="${ODOO_SRC:-$ODOO_HOME/odoo-src}"
ODOO_CONFIG="${ODOO_CONFIG:-/etc/odoo.conf}"
ODOO_MAIL_ENV="${ODOO_MAIL_ENV:-/etc/odoo-mail.env}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root: sudo bash deploy/configure-odoo-email.sh"
    exit 1
fi

if [[ -f "$ODOO_MAIL_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ODOO_MAIL_ENV"
    set +a
fi

required_vars=(SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD)
for var_name in "${required_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
        echo "Missing required value: $var_name"
        echo "Set it in $ODOO_MAIL_ENV or pass it as an environment variable."
        exit 1
    fi
done

SMTP_ENCRYPTION="${SMTP_ENCRYPTION:-starttls}"
SMTP_FROM_FILTER="${SMTP_FROM_FILTER:-$SMTP_USER}"
SMTP_SERVER_NAME="${SMTP_SERVER_NAME:-Company SMTP}"
ODOO_DB="${ODOO_DB:-$(awk -F= '/^db_name/ {gsub(/[[:space:]]/,"",$2); print $2}' "$ODOO_CONFIG")}"

if [[ -z "$ODOO_DB" ]]; then
    echo "Could not determine database name. Set ODOO_DB or db_name in $ODOO_CONFIG."
    exit 1
fi

tmp_script="$(mktemp)"
trap 'rm -f "$tmp_script"' EXIT

cat > "$tmp_script" <<'PY'
import os

server_name = os.environ["SMTP_SERVER_NAME"]
smtp_host = os.environ["SMTP_HOST"]
smtp_port = int(os.environ["SMTP_PORT"])
smtp_user = os.environ["SMTP_USER"]
smtp_password = os.environ["SMTP_PASSWORD"]
smtp_encryption = os.environ["SMTP_ENCRYPTION"]
from_filter = os.environ["SMTP_FROM_FILTER"]

MailServer = env["ir.mail_server"].sudo()
server = MailServer.search([("name", "=", server_name)], limit=1)
values = {
    "name": server_name,
    "sequence": 1,
    "active": True,
    "smtp_authentication": "login",
    "smtp_host": smtp_host,
    "smtp_port": smtp_port,
    "smtp_user": smtp_user,
    "smtp_pass": smtp_password,
    "smtp_encryption": smtp_encryption,
    "from_filter": from_filter,
}
if server:
    server.write(values)
else:
    server = MailServer.create(values)

Config = env["ir.config_parameter"].sudo()
Config.set_param("mail.default.from_filter", from_filter)
if "@" in from_filter:
    Config.set_param("mail.default.from", from_filter)
if os.environ.get("SMTP_CATCHALL_DOMAIN"):
    Config.set_param("mail.catchall.domain", os.environ["SMTP_CATCHALL_DOMAIN"])

print(f"Configured outgoing mail server {server.display_name} for {smtp_host}:{smtp_port}")
PY

sudo -u "$ODOO_USER" env \
    SMTP_SERVER_NAME="$SMTP_SERVER_NAME" \
    SMTP_HOST="$SMTP_HOST" \
    SMTP_PORT="$SMTP_PORT" \
    SMTP_USER="$SMTP_USER" \
    SMTP_PASSWORD="$SMTP_PASSWORD" \
    SMTP_ENCRYPTION="$SMTP_ENCRYPTION" \
    SMTP_FROM_FILTER="$SMTP_FROM_FILTER" \
    SMTP_CATCHALL_DOMAIN="${SMTP_CATCHALL_DOMAIN:-}" \
    "$ODOO_HOME/venv/bin/python" "$ODOO_SRC/odoo-bin" shell \
        -c "$ODOO_CONFIG" \
        -d "$ODOO_DB" \
        --no-http < "$tmp_script"

echo "Restarting Odoo"
systemctl restart odoo
echo "Done. In Odoo, use Settings -> Technical -> Email -> Outgoing Mail Servers -> Test Connection."
