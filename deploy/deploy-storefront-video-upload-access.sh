#!/usr/bin/env bash
set -Eeuo pipefail

runtime="/opt/odoo-storefront"
config="/etc/odoo-storefront.conf"
service="odoo-storefront"
database="odoo_storefront"
module="storefront_video_upload_access"
archive="${CODEX_STOREFRONT_RELEASE:?}/storefront-video-upload-access.tar.gz"
expected_hash="${CODEX_STOREFRONT_ARCHIVE_SHA256:?}"
public_host="${CODEX_STOREFRONT_HOST:?}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/var/backups/odoo-storefront/video-upload-access-${stamp}"
test_database="odoo_storefront_video_access_test_${stamp//[^0-9]/}"
database_dump_tmp="/tmp/${database}-video-upload-access-${stamp}.dump"

if [[ -d "${runtime}/project/custom_addons" ]]; then
    addons_root="${runtime}/project/custom_addons"
elif [[ -d "${runtime}/custom_addons" ]]; then
    addons_root="${runtime}/custom_addons"
else
    echo "Storefront custom addons directory was not found." >&2
    exit 1
fi

service_user="$(systemctl show -p User --value "${service}")"
database_owner="$(sudo -u postgres psql -Atqc \
    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='${database}'")"
module_existed=0
production_started=0
deployment_complete=0

rollback() {
    sudo -u postgres dropdb --if-exists --force "${test_database}" >/dev/null 2>&1 || true
    if [[ "${production_started}" = 1 && "${deployment_complete}" = 0 ]]; then
        echo "Video-upload access deployment failed; restoring storefront backup." >&2
        systemctl stop "${service}" >/dev/null 2>&1 || true
        sudo -u postgres dropdb --if-exists --force "${database}" >/dev/null 2>&1 || true
        sudo -u postgres createdb --owner="${database_owner}" "${database}"
        sudo -u postgres pg_restore --exit-on-error --no-owner \
            --role="${database_owner}" --dbname="${database}" \
            < "${backup_dir}/${database}.dump"
        rm -rf "${addons_root:?}/${module}"
        if [[ "${module_existed}" = 1 ]]; then
            tar -xzf "${backup_dir}/${module}.tar.gz" -C "${addons_root}"
            chown -R "${service_user}:${service_user}" "${addons_root}/${module}"
        fi
        systemctl start "${service}" >/dev/null 2>&1 || true
    fi
}
trap rollback EXIT

test -n "${service_user}"
test -n "${database_owner}"
test -s "${archive}"
test "$(sha256sum "${archive}" | awk '{print $1}')" = "${expected_hash}"
tar -tzf "${archive}" | grep -qx "custom_addons/${module}/__manifest__.py"

install -d -m 0750 "${backup_dir}"
sudo -u postgres pg_dump --format=custom --compress=6 \
    --file="${database_dump_tmp}" "${database}"
install -o root -g root -m 0600 "${database_dump_tmp}" \
    "${backup_dir}/${database}.dump"
rm -f -- "${database_dump_tmp}"
test -s "${backup_dir}/${database}.dump"
if [[ -d "${addons_root}/${module}" ]]; then
    module_existed=1
    tar -czf "${backup_dir}/${module}.tar.gz" -C "${addons_root}" "${module}"
fi

sudo -u postgres createdb -T template0 -O "${database_owner}" "${test_database}"
sudo -u "${service_user}" pg_restore --no-owner --no-privileges \
    -d "${test_database}" < "${backup_dir}/${database}.dump"

sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${test_database}" <<'SQL'
DO $do$
DECLARE signaling_object record;
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

rm -rf "${addons_root:?}/${module}"
tar -xzf "${archive}" --strip-components=1 -C "${runtime}" \
    "custom_addons/${module}"
if [[ "${runtime}/${module}" != "${addons_root}/${module}" ]]; then
    mv "${runtime}/${module}" "${addons_root}/${module}"
fi
chown -R "${service_user}:${service_user}" "${addons_root}/${module}"

sudo -u "${service_user}" -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${test_database}" -i "${module}" \
    --test-enable --test-tags "/${module}" --stop-after-init \
    --http-port=18070 --gevent-port=18073 --log-level=test
sudo -u postgres dropdb "${test_database}"

systemctl stop "${service}"
production_started=1
sudo -u "${service_user}" -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${database}" -i "${module}" \
    --stop-after-init --no-http
systemctl start "${service}"

for attempt in $(seq 1 45); do
    if systemctl is-active --quiet "${service}" \
       && curl --fail --silent --max-time 5 \
            -H "Host: ${public_host}" http://127.0.0.1:8070/ >/dev/null; then
        break
    fi
    sleep 2
done

systemctl is-active --quiet "${service}"
sudo -u postgres psql -d "${database}" -X -v ON_ERROR_STOP=1 -At <<'SQL' \
    | grep -qx 'video-upload-access-ok'
SELECT 'video-upload-access-ok'
  FROM ir_model_data AS upload_data
  JOIN ir_ui_menu AS upload_menu ON upload_menu.id = upload_data.res_id
  JOIN ir_model_data AS root_data
    ON root_data.module = 'website'
   AND root_data.name = 'menu_website_configuration'
   AND root_data.model = 'ir.ui.menu'
  JOIN ir_model_data AS designer_data
    ON designer_data.module = 'website'
   AND designer_data.name = 'group_website_designer'
   AND designer_data.model = 'res.groups'
  JOIN ir_ui_menu_group_rel AS menu_group
    ON menu_group.menu_id = upload_menu.id
   AND menu_group.gid = designer_data.res_id
 WHERE upload_data.module = 'stock_subwarehouse_hierarchy'
   AND upload_data.name = 'menu_website_video_upload'
   AND upload_data.model = 'ir.ui.menu'
   AND upload_menu.parent_id = root_data.res_id
   AND upload_menu.active;
SQL

deployment_complete=1
echo "STOREFRONT_VIDEO_UPLOAD_ACCESS_VERIFIED host=${public_host} backup=${backup_dir}"
