#!/usr/bin/env bash
set -euo pipefail

# Ubuntu VPS bootstrap for this Odoo project.
#
# Example:
#   sudo DOMAIN=erp.example.com ADMIN_EMAIL=admin@example.com INSTALL_SSL=1 \
#     PROJECT_REPO=https://github.com/Steve-s2024/Odoo-Project.git \
#     bash deploy/setup-odoo-vps.sh
#
# For first testing you can omit DOMAIN/SSL and open http://SERVER_IP.

ODOO_VERSION="${ODOO_VERSION:-19.0}"
ODOO_USER="${ODOO_USER:-odoo}"
ODOO_HOME="${ODOO_HOME:-/opt/odoo}"
ODOO_SRC="${ODOO_SRC:-$ODOO_HOME/odoo-src}"
PROJECT_DIR="${PROJECT_DIR:-$ODOO_HOME/project}"
CUSTOM_ADDONS_DIR="${CUSTOM_ADDONS_DIR:-$PROJECT_DIR/custom_addons}"
ODOO_CONFIG="${ODOO_CONFIG:-/etc/odoo.conf}"
ODOO_DB="${ODOO_DB:-odoo_prod}"
ODOO_DB_USER="${ODOO_DB_USER:-odoo}"
ODOO_PORT="${ODOO_PORT:-8069}"
DOMAIN="${DOMAIN:-}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
INSTALL_SSL="${INSTALL_SSL:-0}"
PROJECT_REPO="${PROJECT_REPO:-https://github.com/Steve-s2024/Odoo-Project.git}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root: sudo bash deploy/setup-odoo-vps.sh"
    exit 1
fi

echo "==> Updating Ubuntu packages"
apt-get update
apt-get upgrade -y

echo "==> Installing system dependencies"
apt-get install -y \
    build-essential \
    ca-certificates \
    curl \
    fontconfig \
    fonts-noto-cjk \
    git \
    libevent-dev \
    libfreetype6-dev \
    libjpeg-dev \
    libldap2-dev \
    libpq-dev \
    libsasl2-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    libzip-dev \
    nginx \
    node-less \
    npm \
    postgresql \
    postgresql-client \
    python3-dev \
    python3-pip \
    python3-venv \
    python3-wheel \
    rsync \
    ufw \
    zlib1g-dev

if apt-get install --simulate -y wkhtmltopdf >/dev/null 2>&1; then
    echo "==> Installing wkhtmltopdf"
    apt-get install -y wkhtmltopdf
else
    echo "==> wkhtmltopdf is not available from this Debian release; continuing without it"
    echo "    Odoo will run, but PDF report rendering may be unavailable until wkhtmltopdf is installed separately."
fi

if ! id "$ODOO_USER" >/dev/null 2>&1; then
    echo "==> Creating system user: $ODOO_USER"
    adduser --system --home "$ODOO_HOME" --group "$ODOO_USER"
fi

mkdir -p "$ODOO_HOME"
chown -R "$ODOO_USER:$ODOO_USER" "$ODOO_HOME"

echo "==> Creating PostgreSQL role and database"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$ODOO_DB_USER'" | grep -q 1 || \
    sudo -u postgres createuser --createdb "$ODOO_DB_USER"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$ODOO_DB'" | grep -q 1 || \
    sudo -u postgres createdb --owner="$ODOO_DB_USER" "$ODOO_DB"

if [[ ! -d "$ODOO_SRC/.git" ]]; then
    echo "==> Cloning Odoo $ODOO_VERSION"
    sudo -u "$ODOO_USER" git clone --branch "$ODOO_VERSION" --depth 1 https://github.com/odoo/odoo.git "$ODOO_SRC"
else
    echo "==> Updating Odoo source"
    sudo -u "$ODOO_USER" git -C "$ODOO_SRC" fetch origin "$ODOO_VERSION"
    sudo -u "$ODOO_USER" git -C "$ODOO_SRC" checkout "$ODOO_VERSION"
    sudo -u "$ODOO_USER" git -C "$ODOO_SRC" pull --ff-only
fi

if [[ ! -d "$PROJECT_DIR/.git" ]]; then
    echo "==> Cloning project customizations"
    sudo -u "$ODOO_USER" git clone "$PROJECT_REPO" "$PROJECT_DIR"
else
    echo "==> Updating project customizations"
    sudo -u "$ODOO_USER" git -C "$PROJECT_DIR" pull --ff-only
fi

echo "==> Creating Python virtual environment"
sudo -u "$ODOO_USER" python3 -m venv "$ODOO_HOME/venv"
sudo -u "$ODOO_USER" "$ODOO_HOME/venv/bin/python" -m pip install --upgrade pip wheel setuptools
sudo -u "$ODOO_USER" "$ODOO_HOME/venv/bin/pip" install -r "$ODOO_SRC/requirements.txt"
sudo -u "$ODOO_USER" "$ODOO_HOME/venv/bin/pip" install openpyxl

ADMIN_PASSWD="$(openssl rand -hex 24)"

echo "==> Writing Odoo config: $ODOO_CONFIG"
cat > "$ODOO_CONFIG" <<EOF
[options]
admin_passwd = $ADMIN_PASSWD
addons_path = $ODOO_SRC/odoo/addons,$ODOO_SRC/addons,$CUSTOM_ADDONS_DIR
data_dir = $ODOO_HOME/data
db_host = False
db_port = False
db_user = $ODOO_DB_USER
db_password = False
db_name = $ODOO_DB
http_interface = 127.0.0.1
http_port = $ODOO_PORT
proxy_mode = True
workers = 0
limit_memory_soft = 1073741824
limit_memory_hard = 1610612736
limit_time_cpu = 600
limit_time_real = 1200
logfile = /var/log/odoo/odoo.log
EOF
chown "$ODOO_USER:$ODOO_USER" "$ODOO_CONFIG"
chmod 640 "$ODOO_CONFIG"

mkdir -p "$ODOO_HOME/data" /var/log/odoo
chown -R "$ODOO_USER:$ODOO_USER" "$ODOO_HOME/data" /var/log/odoo

echo "==> Writing systemd service"
cat > /etc/systemd/system/odoo.service <<EOF
[Unit]
Description=Odoo ERP
After=network.target postgresql.service

[Service]
Type=simple
User=$ODOO_USER
Group=$ODOO_USER
ExecStart=$ODOO_HOME/venv/bin/python $ODOO_SRC/odoo-bin -c $ODOO_CONFIG
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

SERVER_NAME="_"
if [[ -n "$DOMAIN" ]]; then
    SERVER_NAME="$DOMAIN"
fi

echo "==> Writing Nginx site"
cat > /etc/nginx/sites-available/odoo <<EOF
server {
    listen 80;
    server_name $SERVER_NAME;

    client_max_body_size 200m;
    proxy_read_timeout 720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout 720s;

    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Real-IP \$remote_addr;

    location / {
        proxy_pass http://127.0.0.1:$ODOO_PORT;
    }
}
EOF
ln -sfn /etc/nginx/sites-available/odoo /etc/nginx/sites-enabled/odoo
rm -f /etc/nginx/sites-enabled/default
nginx -t

echo "==> Enabling firewall"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "==> Starting Odoo and Nginx"
systemctl daemon-reload
systemctl enable --now odoo
systemctl reload nginx

if [[ "$INSTALL_SSL" == "1" ]]; then
    if [[ -z "$DOMAIN" || -z "$ADMIN_EMAIL" ]]; then
        echo "INSTALL_SSL=1 requires DOMAIN and ADMIN_EMAIL."
        exit 1
    fi
    echo "==> Installing HTTPS certificate for $DOMAIN"
    apt-get install -y certbot python3-certbot-nginx
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$ADMIN_EMAIL" --redirect
fi

echo
echo "Done."
echo "Odoo URL: http://${DOMAIN:-SERVER_IP}"
if [[ "$INSTALL_SSL" == "1" ]]; then
    echo "HTTPS URL: https://$DOMAIN"
fi
echo "Master password saved in $ODOO_CONFIG as admin_passwd."
echo "Check service: sudo systemctl status odoo"
echo "Logs: sudo journalctl -u odoo -f"
