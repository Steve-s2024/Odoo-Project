from datetime import timedelta

from odoo import Command, fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWebsiteSecurityCenter(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.policy = cls.env["website.security.policy"].search([
            ("company_id", "=", cls.env.company.id),
        ], limit=1)
        cls.policy.authentication_failure_threshold = 2

    def _log(self, request_id, status=500, path="/api/v1/orders/test/payments"):
        return self.env["shop.api.request.log"].create({
            "request_id": request_id, "method": "POST", "path": path,
            "source_ip": "203.0.113.10", "response_status": status,
            "duration_ms": 100, "state": "error", "error_code": "test_failure",
        })

    def test_payment_failure_creates_fifo_incident_without_payload(self):
        log = self._log("security-test-1")
        self.policy._scan_request_logs(fields.Datetime.now() - timedelta(minutes=1))
        incident = self.env["website.security.incident"].search([
            ("request_log_id", "=", log.id),
        ])
        self.assertEqual(len(incident), 1)
        self.assertEqual(incident.category, "payment")
        self.assertNotIn("password", (incident.safe_details or "").lower())
        self.assertTrue(incident.activity_ids)

    def test_duplicate_pattern_is_aggregated(self):
        self._log("security-test-2")
        self._log("security-test-3")
        self.policy._scan_request_logs(fields.Datetime.now() - timedelta(minutes=1))
        incidents = self.env["website.security.incident"].search([
            ("category", "=", "payment"), ("state", "=", "open"),
        ])
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents.occurrence_count, 2)

    def test_resolution_closes_only_security_activities(self):
        self._log("security-test-4")
        self.policy._scan_request_logs(fields.Datetime.now() - timedelta(minutes=1))
        incident = self.env["website.security.incident"].search([
            ("category", "=", "payment"), ("state", "=", "open"),
        ], limit=1)
        incident.resolution_note = "已确认测试告警并完成修复。"
        incident.with_user(self.env.ref("base.user_admin")).action_resolve()
        self.assertEqual(incident.state, "resolved")
        self.assertFalse(incident.activity_ids.filtered(
            lambda activity: (activity.summary or "").startswith("网站安全事件：")
        ))

    def test_account_cooldown_is_persistent_and_resets_after_success(self):
        self.policy.write({
            "login_cooldown_failure_threshold": 5,
            "login_cooldown_minutes": 60,
        })
        partner = self.env["res.partner"].create({
            "name": "Cooldown Test Customer",
            "email": "cooldown-test@example.test",
        })
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": partner.name,
            "login": partner.email,
            "password": "safe-test-password",
            "partner_id": partner.id,
            "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
        })

        for attempt in range(1, 6):
            status = user._website_security_register_login_failure(
                source_ip="203.0.113.20"
            )
            self.assertEqual(status["failure_count"], attempt)

        self.assertTrue(status["locked"])
        self.assertGreaterEqual(status["retry_after_seconds"], 3590)
        incident = self.env["website.security.incident"].search([
            ("category", "=", "authentication"),
            ("summary", "=", "账户因连续登录失败进入冷却"),
        ], limit=1)
        self.assertTrue(incident)
        self.assertNotIn("safe-test-password", incident.safe_details)

        user._website_security_clear_login_failures()
        self.assertEqual(user.security_login_failure_count, 0)
        self.assertFalse(user.security_login_cooldown_until)

    def test_expired_cooldown_starts_a_new_failure_sequence(self):
        partner = self.env["res.partner"].create({
            "name": "Expired Cooldown Customer",
            "email": "expired-cooldown@example.test",
        })
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": partner.name,
            "login": partner.email,
            "password": "safe-test-password",
            "partner_id": partner.id,
            "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
            "security_login_failure_count": 5,
            "security_login_cooldown_until": fields.Datetime.now() - timedelta(minutes=1),
        })

        status = user._website_security_register_login_failure()

        self.assertFalse(status["locked"])
        self.assertEqual(status["failure_count"], 1)
