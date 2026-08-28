#!/usr/bin/env bash
set -euo pipefail

# Export a root-only relocation bundle.  The output contains production data
# and secrets and must never be uploaded to GitHub.

ODOO_DB="${ODOO_DB:-odoo_prod}"
ODOO_USER="${ODOO_USER:-odoo}"
ODOO_SERVICE="${ODOO_SERVICE:-odoo}"
ODOO_HOME="${ODOO_HOME:-/opt/odoo}"
ODOO_SRC="${ODOO_SRC:-$ODOO_HOME/odoo-src}"
PROJECT_DIR="${PROJECT_DIR:-$ODOO_HOME/project}"
ODOO_CONFIG="${ODOO_CONFIG:-/etc/odoo.conf}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/var/backups/odoo}"
STOP_ODOO="${STOP_ODOO:-0}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root: sudo bash deploy/export-erp-relocation-bundle.sh" >&2
    exit 1
fi

for required in pg_dump tar sha256sum; do
    command -v "$required" >/dev/null 2>&1 || {
        echo "Required command is missing: $required" >&2
        exit 1
    }
done

for required_path in "$ODOO_SRC/odoo-bin" "$PROJECT_DIR/custom_addons" "$ODOO_CONFIG"; do
    [[ -e "$required_path" ]] || {
        echo "Required ERP path is missing: $required_path" >&2
        exit 1
    }
done

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
bundle="$OUTPUT_ROOT/erp-relocation-$timestamp"
install -d -m 0700 "$bundle"

service_was_stopped=0
restart_service() {
    if [[ "$service_was_stopped" == "1" ]]; then
        systemctl start "$ODOO_SERVICE"
    fi
}
trap restart_service EXIT

if [[ "$STOP_ODOO" == "1" ]] && systemctl is-active --quiet "$ODOO_SERVICE"; then
    systemctl stop "$ODOO_SERVICE"
    service_was_stopped=1
fi

sudo -u postgres pg_dump -Fc "$ODOO_DB" > "$bundle/$ODOO_DB.dump"
test -s "$bundle/$ODOO_DB.dump"

tar \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='node_modules' \
    -czf "$bundle/custom-addons.tar.gz" \
    -C "$PROJECT_DIR" custom_addons

tar -czf "$bundle/odoo-source.tar.gz" -C "$ODOO_SRC" .

filestore="$ODOO_HOME/data/filestore/$ODOO_DB"
if [[ -d "$filestore" ]]; then
    tar -czf "$bundle/filestore.tar.gz" -C "$filestore" .
else
    echo "No filestore directory found at $filestore" > "$bundle/filestore-missing.txt"
fi

install -m 0600 "$ODOO_CONFIG" "$bundle/odoo.conf"
systemctl cat "$ODOO_SERVICE" > "$bundle/$ODOO_SERVICE.service.txt"

if [[ -d /etc/nginx ]]; then
    tar -czf "$bundle/nginx-config.tar.gz" -C /etc nginx
fi

# Include root-owned Odoo environment files when present.  Their contents may
# include SMTP, payment, or Shop API secrets, so the bundle remains mode 0700.
find /etc -maxdepth 1 -type f -name 'odoo*.env' -exec cp --preserve=mode,timestamps '{}' "$bundle/" ';'

{
    printf 'created_utc=%s\n' "$timestamp"
    printf 'hostname=%s\n' "$(hostname -f 2>/dev/null || hostname)"
    printf 'database=%s\n' "$ODOO_DB"
    printf 'project_commit=%s\n' "$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || echo unavailable)"
    printf 'project_branch=%s\n' "$(git -C "$PROJECT_DIR" branch --show-current 2>/dev/null || echo unavailable)"
    printf 'project_status_begin\n'
    git -C "$PROJECT_DIR" status --short 2>/dev/null || true
    printf 'project_status_end\n'
    printf 'odoo_release='
    "$ODOO_HOME/venv/bin/python" -c "import sys; sys.path.insert(0, '$ODOO_SRC'); import odoo.release as r; print(r.version)"
    printf 'odoo_bin_sha256='
    sha256sum "$ODOO_SRC/odoo-bin" | cut -d' ' -f1
    "$ODOO_HOME/venv/bin/python" --version
    "$ODOO_HOME/venv/bin/pip" freeze
    node --version 2>/dev/null || true
    npm --version 2>/dev/null || true
    wkhtmltopdf --version 2>/dev/null || true
    psql --version
} > "$bundle/runtime-manifest.txt"

(
    cd "$bundle"
    sha256sum ./* > SHA256SUMS
)

chmod -R go-rwx "$bundle"

if [[ "$service_was_stopped" == "1" ]]; then
    systemctl start "$ODOO_SERVICE"
    service_was_stopped=0
fi

printf 'ERP relocation bundle: %s\n' "$bundle"
printf 'Checksum manifest: %s/SHA256SUMS\n' "$bundle"
