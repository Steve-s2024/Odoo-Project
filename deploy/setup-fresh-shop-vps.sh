#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a brand-new separated storefront from GitHub source only.
# This deliberately creates a fresh PostgreSQL database and never restores an
# existing database or filestore.

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export ODOO_USER="${ODOO_USER:-odoo-storefront}"
export ODOO_HOME="${ODOO_HOME:-/opt/odoo-storefront}"
export ODOO_SRC="${ODOO_SRC:-$ODOO_HOME/odoo-src}"
export PROJECT_DIR="${PROJECT_DIR:-$ODOO_HOME/project}"
export CUSTOM_ADDONS_DIR="${CUSTOM_ADDONS_DIR:-$PROJECT_DIR/custom_addons}"
export ODOO_CONFIG="${ODOO_CONFIG:-/etc/odoo-storefront.conf}"
export ODOO_ENV_FILE="${ODOO_ENV_FILE:-/etc/odoo-storefront.env}"
export ODOO_DB="${ODOO_DB:-odoo_storefront}"
export ODOO_DB_USER="${ODOO_DB_USER:-odoo-storefront}"
export ODOO_PORT="${ODOO_PORT:-8070}"
export ODOO_SERVICE="${ODOO_SERVICE:-odoo-storefront}"
export NGINX_SITE="${NGINX_SITE:-odoo-storefront}"
export INITIAL_MODULES="${INITIAL_MODULES:-storefront_api_bridge}"
export ODOO_WORKERS="${ODOO_WORKERS:-0}"
export CLIENT_MAX_BODY_SIZE="${CLIENT_MAX_BODY_SIZE:-1g}"
export INSTALL_SSL="${INSTALL_SSL:-0}"

exec bash "$script_dir/setup-odoo-vps.sh"
