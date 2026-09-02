#!/usr/bin/env bash
set -Eeuo pipefail

release="${1:-/tmp/account-login-cooldown-storefront.tar.gz}"
expected_hash="${2:?expected release SHA-256 is required}"
public_host="${3:?public host is required}"
runtime="/opt/odoo-storefront"
config="/etc/odoo-storefront.conf"
service="odoo-storefront"
database="odoo_storefront"
module="storefront_api_bridge"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/var/backups/odoo-storefront/login-cooldown-${stamp}"
test_database="odoo_storefront_login_cooldown_test_${stamp//[^0-9]/}"
test_root="/tmp/storefront-login-cooldown-test-${stamp}"
service_user="$(systemctl show -p User --value "${service}")"
db_owner="$(sudo -u postgres psql -Atqc \
    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='${database}'")"

if [[ -d "${runtime}/project/custom_addons" ]]; then
    addons_root="${runtime}/project/custom_addons"
elif [[ -d "${runtime}/custom_addons" ]]; then
    addons_root="${runtime}/custom_addons"
else
    printf 'Storefront custom addons directory was not found.\n' >&2
    exit 1
fi
project_root="$(dirname "${addons_root}")"
source_changed=0
deployment_complete=0

cleanup() {
    sudo -u postgres dropdb --if-exists "${test_database}" >/dev/null 2>&1 || true
    rm -rf -- "${test_root}"
    if [[ "${source_changed}" = 1 && "${deployment_complete}" = 0 ]]; then
        rm -rf -- "${addons_root:?}/${module}"
        tar -xzf "${backup_dir}/${module}.tar.gz" -C "${addons_root}"
        chown -R "${service_user}:${service_user}" "${addons_root}/${module}"
        systemctl start "${service}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

test -n "${service_user}"
test -n "${db_owner}"
test -s "${release}"
test "$(sha256sum "${release}" | awk '{print $1}')" = "${expected_hash}"
grep -q 'login_cooldown' \
    < <(tar -xOzf "${release}" custom_addons/${module}/controllers/native_login.py)
grep -q 'test_login_cooldown_display' \
    < <(tar -xOzf "${release}" custom_addons/${module}/tests/__init__.py)

install -d -m 0750 "${backup_dir}"
sudo -u postgres pg_dump -Fc "${database}" > "${backup_dir}/${database}.dump"
test -s "${backup_dir}/${database}.dump"
tar -czf "${backup_dir}/${module}.tar.gz" -C "${addons_root}" "${module}"

sudo -u postgres createdb -T template0 -O "${db_owner}" "${test_database}"
sudo -u "${service_user}" pg_restore --no-owner --no-privileges \
    -d "${test_database}" < "${backup_dir}/${database}.dump"
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
             'orm_signaling_registry', 'orm_signaling_default',
             'orm_signaling_assets', 'orm_signaling_stable',
             'orm_signaling_templates', 'orm_signaling_routing',
             'orm_signaling_groups'
         ])
    LOOP
        IF signaling_object.relkind = 'S' THEN
            EXECUTE format('DROP SEQUENCE %I.%I CASCADE', signaling_object.nspname, signaling_object.relname);
        ELSIF signaling_object.relkind IN ('r', 'p') THEN
            EXECUTE format('DROP TABLE %I.%I CASCADE', signaling_object.nspname, signaling_object.relname);
        END IF;
    END LOOP;
END
$do$;
SQL

install -d -o "${service_user}" -g "${service_user}" -m 0755 \
    "${test_root}/custom_addons"
cp -a "${addons_root}/${module}" "${test_root}/custom_addons/${module}"
tar -xzf "${release}" -C "${test_root}"
chown -R "${service_user}:${service_user}" "${test_root}"
test_log="${backup_dir}/storefront-login-cooldown-tests.log"
sudo -u "${service_user}" -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${test_database}" \
    --addons-path="${test_root}/custom_addons,${addons_root},/opt/odoo-storefront/odoo-src/odoo/addons,/opt/odoo-storefront/odoo-src/addons" \
    -u "${module}" --test-enable \
    --test-tags '/storefront_api_bridge:TestStorefrontLoginCooldownDisplay' \
    --stop-after-init --http-port=18070 --gevent-port=18073 --log-level=test \
    2>&1 | tee "${test_log}"
if grep -Eq '([1-9][0-9]* failed|[1-9][0-9]* error)' "${test_log}"; then
    printf 'Odoo reported a failing storefront login-cooldown test.\n' >&2
    exit 1
fi
sudo -u postgres dropdb "${test_database}"

systemctl stop "${service}"
tar -xzf "${release}" -C "${project_root}"
source_changed=1
chown -R "${service_user}:${service_user}" "${addons_root}/${module}"
sudo -u "${service_user}" -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${database}" -u "${module}" \
    --stop-after-init --no-http
systemctl start "${service}"

for attempt in $(seq 1 45); do
    if systemctl is-active --quiet "${service}" \
        && curl --fail --silent --max-time 5 -H "Host: ${public_host}" \
            http://127.0.0.1:8070/web/login >/dev/null; then
        break
    fi
    sleep 2
done
systemctl is-active --quiet "${service}"
curl --fail --silent --show-error --max-time 10 -H "Host: ${public_host}" \
    http://127.0.0.1:8070/web/login >/dev/null
grep -q 'login_cooldown' "${addons_root}/${module}/controllers/native_login.py"
grep -q 'Password reset remains available' "${addons_root}/${module}/controllers/native_login.py"

deployment_complete=1
printf 'STOREFRONT_LOGIN_COOLDOWN_VERIFIED host=%s backup=%s\n' \
    "${public_host}" "${backup_dir}"
