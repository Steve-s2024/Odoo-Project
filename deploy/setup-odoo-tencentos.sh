#!/usr/bin/env bash
set -euo pipefail

# TencentOS / RHEL-family bootstrap for this Odoo project.
# This script builds Python 3.11 because TencentOS system Python is commonly
# older than the Python version required by Odoo 19.

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
PROJECT_REPO="${PROJECT_REPO:-https://github.com/Steve-s2024/Odoo-Project.git}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11.11}"
ODOO_SOURCE_ARCHIVE="${ODOO_SOURCE_ARCHIVE:-}"
UPDATE_ODOO_SOURCE="${UPDATE_ODOO_SOURCE:-0}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root: sudo bash deploy/setup-odoo-tencentos.sh"
    exit 1
fi

if ! command -v dnf >/dev/null 2>&1; then
    echo "This script requires TencentOS or another dnf-based Linux distribution."
    exit 1
fi

# The deployment can be launched from /root, which the PostgreSQL and Odoo
# system users cannot enter. Use a neutral working directory for child tools.
cd /

echo "==> Installing TencentOS system dependencies"
dnf install -y \
    ca-certificates \
    cyrus-sasl-devel \
    curl \
    fontconfig \
    gcc \
    gcc-c++ \
    git \
    libevent-devel \
    libffi-devel \
    libjpeg-turbo-devel \
    libpq-devel \
    libxml2-devel \
    libxslt-devel \
    libzip-devel \
    make \
    nginx \
    openldap-devel \
    openssl-devel \
    postgresql \
    postgresql-devel \
    postgresql-server \
    readline-devel \
    sqlite-devel \
    xz-devel \
    zlib-devel

# Chinese fonts and wkhtmltopdf availability vary by TencentOS repository.
dnf install -y google-noto-sans-cjk-fonts || true
dnf install -y wkhtmltopdf || echo "wkhtmltopdf is not available in this TencentOS repository; PDF reports need a later installation."

PYTHON_PREFIX="/opt/python-${PYTHON_VERSION}"
PYTHON_BIN="$PYTHON_PREFIX/bin/python3.11"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "==> Building Python $PYTHON_VERSION for Odoo 19"
    curl --fail --location --retry 4 \
        --output "/tmp/Python-${PYTHON_VERSION}.tgz" \
        "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"
    tar -xzf "/tmp/Python-${PYTHON_VERSION}.tgz" -C /tmp
    pushd "/tmp/Python-${PYTHON_VERSION}" >/dev/null
    ./configure --prefix="$PYTHON_PREFIX" --with-ensurepip=install
    make -j"$(nproc)"
    make altinstall
    popd >/dev/null
fi

echo "==> Initializing PostgreSQL"
if [[ ! -f /var/lib/pgsql/data/PG_VERSION ]]; then
    postgresql-setup --initdb
fi
systemctl enable --now postgresql

runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$ODOO_DB_USER'" | grep -q 1 || \
    runuser -u postgres -- createuser --createdb "$ODOO_DB_USER"
runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='$ODOO_DB'" | grep -q 1 || \
    runuser -u postgres -- createdb --owner="$ODOO_DB_USER" "$ODOO_DB"

if ! id "$ODOO_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$ODOO_HOME" --create-home --user-group "$ODOO_USER"
fi
mkdir -p "$ODOO_HOME"
chown -R "$ODOO_USER:$ODOO_USER" "$ODOO_HOME"

echo "==> Fetching Odoo $ODOO_VERSION and project customizations"
clone_odoo() {
    local attempt
    for attempt in 1 2 3; do
        echo "    Odoo download attempt $attempt/3"
        rm -rf "$ODOO_SRC"
        if runuser -u "$ODOO_USER" -- env GIT_TERMINAL_PROMPT=0 \
            git -c http.version=HTTP/1.1 clone \
            --branch "$ODOO_VERSION" --depth 1 --single-branch --no-tags \
            https://github.com/odoo/odoo.git "$ODOO_SRC"; then
            return 0
        fi
        sleep "$((attempt * 10))"
    done
    echo "Unable to download Odoo after three attempts. Check the CVM's outbound Internet access to github.com."
    return 1
}

if [[ -n "$ODOO_SOURCE_ARCHIVE" ]]; then
    if [[ ! -f "$ODOO_SOURCE_ARCHIVE" ]]; then
        echo "Odoo source archive was not found: $ODOO_SOURCE_ARCHIVE"
        exit 1
    fi
    echo "    Extracting Odoo source archive: $ODOO_SOURCE_ARCHIVE"
    rm -rf "$ODOO_SRC"
    mkdir -p "$ODOO_SRC"
    tar -xzf "$ODOO_SOURCE_ARCHIVE" -C "$ODOO_SRC"
    chown -R "$ODOO_USER:$ODOO_USER" "$ODOO_SRC"
elif git -C "$ODOO_SRC" rev-parse --verify HEAD >/dev/null 2>&1; then
    if [[ "$UPDATE_ODOO_SOURCE" == "1" ]]; then
        runuser -u "$ODOO_USER" -- git -C "$ODOO_SRC" pull --ff-only
    else
        echo "    Reusing existing Odoo source; set UPDATE_ODOO_SOURCE=1 to fetch updates."
    fi
else
    clone_odoo
fi
if [[ ! -d "$PROJECT_DIR/.git" ]]; then
    runuser -u "$ODOO_USER" -- git clone "$PROJECT_REPO" "$PROJECT_DIR"
else
    runuser -u "$ODOO_USER" -- git -C "$PROJECT_DIR" pull --ff-only
fi

echo "==> Creating Python virtual environment"
runuser -u "$ODOO_USER" -- "$PYTHON_BIN" -m venv "$ODOO_HOME/venv"
runuser -u "$ODOO_USER" -- "$ODOO_HOME/venv/bin/pip" install --upgrade pip wheel setuptools

# TencentOS OpenLDAP no longer provides the legacy libldap_r linker alias.
# Build the Odoo-pinned package against its standard ldap/lber libraries first.
LDAP_BUILD_DIR="/tmp/python-ldap-3.4.0"
rm -rf "$LDAP_BUILD_DIR" /tmp/python-ldap-3.4.0.tar.gz
runuser -u "$ODOO_USER" -- "$ODOO_HOME/venv/bin/pip" download \
    --no-binary=:all: --no-deps --dest /tmp python-ldap==3.4.0
tar -xzf /tmp/python-ldap-3.4.0.tar.gz -C /tmp
sed -i 's/^libs = .*/libs = ldap lber sasl2 ssl crypto/' "$LDAP_BUILD_DIR/setup.cfg"
chown -R "$ODOO_USER:$ODOO_USER" "$LDAP_BUILD_DIR"
runuser -u "$ODOO_USER" -- "$ODOO_HOME/venv/bin/pip" install "$LDAP_BUILD_DIR"

runuser -u "$ODOO_USER" -- "$ODOO_HOME/venv/bin/pip" install -r "$ODOO_SRC/requirements.txt"
runuser -u "$ODOO_USER" -- "$ODOO_HOME/venv/bin/pip" install openpyxl cryptography

ADMIN_PASSWD="$(openssl rand -hex 24)"
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

echo "==> Initializing custom Odoo modules"
runuser -u "$ODOO_USER" -- "$ODOO_HOME/venv/bin/python" "$ODOO_SRC/odoo-bin" \
    -c "$ODOO_CONFIG" -d "$ODOO_DB" \
    -i stock_subwarehouse_hierarchy --stop-after-init --no-http

cat > /etc/nginx/conf.d/odoo.conf <<EOF
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 200m;
    proxy_read_timeout 720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout 720s;

    location / {
        proxy_pass http://127.0.0.1:$ODOO_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF

nginx -t
systemctl daemon-reload
systemctl enable --now odoo nginx

echo
echo "Done. Open http://SERVER_PUBLIC_IP"
echo "Odoo service: systemctl status odoo"
