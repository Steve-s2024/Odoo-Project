#!/usr/bin/env bash
set -Eeuo pipefail

module="stock_subwarehouse_hierarchy"
module_root="/opt/odoo/project/custom_addons/${module}"
release_dir="${CODEX_ERP_RELEASE_DIR:?CODEX_ERP_RELEASE_DIR is required}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_root="/var/backups/odoo/erp-login-chinese-${stamp}"
test_root="/tmp/codex-erp-login-chinese-${stamp}"
test_db="codex_erp_login_${stamp//[^0-9]/}"
test_log="${test_root}/odoo-test.log"
update_log="/var/log/odoo/erp-login-chinese-update-${stamp}.log"

manifest_source="${release_dir}/__manifest__.py"
view_source="${release_dir}/erp_login_views.xml"
test_init_source="${release_dir}/erp-login-test-init.py"
test_source="${release_dir}/test_erp_login_chinese.py"

manifest_target="${module_root}/__manifest__.py"
view_target="${module_root}/views/erp_login_views.xml"

expected_manifest_before="9067197cf83027da45d9131b2f1e841e3cc129c3e428377a8b7342d96a565519"
expected_view_before="830654ad8a05f17ebff83ad715d05cf54fa43aaf6a3dfd08892f054b53d6a5c5"

deployed_started=0
deployed_complete=0
test_db_created=0

assert_sha256() {
    local expected="$1"
    local path="$2"
    local actual
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        printf 'Source drift detected for %s\nExpected: %s\nActual:   %s\n' \
            "$path" "$expected" "$actual" >&2
        exit 1
    fi
}

update_module() {
    runuser -u odoo -- \
        /opt/odoo/venv/bin/python \
        /opt/odoo/odoo-src/odoo-bin \
        -c /etc/odoo.conf \
        -d odoo_prod \
        -u "$module" \
        --stop-after-init \
        --no-http \
        --max-cron-threads=0 \
        --logfile="$update_log"
}

remove_new_views() {
    local cleanup_script="${test_root}/remove_new_views.py"
    cat >"$cleanup_script" <<'PY'
for xmlid in (
    "stock_subwarehouse_hierarchy.sun_erp_login_form_chinese",
    "stock_subwarehouse_hierarchy.sun_erp_login_oauth_chinese",
):
    view = env.ref(xmlid, raise_if_not_found=False)
    if view:
        view.unlink()
env.cr.commit()
PY
    runuser -u odoo -- \
        /opt/odoo/venv/bin/python \
        /opt/odoo/odoo-src/odoo-bin shell \
        -c /etc/odoo.conf -d odoo_prod --no-http \
        <"$cleanup_script"
}

rollback_release() {
    set +e
    echo "ERP login deployment failed; restoring the exact previous source." >&2
    systemctl stop odoo
    install -o odoo -g odoo -m 0644 \
        "${backup_root}/__manifest__.py" "$manifest_target"
    install -o odoo -g odoo -m 0644 \
        "${backup_root}/erp_login_views.xml" "$view_target"
    remove_new_views
    update_module
    systemctl start odoo
    systemctl is-active --quiet odoo
    set -e
}

cleanup() {
    local status=$?
    if [[ "$deployed_started" -eq 1 && "$deployed_complete" -ne 1 ]]; then
        rollback_release || true
    fi
    if [[ "$test_db_created" -eq 1 && "$test_db" =~ ^codex_erp_login_[0-9]+$ ]]; then
        runuser -u postgres -- dropdb --if-exists "$test_db" >/dev/null 2>&1 || true
    fi
    if [[ -d "$test_root" && "$test_root" == /tmp/codex-erp-login-chinese-* ]]; then
        rm -rf -- "$test_root"
    fi
    exit "$status"
}
trap cleanup EXIT

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
for source_file in \
    "$manifest_source" "$view_source" "$test_init_source" "$test_source"; do
    test -s "$source_file"
done
test "$(realpath /opt/odoo/project/custom_addons)" = "/opt/odoo/project/custom_addons"
test "$(realpath "$module_root")" = "$module_root"

# Refuse to overwrite a deployment that changed since the read-only audit.
assert_sha256 "$expected_manifest_before" "$manifest_target"
assert_sha256 "$expected_view_before" "$view_target"

/opt/odoo/venv/bin/python -m py_compile "$test_init_source" "$test_source"
/opt/odoo/venv/bin/python - <<PY
from lxml import etree
etree.parse(${view_source@Q})
print("XML_PARSE_OK")
PY

install -d -m 0750 "$backup_root"
cp -a "$manifest_target" "${backup_root}/__manifest__.py"
cp -a "$view_target" "${backup_root}/erp_login_views.xml"

# First validate the new QWeb inheritance and both login URLs on a disposable
# copy of production.  The production service and source remain untouched.
install -d -o odoo -g odoo -m 0700 "$test_root"
install -d -o odoo -g odoo -m 0700 "${test_root}/custom_addons"
cp -a "$module_root" "${test_root}/custom_addons/${module}"
install -m 0644 "$manifest_source" \
    "${test_root}/custom_addons/${module}/__manifest__.py"
install -m 0644 "$view_source" \
    "${test_root}/custom_addons/${module}/views/erp_login_views.xml"
install -m 0644 "$test_init_source" \
    "${test_root}/custom_addons/${module}/tests/__init__.py"
install -m 0644 "$test_source" \
    "${test_root}/custom_addons/${module}/tests/test_erp_login_chinese.py"
chown -R odoo:odoo "${test_root}/custom_addons"

runuser -u postgres -- createdb -O odoo "$test_db"
test_db_created=1
runuser -u postgres -- pg_dump -Fc odoo_prod | \
    runuser -u postgres -- pg_restore --no-owner --role=odoo -d "$test_db"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$test_db" \
    -c "UPDATE ir_cron SET active = FALSE WHERE active" >/dev/null

if ! runuser -u odoo -- \
    /opt/odoo/venv/bin/python \
    /opt/odoo/odoo-src/odoo-bin \
    -c /etc/odoo.conf \
    -d "$test_db" \
    --db-filter="^${test_db}$" \
    --addons-path="${test_root}/custom_addons,/opt/odoo/odoo-src/odoo/addons,/opt/odoo/odoo-src/addons,/opt/odoo/project/custom_addons" \
    -u "$module" \
    --test-enable \
    --test-tags="/${module}:TestErpLoginChinesePresentation" \
    --stop-after-init \
    --http-interface=127.0.0.1 \
    --http-port=18069 \
    --gevent-port=18072 \
    --max-cron-threads=0 \
    --logfile="$test_log"; then
    tail -n 160 "$test_log" >&2 || true
    exit 1
fi
if grep -Eq '(^|[[:space:]])(ERROR|CRITICAL)([[:space:]]|$)|[1-9][0-9]* failed' "$test_log"; then
    tail -n 160 "$test_log" >&2
    exit 1
fi
cp -a "$test_log" "${backup_root}/isolated-test.log"

# Install only the ERP presentation files. Authentication controllers, Shops,
# Nginx, credentials and payment services are deliberately outside this scope.
deployed_started=1
install -o odoo -g odoo -m 0644 "$manifest_source" "$manifest_target"
install -o odoo -g odoo -m 0644 "$view_source" "$view_target"

systemctl stop odoo
update_module
systemctl start odoo
for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error --max-time 5 \
        http://127.0.0.1:8069/api/v1/health >/dev/null; then
        break
    fi
    if [[ "$attempt" -eq 30 ]]; then
        echo "ERP did not become healthy after the login-page update." >&2
        exit 1
    fi
    sleep 2
done

curl --fail --silent --show-error --max-time 15 \
    http://127.0.0.1:8069/web/login \
    -o "${test_root}/login-default.html"
curl --fail --silent --show-error --max-time 15 \
    -H 'Accept-Language: en-US,en;q=0.9' \
    -H 'Cookie: frontend_lang=en_US' \
    http://127.0.0.1:8069/en/web/login \
    -o "${test_root}/login-english-prefix.html"
curl --fail --silent --show-error --max-time 15 \
    -H 'Accept-Language: en-US,en;q=0.9' \
    -H 'Cookie: frontend_lang=en_US' \
    'http://127.0.0.1:8069/en/web/login?error=access' \
    -o "${test_root}/login-access-error.html"

/opt/odoo/venv/bin/python - \
    "${test_root}/login-default.html" \
    "${test_root}/login-english-prefix.html" \
    "${test_root}/login-access-error.html" <<'PY'
import sys
from lxml import html

for filename in sys.argv[1:]:
    raw = open(filename, "rb").read()
    document = html.fromstring(raw)
    title = document.xpath("string(//title)").strip()
    assert "思安奇ERP系统登录" in title, (filename, title)
    assert "Login" not in title, (filename, title)
    assert document.xpath("string(//label[@for='login'])").strip() == "账号或邮箱"
    assert document.xpath("//input[@id='login']/@placeholder") == ["请输入账号或邮箱"]
    assert document.xpath("string(//label[@for='password'])").strip() == "密码"
    assert document.xpath("//input[@id='password']/@placeholder") == ["请输入密码"]
    login_text = document.xpath(
        "string(//div[contains(@class, 'oe_login_buttons')]/button[@type='submit'][1])"
    ).strip()
    assert login_text == "登录", (filename, login_text)
    assert "重置密码" in document.xpath(
        "string(//a[contains(@href, '/web/reset_password')])"
    )
    assert document.xpath("//form[@method='post' and contains(@action, '/web/login')]")
    assert document.xpath("//input[@name='csrf_token' and string-length(@value) > 10]")
    assert not document.xpath("//header[@id='top']")
    assert not document.xpath("//footer[@id='bottom']")
    if document.xpath("//a[contains(@class, 'passkey_login_link')]"):
        assert "使用通行密钥" in document.xpath(
            "string(//a[contains(@class, 'passkey_login_link')])"
        )
    if filename.endswith("login-access-error.html"):
        error = document.xpath("string(//p[contains(@class, 'alert-danger')])").strip()
        assert error == "只有员工可以访问此数据库，请与管理员联系。", error
print("ERP_LOGIN_CHINESE_SMOKE_OK")
PY

systemctl is-active --quiet odoo
systemctl is-enabled --quiet odoo
deployed_complete=1
echo "ERP_LOGIN_CHINESE_DEPLOYED=1"
echo "FILE_BACKUP=${backup_root}"
echo "TEST_LOG=${backup_root}/isolated-test.log"
