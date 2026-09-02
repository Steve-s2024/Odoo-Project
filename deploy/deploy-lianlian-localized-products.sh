#!/usr/bin/env bash
set -Eeuo pipefail

root="/opt/odoo/project"
runtime="/opt/odoo"
database="odoo_prod"
service="odoo"
config="/etc/odoo.conf"
module="payment_lianlian"
release="${CODEX_ERP_RELEASE_DIR:?}/lianlian-localized-products.tar.gz"
expected_hash="3989ac2b17776e60adc520c027792be9b90ec01269579402b14acc798ea4b037"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/var/backups/odoo/lianlian-localized-products-${stamp}"
test_database="odoo_lianlian_localization_test_${stamp//[^0-9]/}"
source_changed=0
deployment_complete=0

cleanup() {
    sudo -u postgres dropdb --if-exists "${test_database}" >/dev/null 2>&1 || true
    if [[ "${source_changed}" = 1 && "${deployment_complete}" = 0 ]]; then
        rm -rf "${root}/custom_addons/${module}"
        tar -xzf "${backup_dir}/${module}.tar.gz" -C "${root}/custom_addons"
        chown -R odoo:odoo "${root}/custom_addons/${module}"
        systemctl start "${service}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

test -s "${release}"
test "$(sha256sum "${release}" | awk '{print $1}')" = "${expected_hash}"
grep -q '19.0.1.0.10' \
    < <(tar -xOzf "${release}" custom_addons/payment_lianlian/__manifest__.py)
grep -q '_lianlian_product_presentation' \
    < <(tar -xOzf "${release}" custom_addons/payment_lianlian/models/payment_transaction.py)

install -d -o root -g postgres -m 0750 "${backup_dir}"
sudo -u postgres pg_dump -Fc "${database}" > "${backup_dir}/${database}.dump"
test -s "${backup_dir}/${database}.dump"
chown postgres:postgres "${backup_dir}/${database}.dump"
chmod 0600 "${backup_dir}/${database}.dump"
tar -czf "${backup_dir}/${module}.tar.gz" \
    -C "${root}/custom_addons" "${module}"

sudo -u postgres createdb -T template0 -O odoo "${test_database}"
sudo -u odoo pg_restore --no-owner --no-privileges \
    -d "${test_database}" < "${backup_dir}/${database}.dump"
# Odoo recreates these cache-invalidation objects on every registry start.
# They are present in a physical production dump but must not be retained in
# an isolated clone, otherwise the first test registry load sees duplicates.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${test_database}" <<'SQL'
DO $do$
DECLARE
    signaling_object record;
BEGIN
    FOR signaling_object IN
        SELECT namespace.nspname, class.relname, class.relkind
          FROM pg_class AS class
          JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
         WHERE class.relname = ANY (ARRAY[
             'orm_signaling_registry',
             'orm_signaling_default',
             'orm_signaling_assets',
             'orm_signaling_stable',
             'orm_signaling_templates',
             'orm_signaling_routing',
             'orm_signaling_groups'
         ])
    LOOP
        IF signaling_object.relkind = 'S' THEN
            EXECUTE format(
                'DROP SEQUENCE %I.%I CASCADE',
                signaling_object.nspname,
                signaling_object.relname
            );
        ELSIF signaling_object.relkind IN ('r', 'p') THEN
            EXECUTE format(
                'DROP TABLE %I.%I CASCADE',
                signaling_object.nspname,
                signaling_object.relname
            );
        END IF;
    END LOOP;
END
$do$;
SQL

tar -xzf "${release}" -C "${root}"
source_changed=1
chown -R odoo:odoo "${root}/custom_addons/${module}"

# Validate the complete provider suite against an isolated copy of production.
# Tests mock provider calls and never create a real charge.
sudo -u odoo -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${test_database}" -u "${module}" \
    --test-enable --test-tags "/${module}" --stop-after-init \
    --http-port=18069 --gevent-port=18072 --log-level=test \
    2>&1 | tee "${backup_dir}/${module}-test.log"

sudo -u postgres dropdb "${test_database}"

systemctl stop "${service}"
sudo -u odoo -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${database}" -u "${module}" \
    --stop-after-init --no-http
systemctl start "${service}"

for attempt in $(seq 1 45); do
    if systemctl is-active --quiet "${service}" \
        && curl --fail --silent --max-time 5 \
            http://127.0.0.1:8069/api/v1/health >/dev/null; then
        break
    fi
    sleep 2
done
systemctl is-active --quiet "${service}"
curl --fail --silent --show-error --max-time 10 \
    http://127.0.0.1:8069/api/v1/health >/dev/null

verification="$(sudo -u odoo -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" shell \
    -c "${config}" -d "${database}" --no-http <<'PY'
module = env["ir.module.module"].sudo().search([("name", "=", "payment_lianlian")], limit=1)
assert module.state == "installed", module.state
assert module.latest_version == "19.0.1.0.10", module.latest_version
print("LIANLIAN_LOCALIZED_PRODUCTS_VERIFIED")
PY
)"
printf '%s\n' "${verification}"
grep -q 'LIANLIAN_LOCALIZED_PRODUCTS_VERIFIED' <<<"${verification}"

deployment_complete=1
printf 'backup_dir=%s\n' "${backup_dir}"
