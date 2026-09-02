#!/usr/bin/env bash
set -Eeuo pipefail

runtime="/opt/odoo"
config="/etc/odoo.conf"
database="odoo_prod"
login="codex-login-cooldown-smoke@example.invalid"
password="correct-smoke-password"
workdir="$(mktemp -d /tmp/login-cooldown-smoke.XXXXXX)"

cleanup() {
    rm -rf -- "${workdir}"
    LOGIN_SMOKE="${login}" sudo -u odoo -H env LOGIN_SMOKE="${login}" \
        "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" shell \
        -c "${config}" -d "${database}" --no-http <<'PY' >/dev/null 2>&1 || true
import os
login = os.environ["LOGIN_SMOKE"]
incidents = env["website.security.incident"].sudo().search([
    ("safe_details", "ilike", login),
])
incidents.unlink()
users = env["res.users"].sudo().with_context(active_test=False).search([
    ("login", "=", login),
])
partners = users.partner_id
users.unlink()
partners.filtered(lambda partner: not partner.user_ids).unlink()
env.cr.commit()
PY
}
trap cleanup EXIT

LOGIN_SMOKE="${login}" PASSWORD_SMOKE="${password}" \
    sudo -u odoo -H env LOGIN_SMOKE="${login}" PASSWORD_SMOKE="${password}" \
    "${runtime}/venv/bin/python" "${runtime}/odoo-src/odoo-bin" shell \
    -c "${config}" -d "${database}" --no-http <<'PY'
import os
from odoo import Command

login = os.environ["LOGIN_SMOKE"]
password = os.environ["PASSWORD_SMOKE"]
existing = env["res.users"].sudo().with_context(active_test=False).search([
    ("login", "=", login),
])
if existing:
    partners = existing.partner_id
    existing.unlink()
    partners.filtered(lambda partner: not partner.user_ids).unlink()
partner = env["res.partner"].sudo().create({"name": "Cooldown Smoke Account", "email": login})
env["res.users"].sudo().with_context(no_reset_password=True).create({
    "name": partner.name,
    "login": login,
    "password": password,
    "partner_id": partner.id,
    "group_ids": [Command.set([env.ref("base.group_portal").id])],
})
env.cr.commit()
print("SMOKE_ACCOUNT_CREATED")
PY

hk_cookie="${workdir}/hk.cookie"
hk_login_page="${workdir}/hk-login.html"
curl --fail --silent --show-error --max-time 20 \
    -c "${hk_cookie}" https://sunwintersports.com/en/web/login \
    > "${hk_login_page}"
hk_csrf="$(sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' "${hk_login_page}" | head -1)"
test -n "${hk_csrf}"

for attempt in 1 2 3 4 5; do
    curl --fail --silent --show-error --max-time 25 \
        -b "${hk_cookie}" -c "${hk_cookie}" \
        --data-urlencode "csrf_token=${hk_csrf}" \
        --data-urlencode "login=${login}" \
        --data-urlencode "password=incorrect-${attempt}" \
        --data-urlencode "redirect=/purchase-history" \
        https://sunwintersports.com/en/web/login \
        > "${workdir}/hk-attempt-${attempt}.html"
done
# The fifth rejection activates the lock.  The next login request must surface
# the cooldown without attempting another password verification.
curl --fail --silent --show-error --max-time 25 \
    -b "${hk_cookie}" -c "${hk_cookie}" \
    --data-urlencode "csrf_token=${hk_csrf}" \
    --data-urlencode "login=${login}" \
    --data-urlencode "password=${password}" \
    --data-urlencode "redirect=/purchase-history" \
    https://sunwintersports.com/en/web/login \
    > "${workdir}/hk-locked.html"
grep -q 'Too many failed login attempts' "${workdir}/hk-locked.html"
grep -q 'Password reset remains available' "${workdir}/hk-locked.html"

cn_cookie="${workdir}/cn.cookie"
cn_login_page="${workdir}/cn-login.html"
curl --fail --silent --show-error --max-time 20 \
    -H 'Host: sunwintersports.cn' -c "${cn_cookie}" \
    http://127.0.0.1:8070/web/login > "${cn_login_page}"
cn_csrf="$(sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' "${cn_login_page}" | head -1)"
test -n "${cn_csrf}"
curl --fail --silent --show-error --max-time 25 \
    -H 'Host: sunwintersports.cn' -b "${cn_cookie}" -c "${cn_cookie}" \
    --data-urlencode "csrf_token=${cn_csrf}" \
    --data-urlencode "login=${login}" \
    --data-urlencode "password=${password}" \
    --data-urlencode "redirect=/purchase-history" \
    http://127.0.0.1:8070/web/login > "${workdir}/cn-locked.html"
grep -q '登录失败次数过多' "${workdir}/cn-locked.html"
grep -q '密码重置仍可使用' "${workdir}/cn-locked.html"

# Cooldown applies only to login.  Both reset portals must remain reachable;
# the smoke test does not submit the form, so it cannot send an email.
curl --fail --silent --show-error --max-time 20 \
    https://sunwintersports.com/en/web/reset_password \
    > "${workdir}/hk-reset.html"
grep -q 'reset_password' "${workdir}/hk-reset.html"
curl --fail --silent --show-error --max-time 20 \
    -H 'Host: sunwintersports.cn' http://127.0.0.1:8070/web/reset_password \
    > "${workdir}/cn-reset.html"
grep -q 'reset_password' "${workdir}/cn-reset.html"

printf 'LIVE_LOGIN_COOLDOWN_OK hk=en cn=zh reset_portals=available\n'
