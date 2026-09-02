#!/usr/bin/env bash
set -Eeuo pipefail

runtime="/opt/odoo-storefront"
config="/etc/odoo-storefront.conf"
service="odoo-storefront"
database="odoo_storefront"
module="storefront_terms_template"
archive="${CODEX_STOREFRONT_RELEASE:?}/storefront-terms-template.tar.gz"
expected_hash="9d07dff38e463d53f84883a4372aaed880124defe9f9d096b4398bf3647d6eea"
public_host="${CODEX_STOREFRONT_HOST:?}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/var/backups/odoo-storefront/terms-${stamp}"
test_database="odoo_storefront_terms_test_${stamp//[^0-9]/}"
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

source_changed=0
module_existed=0
deployment_complete=0

cleanup() {
    sudo -u postgres dropdb --if-exists "${test_database}" >/dev/null 2>&1 || true
    if [[ "${source_changed}" = 1 && "${deployment_complete}" = 0 ]]; then
        rm -rf "${addons_root:?}/${module}"
        if [[ "${module_existed}" = 1 ]]; then
            tar -xzf "${backup_dir}/${module}.tar.gz" -C "${addons_root}"
            chown -R "${service_user}:${service_user}" "${addons_root}/${module}"
        fi
        systemctl start "${service}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

test -n "${service_user}"
test -n "${db_owner}"
test -s "${archive}"
test "$(sha256sum "${archive}" | awk '{print $1}')" = "${expected_hash}"
grep -q 'SUN Storefront Terms of Service' \
    < <(tar -xOzf "${archive}" custom_addons/${module}/__manifest__.py)
grep -q '服务条款' \
    < <(tar -xOzf "${archive}" custom_addons/${module}/views/terms_templates.xml)

install -d -m 0750 "${backup_dir}"
sudo -u postgres pg_dump -Fc "${database}" > "${backup_dir}/${database}.dump"
test -s "${backup_dir}/${database}.dump"
if [[ -d "${addons_root}/${module}" ]]; then
    module_existed=1
    tar -czf "${backup_dir}/${module}.tar.gz" -C "${addons_root}" "${module}"
fi

sudo -u postgres createdb -T template0 -O "${db_owner}" "${test_database}"
sudo -u "${service_user}" pg_restore --no-owner --no-privileges \
    -d "${test_database}" < "${backup_dir}/${database}.dump"

# These objects only contain cache-invalidation counters. Odoo creates fresh
# copies at registry startup, so an isolated clone must not retain the dump's
# copies.
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

rm -rf "${addons_root:?}/${module}"
tar -xzf "${archive}" --strip-components=1 -C "${runtime}" \
    "custom_addons/${module}"

# The two storefront layouts place custom_addons at different roots. Move the
# extracted module to the active path when necessary.
extracted_module="${runtime}/${module}"
if [[ "${extracted_module}" != "${addons_root}/${module}" ]]; then
    mv "${extracted_module}" "${addons_root}/${module}"
fi
source_changed=1
chown -R "${service_user}:${service_user}" "${addons_root}/${module}"

sudo -u "${service_user}" -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${test_database}" -i "${module}" \
    --test-enable --test-tags "/${module}" --stop-after-init \
    --http-port=18070 --gevent-port=18073 --log-level=test

sudo -u postgres dropdb "${test_database}"

systemctl stop "${service}"
sudo -u "${service_user}" -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${database}" -i "${module}" \
    --stop-after-init --no-http
systemctl start "${service}"

for attempt in $(seq 1 45); do
    if systemctl is-active --quiet "${service}" \
        && curl --fail --silent --max-time 5 \
            -H "Host: ${public_host}" http://127.0.0.1:8070/terms \
            | grep -q 'sun_terms_page'; then
        break
    fi
    sleep 2
done

systemctl is-active --quiet "${service}"
page="$(curl --fail --silent --show-error --max-time 10 \
    -H "Host: ${public_host}" http://127.0.0.1:8070/terms)"
grep -q 'sun_terms_page' <<<"${page}"
grep -q '服务条款' <<<"${page}"
if grep -q 'You should update this document' <<<"${page}"; then
    printf 'The Odoo placeholder terms are still visible.\n' >&2
    exit 1
fi

deployment_complete=1
printf 'STOREFRONT_TERMS_VERIFIED host=%s backup=%s\n' \
    "${public_host}" "${backup_dir}"
