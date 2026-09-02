#!/usr/bin/env bash
set -Eeuo pipefail

release="${1:-/tmp/account-login-cooldown-erp.tar.gz}"
expected_hash="${2:?expected release SHA-256 is required}"
root="/opt/odoo/project"
runtime="/opt/odoo"
database="odoo_prod"
service="odoo"
config="/etc/odoo.conf"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/var/backups/odoo/login-cooldown-${stamp}"
test_database="odoo_login_cooldown_test_${stamp//[^0-9]/}"
test_root="/tmp/login-cooldown-test-${stamp}"
source_changed=0
deployment_complete=0

cleanup() {
    sudo -u postgres dropdb --if-exists "${test_database}" >/dev/null 2>&1 || true
    rm -rf -- "${test_root}"
    if [[ "${source_changed}" = 1 && "${deployment_complete}" = 0 ]]; then
        rm -rf -- \
            "${root}/custom_addons/shop_api" \
            "${root}/custom_addons/website_security_center"
        tar -xzf "${backup_dir}/modules.tar.gz" -C "${root}/custom_addons"
        chown -R odoo:odoo \
            "${root}/custom_addons/shop_api" \
            "${root}/custom_addons/website_security_center"
        systemctl start "${service}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

test -s "${release}"
test "$(sha256sum "${release}" | awk '{print $1}')" = "${expected_hash}"
grep -q '19.0.1.22.2' \
    < <(tar -xOzf "${release}" custom_addons/shop_api/__manifest__.py)
grep -q '19.0.1.1.0' \
    < <(tar -xOzf "${release}" custom_addons/website_security_center/__manifest__.py)
grep -q 'login_cooldown' \
    < <(tar -xOzf "${release}" custom_addons/shop_api/controllers/main.py)

install -d -o root -g postgres -m 0750 "${backup_dir}"
sudo -u postgres pg_dump -Fc "${database}" > "${backup_dir}/${database}.dump"
test -s "${backup_dir}/${database}.dump"
chown postgres:postgres "${backup_dir}/${database}.dump"
chmod 0600 "${backup_dir}/${database}.dump"
tar -czf "${backup_dir}/modules.tar.gz" -C "${root}/custom_addons" \
    shop_api website_security_center

sudo -u postgres createdb -T template0 -O odoo "${test_database}"
sudo -u odoo pg_restore --no-owner --no-privileges \
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

install -d -o odoo -g odoo -m 0755 "${test_root}"
tar -xzf "${release}" -C "${test_root}"
chown -R odoo:odoo "${test_root}"
test_log="${backup_dir}/website-security-tests.log"
sudo -u odoo -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${test_database}" \
    --addons-path="${test_root}/custom_addons,/opt/odoo/odoo-src/odoo/addons,/opt/odoo/odoo-src/addons,/opt/odoo/project/custom_addons" \
    -u shop_api,website_security_center \
    --test-enable --test-tags '/website_security_center' --stop-after-init \
    --http-port=18069 --gevent-port=18072 --log-level=test \
    2>&1 | tee "${test_log}"
if grep -Eq '([1-9][0-9]* failed|[1-9][0-9]* error)' "${test_log}"; then
    printf 'Odoo reported a failing website-security test.\n' >&2
    exit 1
fi

# Exercise the real authentication override against a disposable persisted
# account.  This validates durable counting across rejected transactions.
sudo -u odoo -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" shell \
    -c "${config}" -d "${test_database}" --no-http \
    --addons-path="${test_root}/custom_addons,/opt/odoo/odoo-src/odoo/addons,/opt/odoo/odoo-src/addons,/opt/odoo/project/custom_addons" <<'PY'
from datetime import timedelta
from odoo import Command, fields
from odoo.exceptions import AccessDenied

login = "login-cooldown-smoke@example.test"
partner = env["res.partner"].sudo().create({"name": "Login Cooldown Smoke", "email": login})
user = env["res.users"].sudo().with_context(no_reset_password=True).create({
    "name": partner.name,
    "login": login,
    "password": "correct-test-password",
    "partner_id": partner.id,
    "group_ids": [Command.set([env.ref("base.group_portal").id])],
})
env.cr.commit()
credential = {"type": "password", "login": login, "password": "incorrect-test-password"}
agent = {"interactive": True, "REMOTE_ADDR": "203.0.113.50"}
for _attempt in range(5):
    try:
        env["res.users"].authenticate(credential, agent)
    except AccessDenied:
        pass
    else:
        raise AssertionError("invalid credential was accepted")
    env.cr.rollback()
user.invalidate_recordset()
status = user._website_security_login_cooldown_status()
assert status["locked"], status
assert status["failure_count"] == 5, status
try:
    env["res.users"].authenticate({
        "type": "password", "login": login, "password": "correct-test-password",
    }, agent)
except AccessDenied:
    pass
else:
    raise AssertionError("locked account was allowed to authenticate")
env.cr.rollback()
env.cr.execute(
    "UPDATE res_users SET security_login_cooldown_until=%s WHERE id=%s",
    [fields.Datetime.now() - timedelta(minutes=1), user.id],
)
env.cr.commit()
auth_info = env["res.users"].authenticate({
    "type": "password", "login": login, "password": "correct-test-password",
}, agent)
assert auth_info["uid"] == user.id, auth_info
env.cr.commit()
user.invalidate_recordset()
assert user.security_login_failure_count == 0
assert not user.security_login_cooldown_until
print("ACCOUNT_LOGIN_COOLDOWN_AUTH_FLOW_OK")
PY

sudo -u postgres dropdb "${test_database}"

systemctl stop "${service}"
tar -xzf "${release}" -C "${root}"
source_changed=1
chown -R odoo:odoo \
    "${root}/custom_addons/shop_api" \
    "${root}/custom_addons/website_security_center"
sudo -u odoo -H \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" server \
    -c "${config}" -d "${database}" \
    -u shop_api,website_security_center --stop-after-init --no-http
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
security_module = env["ir.module.module"].sudo().search([("name", "=", "website_security_center")], limit=1)
api_module = env["ir.module.module"].sudo().search([("name", "=", "shop_api")], limit=1)
policy = env["website.security.policy"].sudo().search([("company_id", "=", env.company.id)], limit=1)
assert security_module.latest_version == "19.0.1.1.0", security_module.latest_version
assert api_module.latest_version == "19.0.1.22.2", api_module.latest_version
assert policy.login_cooldown_failure_threshold == 5
assert policy.login_cooldown_minutes == 60
assert "security_login_cooldown_until" in env["res.users"]._fields
print("ERP_ACCOUNT_LOGIN_COOLDOWN_VERIFIED")
PY
)"
printf '%s\n' "${verification}"
grep -q 'ERP_ACCOUNT_LOGIN_COOLDOWN_VERIFIED' <<<"${verification}"

deployment_complete=1
printf 'backup_dir=%s\n' "${backup_dir}"
