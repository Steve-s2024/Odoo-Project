from datetime import timedelta

from odoo import fields
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
