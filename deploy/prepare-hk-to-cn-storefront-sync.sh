#!/usr/bin/env bash
set -Eeuo pipefail

stamp="${1:?usage: prepare-hk-to-cn-storefront-sync.sh TIMESTAMP}"
database="odoo_storefront"
runtime="/opt/odoo-storefront"
addons_root="${runtime}/project/custom_addons"
filestore="${runtime}/data/filestore/${database}"
backup_dir="/var/backups/odoo-storefront/hk-to-cn-sync-source-${stamp}"
stage="${backup_dir}/payload"
transfer_dir="/home/ubuntu/hk-to-cn-sync-${stamp}"
transfer_owner="${SUDO_USER:-ubuntu}"
database_dump_tmp="/tmp/${database}-hk-to-cn-source-${stamp}.dump"

test "$(readlink -f "${addons_root}")" = "/opt/odoo-storefront/project/custom_addons"
test "$(readlink -f "${filestore}")" = "/opt/odoo-storefront/data/filestore/${database}"
test -d "${addons_root}"
test -d "${filestore}"
test ! -e "${backup_dir}"
test ! -e "${transfer_dir}"

install -d -o root -g postgres -m 0710 "${backup_dir}"
install -d -o postgres -g postgres -m 0700 "${stage}"
install -d -o "${transfer_owner}" -g "${transfer_owner}" -m 0700 "${transfer_dir}"

# The source is not modified, but retain a full point-in-time database backup
# alongside the exported website subset for audit and recovery.
sudo -u postgres pg_dump --format=custom --compress=6 \
    --file="${database_dump_tmp}" "${database}"
install -o root -g root -m 0600 "${database_dump_tmp}" \
    "${backup_dir}/${database}.dump"
rm -f -- "${database_dump_tmp}"
test -s "${backup_dir}/${database}.dump"

# Export one repeatable-read snapshot of presentation records only. Orders,
# payments, users, API channels and security records are deliberately excluded.
sudo -u postgres psql -d "${database}" -X -v ON_ERROR_STOP=1 <<SQL
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
COPY (
    SELECT * FROM ir_ui_view WHERE website_id = 1 ORDER BY id
) TO '${stage}/ir_ui_view.csv' WITH (FORMAT csv, HEADER true);
COPY (
    SELECT * FROM website_page WHERE website_id = 1 ORDER BY id
) TO '${stage}/website_page.csv' WITH (FORMAT csv, HEADER true);
COPY (
    SELECT * FROM website_menu WHERE website_id = 1 ORDER BY id
) TO '${stage}/website_menu.csv' WITH (FORMAT csv, HEADER true);
COPY (
    SELECT * FROM website WHERE id = 1
) TO '${stage}/website.csv' WITH (FORMAT csv, HEADER true);
COPY (
    SELECT *
      FROM ir_attachment
     WHERE website_id = 1
       AND coalesce(url, '') NOT LIKE '/sitemap-%'
     ORDER BY id
) TO '${stage}/ir_attachment.csv' WITH (FORMAT csv, HEADER true);
COMMIT;
SQL

sudo -u postgres psql -d "${database}" -X -At \
    > "${stage}/filestore.list" <<'SQL'
SELECT DISTINCT store_fname
FROM ir_attachment
WHERE website_id = 1
  AND coalesce(url, '') NOT LIKE '/sitemap-%'
  AND store_fname IS NOT NULL
  AND store_fname <> ''
ORDER BY store_fname;
SQL

while IFS= read -r stored_file; do
    [[ "${stored_file}" =~ ^[0-9a-f]{2}/[0-9a-f]{40}$ ]]
    test -f "${filestore}/${stored_file}"
done < "${stage}/filestore.list"

tar --numeric-owner -C "${filestore}" -czf "${stage}/website-filestore.tar.gz" \
    -T "${stage}/filestore.list"
tar --exclude='*/__pycache__' --exclude='*.pyc' --numeric-owner \
    -C "${runtime}/project" -czf "${stage}/custom_addons.tar.gz" custom_addons

sudo -u postgres psql -d "${database}" -X -At -F '|' \
    > "${stage}/source-summary.txt" <<'SQL'
SELECT 'views', count(*) FROM ir_ui_view WHERE website_id = 1;
SELECT 'pages', count(*) FROM website_page WHERE website_id = 1;
SELECT 'menus', count(*) FROM website_menu WHERE website_id = 1;
SELECT 'website_attachments', count(*)
FROM ir_attachment
WHERE website_id = 1 AND coalesce(url, '') NOT LIKE '/sitemap-%';
SELECT 'products', count(*) FROM product_product;
SELECT 'videos', count(*) FROM ir_attachment WHERE mimetype LIKE 'video/%';
SELECT 'orders', count(*) FROM sale_order;
SELECT 'payments', count(*) FROM payment_transaction;
SQL

chown -R root:root "${stage}"
chmod -R go-rwx "${stage}"
(
    cd "${stage}"
    sha256sum ./*.csv ./*.tar.gz ./filestore.list ./source-summary.txt \
        > SHA256SUMS
)

tar -C "${backup_dir}" -czf "${backup_dir}/hk-to-cn-storefront-sync.tar.gz" payload
(
    cd "${backup_dir}"
    sha256sum hk-to-cn-storefront-sync.tar.gz \
        > hk-to-cn-storefront-sync.tar.gz.sha256
)

install -o "${transfer_owner}" -g "${transfer_owner}" -m 0600 \
    "${backup_dir}/hk-to-cn-storefront-sync.tar.gz" \
    "${transfer_dir}/hk-to-cn-storefront-sync.tar.gz"
install -o "${transfer_owner}" -g "${transfer_owner}" -m 0600 \
    "${backup_dir}/hk-to-cn-storefront-sync.tar.gz.sha256" \
    "${transfer_dir}/hk-to-cn-storefront-sync.tar.gz.sha256"

echo "SOURCE_BACKUP=${backup_dir}"
echo "TRANSFER_DIR=${transfer_dir}"
cat "${stage}/source-summary.txt"
du -sh "${backup_dir}/${database}.dump" \
    "${backup_dir}/hk-to-cn-storefront-sync.tar.gz"
