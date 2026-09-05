#!/usr/bin/env bash
set -Eeuo pipefail

source_stamp="${1:?usage: apply-hk-to-cn-storefront-sync.sh SOURCE_TIMESTAMP [RUN_TIMESTAMP]}"
run_stamp="${2:-${source_stamp}}"
database="odoo_storefront"
runtime="/opt/odoo-storefront"
config="/etc/odoo-storefront.conf"
service="odoo-storefront"
addons_root="${runtime}/custom_addons"
preserved_addons="${runtime}/custom_addons.pre-hk-sync-${run_stamp}"
filestore="${runtime}/data/filestore/${database}"
transfer_dir="/root/hk-to-cn-sync-${source_stamp}"
bundle="${transfer_dir}/hk-to-cn-storefront-sync.tar.gz"
bundle_checksum="${transfer_dir}/hk-to-cn-storefront-sync.tar.gz.sha256"
work_dir="/var/tmp/hk-to-cn-sync-work-${run_stamp}"
payload="${work_dir}/payload"
backup_dir="/var/backups/odoo-storefront/pre-hk-to-cn-sync-${run_stamp}"
failed_addons="${runtime}/custom_addons.failed-hk-sync-${run_stamp}"
modules="payment_alipay,payment_wechatpay,shop_api,stock_subwarehouse_hierarchy,storefront_api_bridge,storefront_terms_template,storefront_video_upload_access"
database_dump_tmp="/tmp/${database}-pre-hk-to-cn-sync-${run_stamp}.dump"

test "$(readlink -f "${runtime}")" = "/opt/odoo-storefront"
test "$(readlink -f "${addons_root}")" = "/opt/odoo-storefront/custom_addons"
test "$(readlink -f "${filestore}")" = "/opt/odoo-storefront/data/filestore/${database}"
test -d "${addons_root}"
test -d "${filestore}"
test -f "${config}"
test -f "${bundle}"
test -f "${bundle_checksum}"
test ! -e "${preserved_addons}"
test ! -e "${failed_addons}"
test ! -e "${backup_dir}"
test ! -e "${work_dir}"

expected_bundle_hash="$(awk 'NR == 1 {print $1}' "${bundle_checksum}")"
actual_bundle_hash="$(sha256sum "${bundle}" | awk '{print $1}')"
test -n "${expected_bundle_hash}"
test "${actual_bundle_hash}" = "${expected_bundle_hash}"

# PostgreSQL's client-side \copy runs as the postgres OS user. Keep the
# extracted payload outside /root and allow that user to traverse only this
# staging directory; the payload itself is locked down again before import.
install -d -o root -g postgres -m 0710 "${work_dir}"
tar -xzf "${bundle}" -C "${work_dir}"
test -d "${payload}"
(
    cd "${payload}"
    sha256sum --check SHA256SUMS
)

for required in ir_ui_view.csv website_page.csv website_menu.csv website.csv \
    ir_attachment.csv website-filestore.tar.gz custom_addons.tar.gz filestore.list; do
    test -f "${payload}/${required}"
done

while IFS= read -r archive_path; do
    case "${archive_path}" in
        custom_addons|custom_addons/) ;;
        custom_addons/*) ;;
        *) echo "Unsafe custom-addons archive path: ${archive_path}" >&2; exit 1 ;;
    esac
done < <(tar -tzf "${payload}/custom_addons.tar.gz")

while IFS= read -r stored_file; do
    [[ "${stored_file}" =~ ^[0-9a-f]{2}/[0-9a-f]{40}$ ]]
done < "${payload}/filestore.list"

service_user="$(systemctl show -p User --value "${service}")"
database_owner="$(sudo -u postgres psql -Atqc \
    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='${database}'")"
test -n "${service_user}"
test -n "${database_owner}"

orders_before="$(sudo -u postgres psql -d "${database}" -Atqc 'SELECT count(*) FROM sale_order')"
payments_before="$(sudo -u postgres psql -d "${database}" -Atqc 'SELECT count(*) FROM payment_transaction')"
database_uuid_before="$(sudo -u postgres psql -d "${database}" -Atqc \
    "SELECT value FROM ir_config_parameter WHERE key='database.uuid'")"
providers_before="$(sudo -u postgres psql -d "${database}" -Atqc \
    "SELECT md5(coalesce(string_agg((to_jsonb(p) - 'write_date')::text, E'\\n' ORDER BY p.id), '')) FROM payment_provider p")"
environment_hash_before="missing"
if test -f /etc/odoo-storefront.env; then
    environment_hash_before="$(sha256sum /etc/odoo-storefront.env | awk '{print $1}')"
fi
video_access_state="$(sudo -u postgres psql -d "${database}" -Atqc \
    "SELECT state FROM ir_module_module WHERE name='storefront_video_upload_access'")"

install -d -o root -g root -m 0700 "${backup_dir}"
sudo -u postgres pg_dump --format=custom --compress=6 \
    --file="${database_dump_tmp}" "${database}"
install -o root -g root -m 0600 "${database_dump_tmp}" \
    "${backup_dir}/${database}.dump"
rm -f -- "${database_dump_tmp}"
test -s "${backup_dir}/${database}.dump"
tar -C "${runtime}" -czf "${backup_dir}/custom_addons.tar.gz" custom_addons
cp -a "${config}" "${backup_dir}/odoo-storefront.conf"
if test -f /etc/odoo-storefront.env; then
    cp -a /etc/odoo-storefront.env "${backup_dir}/odoo-storefront.env"
fi
if test -e /etc/nginx/sites-enabled/odoo-storefront; then
    cp -aL /etc/nginx/sites-enabled/odoo-storefront \
        "${backup_dir}/nginx-odoo-storefront.conf"
fi
sha256sum "${backup_dir}/${database}.dump" \
    "${backup_dir}/custom_addons.tar.gz" > "${backup_dir}/SHA256SUMS"

deployment_started=0
deployment_complete=0
code_changed=0

rollback() {
    if [[ "${deployment_started}" = 1 && "${deployment_complete}" = 0 ]]; then
        echo "Synchronization failed; restoring the China Shop backup." >&2
        systemctl stop "${service}" >/dev/null 2>&1 || true
        sudo -u postgres dropdb --if-exists --force "${database}" >/dev/null 2>&1 || true
        sudo -u postgres createdb --owner="${database_owner}" "${database}"
        sudo -u postgres pg_restore --exit-on-error --no-owner \
            --role="${database_owner}" --dbname="${database}" \
            < "${backup_dir}/${database}.dump"
        if [[ "${code_changed}" = 1 && -d "${preserved_addons}" ]]; then
            if [[ -d "${addons_root}" ]]; then
                mv "${addons_root}" "${failed_addons}"
            fi
            mv "${preserved_addons}" "${addons_root}"
        fi
        systemctl start "${service}" >/dev/null 2>&1 || true
    fi
}
trap rollback EXIT

systemctl stop "${service}"
deployment_started=1

mv "${addons_root}" "${preserved_addons}"
tar --no-same-owner -xzf "${payload}/custom_addons.tar.gz" -C "${runtime}"
test -d "${addons_root}/storefront_api_bridge"
test -d "${addons_root}/stock_subwarehouse_hierarchy"
code_changed=1
chown -R "${service_user}:${service_user}" "${addons_root}"

# Upgrade the synchronized code first. Website-builder records exported from
# Hong Kong are applied afterwards so module data cannot overwrite them.
sudo -u "${service_user}" -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${database}" -u "${modules}" \
    --stop-after-init --no-http
if [[ "${video_access_state}" != "installed" ]]; then
    sudo -u "${service_user}" -H \
        "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
        -c "${config}" -d "${database}" \
        -i storefront_video_upload_access --stop-after-init --no-http
fi

tar --no-same-owner -xzf "${payload}/website-filestore.tar.gz" -C "${filestore}"
while IFS= read -r stored_file; do
    test -f "${filestore}/${stored_file}"
    chown "${service_user}:${service_user}" "${filestore}/${stored_file}"
done < "${payload}/filestore.list"

# The PostgreSQL client reads the CSV files as the postgres OS user.
chown -R postgres:postgres "${payload}"
chmod -R go-rwx "${payload}"

sudo -u postgres psql -d "${database}" -X -v ON_ERROR_STOP=1 <<SQL
\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE sync_ir_ui_view (LIKE ir_ui_view INCLUDING DEFAULTS);
CREATE TEMP TABLE sync_website_page (LIKE website_page INCLUDING DEFAULTS);
CREATE TEMP TABLE sync_website_menu (LIKE website_menu INCLUDING DEFAULTS);
CREATE TEMP TABLE sync_website (LIKE website INCLUDING DEFAULTS);
CREATE TEMP TABLE sync_ir_attachment (LIKE ir_attachment INCLUDING DEFAULTS);

\copy sync_ir_ui_view FROM '${payload}/ir_ui_view.csv' WITH (FORMAT csv, HEADER true)
\copy sync_website_page FROM '${payload}/website_page.csv' WITH (FORMAT csv, HEADER true)
\copy sync_website_menu FROM '${payload}/website_menu.csv' WITH (FORMAT csv, HEADER true)
\copy sync_website FROM '${payload}/website.csv' WITH (FORMAT csv, HEADER true)
\copy sync_ir_attachment FROM '${payload}/ir_attachment.csv' WITH (FORMAT csv, HEADER true)

-- Module upgrades can legitimately allocate an ID that the independently
-- edited HK website later used for a custom view. Never overwrite that CN
-- non-website record. Allocate a fresh view ID above both snapshots and
-- rewrite every exported relational reference before applying the snapshot.
CREATE TEMP TABLE sync_view_id_map (
    old_id bigint PRIMARY KEY,
    new_id bigint NOT NULL UNIQUE
);

SELECT setval(
    'ir_ui_view_id_seq',
    greatest(
        coalesce((SELECT max(id) FROM ir_ui_view), 1),
        coalesce((SELECT max(id) FROM sync_ir_ui_view), 1)
    ),
    true
);

INSERT INTO sync_view_id_map (old_id, new_id)
SELECT source.id, nextval('ir_ui_view_id_seq')
FROM sync_ir_ui_view source
JOIN ir_ui_view target ON target.id = source.id
WHERE target.website_id IS DISTINCT FROM 1
ORDER BY source.id;

UPDATE sync_ir_ui_view source
SET inherit_id = mapping.new_id
FROM sync_view_id_map mapping
WHERE source.inherit_id = mapping.old_id;

UPDATE sync_ir_ui_view source
SET theme_template_id = mapping.new_id
FROM sync_view_id_map mapping
WHERE source.theme_template_id = mapping.old_id;

UPDATE sync_website_page source
SET view_id = mapping.new_id
FROM sync_view_id_map mapping
WHERE source.view_id = mapping.old_id;

UPDATE sync_website_page source
SET theme_template_id = mapping.new_id
FROM sync_view_id_map mapping
WHERE source.theme_template_id = mapping.old_id;

UPDATE sync_website_menu source
SET theme_template_id = mapping.new_id
FROM sync_view_id_map mapping
WHERE source.theme_template_id = mapping.old_id;

UPDATE sync_ir_attachment source
SET res_id = mapping.new_id
FROM sync_view_id_map mapping
WHERE source.res_model = 'ir.ui.view'
  AND source.res_id = mapping.old_id;

UPDATE sync_ir_attachment source
SET theme_template_id = mapping.new_id
FROM sync_view_id_map mapping
WHERE source.theme_template_id = mapping.old_id;

UPDATE sync_ir_ui_view source
SET id = mapping.new_id
FROM sync_view_id_map mapping
WHERE source.id = mapping.old_id;

DO \$\$
BEGIN
    IF EXISTS (
        SELECT 1 FROM sync_ir_ui_view s
        JOIN ir_ui_view t ON t.id = s.id
        WHERE t.website_id IS DISTINCT FROM 1
    ) THEN
        RAISE EXCEPTION 'A source website view ID collides with a non-website target view';
    END IF;
    IF EXISTS (
        SELECT 1 FROM sync_website_page s
        JOIN website_page t ON t.id = s.id
        WHERE t.website_id IS DISTINCT FROM 1
    ) THEN
        RAISE EXCEPTION 'A source website page ID collides with a different target website';
    END IF;
    IF EXISTS (
        SELECT 1 FROM sync_website_menu s
        JOIN website_menu t ON t.id = s.id
        WHERE t.website_id IS DISTINCT FROM 1
    ) THEN
        RAISE EXCEPTION 'A source website menu ID collides with a different target website';
    END IF;
    IF EXISTS (
        SELECT 1 FROM sync_ir_attachment s
        JOIN ir_attachment t ON t.id = s.id
        WHERE t.website_id IS DISTINCT FROM 1
    ) THEN
        RAISE EXCEPTION 'A source website attachment ID collides with non-website target data';
    END IF;
END
\$\$;

UPDATE ir_ui_view t SET
    priority = s.priority,
    inherit_id = s.inherit_id,
    create_uid = s.create_uid,
    write_uid = s.write_uid,
    name = s.name,
    model = s.model,
    key = s.key,
    type = s.type,
    arch_fs = s.arch_fs,
    mode = s.mode,
    arch_db = s.arch_db,
    arch_prev = s.arch_prev,
    arch_updated = s.arch_updated,
    active = s.active,
    create_date = s.create_date,
    write_date = s.write_date,
    customize_show = s.customize_show,
    website_id = s.website_id,
    theme_template_id = s.theme_template_id,
    website_meta_og_img = s.website_meta_og_img,
    visibility = s.visibility,
    visibility_password = s.visibility_password,
    website_meta_title = s.website_meta_title,
    website_meta_description = s.website_meta_description,
    website_meta_keywords = s.website_meta_keywords,
    seo_name = s.seo_name,
    is_seo_optimized = s.is_seo_optimized,
    track = s.track
FROM sync_ir_ui_view s
WHERE t.id = s.id;

INSERT INTO ir_ui_view
SELECT s.* FROM sync_ir_ui_view s
WHERE NOT EXISTS (SELECT 1 FROM ir_ui_view t WHERE t.id = s.id);

UPDATE website_page t SET
    website_id = s.website_id,
    view_id = s.view_id,
    create_uid = s.create_uid,
    write_uid = s.write_uid,
    theme_template_id = s.theme_template_id,
    header_color = s.header_color,
    header_text_color = s.header_text_color,
    url = s.url,
    header_visible = s.header_visible,
    footer_visible = s.footer_visible,
    header_overlay = s.header_overlay,
    is_published = s.is_published,
    website_indexed = s.website_indexed,
    is_new_page_template = s.is_new_page_template,
    date_publish = s.date_publish,
    create_date = s.create_date,
    write_date = s.write_date
FROM sync_website_page s
WHERE t.id = s.id;

INSERT INTO website_page
SELECT s.* FROM sync_website_page s
WHERE NOT EXISTS (SELECT 1 FROM website_page t WHERE t.id = s.id);

UPDATE website_menu t SET
    page_id = s.page_id,
    controller_page_id = s.controller_page_id,
    sequence = s.sequence,
    website_id = s.website_id,
    parent_id = s.parent_id,
    create_uid = s.create_uid,
    write_uid = s.write_uid,
    theme_template_id = s.theme_template_id,
    url = s.url,
    parent_path = s.parent_path,
    mega_menu_classes = s.mega_menu_classes,
    name = s.name,
    mega_menu_content = s.mega_menu_content,
    new_window = s.new_window,
    create_date = s.create_date,
    write_date = s.write_date
FROM sync_website_menu s
WHERE t.id = s.id;

INSERT INTO website_menu
SELECT s.* FROM sync_website_menu s
WHERE NOT EXISTS (SELECT 1 FROM website_menu t WHERE t.id = s.id);

DELETE FROM website_menu t
WHERE t.website_id = 1
  AND NOT EXISTS (SELECT 1 FROM sync_website_menu s WHERE s.id = t.id);
DELETE FROM website_page t
WHERE t.website_id = 1
  AND NOT EXISTS (SELECT 1 FROM sync_website_page s WHERE s.id = t.id);
DELETE FROM ir_ui_view t
WHERE t.website_id = 1
  AND NOT EXISTS (SELECT 1 FROM sync_ir_ui_view s WHERE s.id = t.id);

UPDATE website t SET
    sequence = s.sequence,
    company_id = s.company_id,
    default_lang_id = s.default_lang_id,
    user_id = s.user_id,
    theme_id = s.theme_id,
    name = s.name,
    social_twitter = s.social_twitter,
    social_facebook = s.social_facebook,
    social_github = s.social_github,
    social_linkedin = s.social_linkedin,
    social_youtube = s.social_youtube,
    social_instagram = s.social_instagram,
    social_tiktok = s.social_tiktok,
    social_discord = s.social_discord,
    google_analytics_key = s.google_analytics_key,
    google_search_console = s.google_search_console,
    google_maps_api_key = s.google_maps_api_key,
    plausible_shared_key = s.plausible_shared_key,
    plausible_site = s.plausible_site,
    cdn_url = s.cdn_url,
    homepage_url = s.homepage_url,
    auth_signup_uninvited = s.auth_signup_uninvited,
    custom_blocked_third_party_domains = s.custom_blocked_third_party_domains,
    cdn_filters = s.cdn_filters,
    custom_code_head = s.custom_code_head,
    custom_code_footer = s.custom_code_footer,
    robots_txt = s.robots_txt,
    auto_redirect_lang = s.auto_redirect_lang,
    cookies_bar = s.cookies_bar,
    configurator_done = s.configurator_done,
    block_third_party_domains = s.block_third_party_domains,
    has_social_default_image = s.has_social_default_image,
    cdn_activated = s.cdn_activated,
    specific_user_account = s.specific_user_account,
    salesperson_id = s.salesperson_id,
    salesteam_id = s.salesteam_id,
    cart_recovery_mail_template_id = s.cart_recovery_mail_template_id,
    shop_ppg = s.shop_ppg,
    shop_ppr = s.shop_ppr,
    product_page_grid_columns = s.product_page_grid_columns,
    confirmation_email_template_id = s.confirmation_email_template_id,
    show_line_subtotals_tax_selection = s.show_line_subtotals_tax_selection,
    add_to_cart_action = s.add_to_cart_action,
    account_on_checkout = s.account_on_checkout,
    shop_page_container = s.shop_page_container,
    shop_gap = s.shop_gap,
    shop_opt_products_design_classes = s.shop_opt_products_design_classes,
    shop_default_sort = s.shop_default_sort,
    product_page_container = s.product_page_container,
    product_page_cols_order = s.product_page_cols_order,
    product_page_image_layout = s.product_page_image_layout,
    product_page_image_width = s.product_page_image_width,
    product_page_image_spacing = s.product_page_image_spacing,
    product_page_image_roundness = s.product_page_image_roundness,
    product_page_image_ratio = s.product_page_image_ratio,
    product_page_image_ratio_mobile = s.product_page_image_ratio_mobile,
    ecommerce_access = s.ecommerce_access,
    contact_us_button_url = s.contact_us_button_url,
    send_abandoned_cart_email = s.send_abandoned_cart_email,
    prevent_zero_price_sale = s.prevent_zero_price_sale,
    enabled_gmc_src = s.enabled_gmc_src,
    send_abandoned_cart_email_activation_time = s.send_abandoned_cart_email_activation_time,
    cart_abandoned_delay = s.cart_abandoned_delay,
    warehouse_id = s.warehouse_id,
    wishlist_grid_columns = s.wishlist_grid_columns,
    wishlist_mobile_columns = s.wishlist_mobile_columns,
    wishlist_opt_products_design_classes = s.wishlist_opt_products_design_classes,
    wishlist_gap = s.wishlist_gap,
    newsletter_id = s.newsletter_id
FROM sync_website s
WHERE t.id = s.id
  AND t.id = 1;

UPDATE ir_attachment t SET
    res_id = s.res_id,
    company_id = s.company_id,
    file_size = s.file_size,
    create_uid = s.create_uid,
    write_uid = s.write_uid,
    name = s.name,
    res_model = s.res_model,
    res_field = s.res_field,
    type = s.type,
    url = s.url,
    access_token = s.access_token,
    store_fname = s.store_fname,
    checksum = s.checksum,
    mimetype = s.mimetype,
    description = s.description,
    index_content = s.index_content,
    public = s.public,
    create_date = s.create_date,
    write_date = s.write_date,
    db_datas = s.db_datas,
    original_id = s.original_id,
    website_id = s.website_id,
    theme_template_id = s.theme_template_id,
    key = s.key
FROM sync_ir_attachment s
WHERE t.id = s.id;

INSERT INTO ir_attachment
SELECT s.* FROM sync_ir_attachment s
WHERE NOT EXISTS (SELECT 1 FROM ir_attachment t WHERE t.id = s.id);

DO \$\$
BEGIN
    IF EXISTS (SELECT * FROM sync_ir_ui_view EXCEPT SELECT * FROM ir_ui_view WHERE website_id = 1) THEN
        RAISE EXCEPTION 'Target website views do not contain the complete source snapshot';
    END IF;
    IF EXISTS (SELECT * FROM ir_ui_view WHERE website_id = 1 EXCEPT SELECT * FROM sync_ir_ui_view) THEN
        RAISE EXCEPTION 'Target contains website views absent from the source snapshot';
    END IF;
    IF EXISTS (SELECT * FROM sync_website_page EXCEPT SELECT * FROM website_page WHERE website_id = 1) THEN
        RAISE EXCEPTION 'Target website pages do not contain the complete source snapshot';
    END IF;
    IF EXISTS (SELECT * FROM website_page WHERE website_id = 1 EXCEPT SELECT * FROM sync_website_page) THEN
        RAISE EXCEPTION 'Target contains website pages absent from the source snapshot';
    END IF;
    IF EXISTS (SELECT * FROM sync_website_menu EXCEPT SELECT * FROM website_menu WHERE website_id = 1) THEN
        RAISE EXCEPTION 'Target website menus do not contain the complete source snapshot';
    END IF;
    IF EXISTS (SELECT * FROM website_menu WHERE website_id = 1 EXCEPT SELECT * FROM sync_website_menu) THEN
        RAISE EXCEPTION 'Target contains website menus absent from the source snapshot';
    END IF;
END
\$\$;

-- Rewrite only the internal storefront hostname; Shop-specific credentials,
-- payment providers and ERP channel configuration are not imported.
UPDATE ir_ui_view
SET arch_db = replace(
        replace(arch_db::text, 'www.sunwintersports.com', 'www.sunwintersports.cn'),
        'sunwintersports.com', 'sunwintersports.cn'
    )::jsonb,
    website_meta_title = replace(
        replace(website_meta_title::text, 'www.sunwintersports.com', 'www.sunwintersports.cn'),
        'sunwintersports.com', 'sunwintersports.cn'
    )::jsonb,
    website_meta_description = replace(
        replace(website_meta_description::text, 'www.sunwintersports.com', 'www.sunwintersports.cn'),
        'sunwintersports.com', 'sunwintersports.cn'
    )::jsonb,
    website_meta_keywords = replace(
        replace(website_meta_keywords::text, 'www.sunwintersports.com', 'www.sunwintersports.cn'),
        'sunwintersports.com', 'sunwintersports.cn'
    )::jsonb
WHERE website_id = 1;

UPDATE website
SET domain = 'https://sunwintersports.cn',
    cdn_url = replace(cdn_url, 'sunwintersports.com', 'sunwintersports.cn'),
    custom_code_head = replace(custom_code_head, 'sunwintersports.com', 'sunwintersports.cn'),
    custom_code_footer = replace(custom_code_footer, 'sunwintersports.com', 'sunwintersports.cn'),
    robots_txt = replace(robots_txt, 'sunwintersports.com', 'sunwintersports.cn')
WHERE id = 1;

INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
VALUES ('web.base.url', 'https://sunwintersports.cn', 1, 1, NOW(), NOW())
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, write_date = NOW();

INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
VALUES ('web.base.url.freeze', 'True', 1, 1, NOW(), NOW())
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, write_date = NOW();

-- Compiled bundles and sitemaps are caches and must be rebuilt for the .cn host.
DELETE FROM ir_attachment
WHERE url LIKE '/web/assets/%'
   OR url LIKE '/sitemap-%';

SELECT setval('ir_ui_view_id_seq', greatest((SELECT max(id) FROM ir_ui_view), 1), true);
SELECT setval('website_page_id_seq', greatest((SELECT max(id) FROM website_page), 1), true);
SELECT setval('website_menu_id_seq', greatest((SELECT max(id) FROM website_menu), 1), true);
SELECT setval('ir_attachment_id_seq', greatest((SELECT max(id) FROM ir_attachment), 1), true);

COMMIT;
SQL

while IFS= read -r stored_file; do
    [[ "${stored_file}" =~ ^[0-9a-f]{2}/[0-9a-f]{40}$ ]]
    test -f "${filestore}/${stored_file}"
done < <(
    sudo -u postgres psql -d "${database}" -X -Atqc \
        "SELECT DISTINCT store_fname FROM ir_attachment WHERE website_id = 1 AND coalesce(url, '') NOT LIKE '/sitemap-%' AND store_fname IS NOT NULL AND store_fname <> '' ORDER BY store_fname"
)

environment_hash_after="missing"
if test -f /etc/odoo-storefront.env; then
    environment_hash_after="$(sha256sum /etc/odoo-storefront.env | awk '{print $1}')"
fi
test "${environment_hash_after}" = "${environment_hash_before}"

orders_after="$(sudo -u postgres psql -d "${database}" -Atqc 'SELECT count(*) FROM sale_order')"
payments_after="$(sudo -u postgres psql -d "${database}" -Atqc 'SELECT count(*) FROM payment_transaction')"
database_uuid_after="$(sudo -u postgres psql -d "${database}" -Atqc \
    "SELECT value FROM ir_config_parameter WHERE key='database.uuid'")"
providers_after="$(sudo -u postgres psql -d "${database}" -Atqc \
    "SELECT md5(coalesce(string_agg((to_jsonb(p) - 'write_date')::text, E'\\n' ORDER BY p.id), '')) FROM payment_provider p")"
test "${orders_after}" = "${orders_before}"
test "${payments_after}" = "${payments_before}"
test "${database_uuid_after}" = "${database_uuid_before}"
test "${providers_after}" = "${providers_before}"

systemctl start "${service}"
for attempt in $(seq 1 45); do
    if systemctl is-active --quiet "${service}" \
       && curl --fail --silent --max-time 5 \
            -H 'Host: sunwintersports.cn' http://127.0.0.1:8070/ >/dev/null; then
        break
    fi
    sleep 2
done

systemctl is-active --quiet "${service}"
systemctl is-active --quiet nginx
cn_home="$(curl --fail --silent --show-error --max-time 10 \
    -H 'Host: sunwintersports.cn' http://127.0.0.1:8070/)"
test -n "${cn_home}"
cn_terms="$(curl --fail --silent --show-error --max-time 10 \
    -H 'Host: sunwintersports.cn' http://127.0.0.1:8070/terms)"
grep -q 'sun_terms_page' <<<"${cn_terms}"

erp_base_url="$(awk -F= '/^STOREFRONT_ERP_BASE_URL=/{print substr($0, index($0, "=") + 1); exit}' \
    /etc/odoo-storefront.env)"
test -n "${erp_base_url}"
curl --fail --silent --show-error --max-time 10 --insecure \
    "${erp_base_url%/}/api/v1/health" >/dev/null

sudo -u postgres psql -d "${database}" -X -At -F '|' <<'SQL'
SELECT 'website', domain FROM website WHERE id = 1;
SELECT 'base_url', value FROM ir_config_parameter WHERE key = 'web.base.url';
SELECT 'views', count(*) FROM ir_ui_view WHERE website_id = 1;
SELECT 'pages', count(*) FROM website_page WHERE website_id = 1;
SELECT 'menus', count(*) FROM website_menu WHERE website_id = 1;
SELECT 'products', count(*) FROM product_product;
SELECT 'videos', count(*) FROM ir_attachment WHERE mimetype LIKE 'video/%';
SELECT 'orders', count(*) FROM sale_order;
SELECT 'payments', count(*) FROM payment_transaction;
SELECT 'modules', name, latest_version
FROM ir_module_module
WHERE name IN (
    'shop_api', 'storefront_api_bridge', 'storefront_terms_template',
    'storefront_video_upload_access'
)
ORDER BY name;
SQL

deployment_complete=1
echo "CN_STOREFRONT_SYNC_VERIFIED backup=${backup_dir} preserved_code=${preserved_addons}"
