from __future__ import annotations

import smtplib
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from postmaster.delivery_reliability import (
    ReliabilityStore,
    RetryPolicy,
    ThrottleController,
    classify_smtp_failure,
)
from postmaster.email_analytics import EmailAnalyticsStore


class ReliabilityFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = str(root / "analytics.db")
        self.key = str(root / "analytics.key")
        self.analytics = EmailAnalyticsStore(db_path=self.db, key_path=self.key)
        self.store = ReliabilityStore(db_path=self.db)
        self.campaign = self.analytics.create_campaign(
            account_id="sender",
            sender="sender@example.com",
            subject="hello",
            track_opens=False,
            amp_used=False,
        )
        self.delivery = self.analytics.create_delivery(
            campaign_id=self.campaign["id"],
            account_id="sender",
            recipient="person@example.net",
            recipient_role="to",
        )
        self.analytics.mark_sent(self.delivery["id"], "<m1@example.com>")

    def tearDown(self):
        self.tmp.cleanup()


class FailureClassificationTests(unittest.TestCase):
    def test_4xx_is_temporary(self):
        result = classify_smtp_failure(smtplib.SMTPDataError(451, b"try later"), phase="data_response")
        self.assertTrue(result["temporary"])
        self.assertFalse(result["permanent"])
        self.assertEqual(result["smtp_code"], 451)

    def test_5xx_is_permanent(self):
        result = classify_smtp_failure(smtplib.SMTPDataError(550, b"rejected"), phase="data_response")
        self.assertFalse(result["temporary"])
        self.assertTrue(result["permanent"])
        self.assertEqual(result["classification"], "permanent_smtp_failure")

    def test_auth_is_permanent(self):
        result = classify_smtp_failure(smtplib.SMTPAuthenticationError(535, b"bad credentials"), phase="auth")
        self.assertFalse(result["temporary"])
        self.assertEqual(result["classification"], "authentication_failure")

    def test_unsupported_capability_is_permanent(self):
        result = classify_smtp_failure(
            smtplib.SMTPNotSupportedError("SMTPUTF8 is required"),
            phase="mail_from",
        )
        self.assertFalse(result["temporary"])
        self.assertTrue(result["permanent"])
        self.assertFalse(result["uncertain"])
        self.assertEqual(result["classification"], "unsupported_smtp_capability")

    def test_connect_timeout_is_retryable(self):
        result = classify_smtp_failure(socket.timeout("timed out"), phase="connect")
        self.assertTrue(result["temporary"])
        self.assertEqual(result["classification"], "timeout")

    def test_data_transport_failure_is_not_retried(self):
        result = classify_smtp_failure(socket.timeout("lost response"), phase="data_waiting")
        self.assertTrue(result["uncertain"])
        self.assertFalse(result["temporary"])
        self.assertEqual(result["classification"], "delivery_uncertain")


class RetryPolicyTests(unittest.TestCase):
    def test_exponential_backoff(self):
        policy = RetryPolicy(base_delay_seconds=2, max_delay_seconds=60, jitter_min=1, jitter_max=1)
        self.assertEqual(policy.delay_for(1), 2)
        self.assertEqual(policy.delay_for(2), 4)
        self.assertEqual(policy.delay_for(3), 8)

    def test_max_delay(self):
        policy = RetryPolicy(base_delay_seconds=10, max_delay_seconds=15, jitter_min=1, jitter_max=1)
        self.assertEqual(policy.delay_for(5), 15)

    def test_jitter_bounded(self):
        policy = RetryPolicy(base_delay_seconds=10, max_delay_seconds=100, jitter_min=0.75, jitter_max=1.25)
        self.assertEqual(policy.delay_for(1, rand=lambda low, high: low), 7.5)
        self.assertEqual(policy.delay_for(1, rand=lambda low, high: high), 12.5)


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def sleep(self, seconds):
        with self.lock:
            self.value += seconds


class ThrottleTests(unittest.TestCase):
    def test_global_limit(self):
        clock = FakeClock()
        throttle = ThrottleController(global_per_second=2, account_per_second=10, domain_per_second=10, clock=clock, sleeper=clock.sleep)
        self.assertEqual(throttle.acquire("a", ["x@example.net"]), 0)
        self.assertEqual(throttle.acquire("b", ["y@other.net"]), 0)
        self.assertGreaterEqual(throttle.acquire("c", ["z@third.net"]), 1.0)

    def test_account_limit(self):
        clock = FakeClock()
        throttle = ThrottleController(global_per_second=20, account_per_second=1, domain_per_second=20, clock=clock, sleeper=clock.sleep)
        self.assertEqual(throttle.acquire("a", ["x@example.net"]), 0)
        self.assertGreaterEqual(throttle.acquire("a", ["y@other.net"]), 1.0)

    def test_domain_limit(self):
        clock = FakeClock()
        throttle = ThrottleController(global_per_second=20, account_per_second=20, domain_per_second=1, clock=clock, sleeper=clock.sleep)
        self.assertEqual(throttle.acquire("a", ["x@example.net"]), 0)
        self.assertGreaterEqual(throttle.acquire("b", ["y@example.net"]), 1.0)

    def test_concurrent_acquire_is_safe(self):
        clock = FakeClock()
        throttle = ThrottleController(global_per_second=100, account_per_second=100, domain_per_second=100, clock=clock, sleeper=clock.sleep)
        failures = []
        def worker(index):
            try:
                throttle.acquire(f"a{index % 3}", [f"u{index}@example.net"])
            except Exception as exc:  # pragma: no cover - diagnostic only
                failures.append(exc)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])


class StoreTests(ReliabilityFixture):
    def test_additive_delivery_columns_exist(self):
        with self.store._connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracking_deliveries)")}
        for name in self.store.DELIVERY_COLUMNS:
            self.assertIn(name, columns)

    def test_attempt_history_and_delivery_state(self):
        self.store.record_attempt(
            operation_id="op-1",
            delivery_id=self.delivery["id"],
            account_id="sender",
            recipient="person@example.net",
            attempt_number=1,
            state="temporarily_failed",
            message_id="<m1@example.com>",
            idempotency_key="key-1",
            classification="temporary_smtp_failure",
            smtp_code=451,
            detail="try later",
            phase="data_response",
            next_retry_at="2099-01-01T00:00:00+00:00",
        )
        attempts = self.store.list_attempts(self.delivery["id"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["smtp_code"], 451)
        row = self.analytics.get_delivery(self.delivery["id"])
        self.assertEqual(row["delivery_state"], "temporarily_failed")
        self.assertEqual(row["attempt_count"], 1)

    def test_operation_sent_is_idempotency_guard(self):
        self.store.record_attempt(
            operation_id="op-1",
            delivery_id=self.delivery["id"],
            account_id="sender",
            recipient="person@example.net",
            attempt_number=1,
            state="sent",
            message_id="<m1@example.com>",
            idempotency_key="key-1",
        )
        self.assertTrue(self.store.operation_sent("op-1", "<m1@example.com>"))
        self.assertFalse(self.store.operation_sent("op-2", "<m1@example.com>"))

    def test_hard_bounce_suppresses(self):
        result = self.store.record_bounce(
            recipient="person@example.net",
            classification="user_unknown",
            delivery_id=self.delivery["id"],
            status="5.1.1",
            diagnostic="550 user unknown",
            confidence="high",
        )
        self.assertTrue(result["suppression"]["active"])
        self.assertEqual(result["suppression"]["reason"], "hard_bounce")

    def test_one_soft_bounce_does_not_suppress(self):
        result = self.store.record_bounce(
            recipient="person@example.net",
            classification="mailbox_full",
            delivery_id=self.delivery["id"],
            status="4.2.2",
            diagnostic="mailbox full",
            confidence="high",
        )
        self.assertIsNone(result["suppression"])
        record = self.store.get_suppression("person@example.net")
        self.assertIsNotNone(record)
        self.assertFalse(record["active"])
        self.assertEqual(record["soft_bounce_count"], 1)
        self.assertEqual(self.store.blocked_recipients(["person@example.net"]), [])

    def test_repeated_soft_bounces_suppress_at_default_threshold(self):
        for _ in range(2):
            result = self.store.record_bounce(
                recipient="person@example.net",
                classification="soft_bounce",
                delivery_id=self.delivery["id"],
                status="4.4.1",
                diagnostic="temporary failure",
                confidence="medium",
            )
            self.assertIsNone(result["suppression"])
        result = self.store.record_bounce(
            recipient="person@example.net",
            classification="soft_bounce",
            delivery_id=self.delivery["id"],
            status="4.4.1",
            diagnostic="temporary failure",
            confidence="medium",
        )
        self.assertTrue(result["suppression"]["active"])
        self.assertEqual(result["suppression"]["reason"], "repeated_soft_bounce")
        self.assertEqual(result["soft_bounce_count"], 3)

    def test_manual_suppress_and_unsuppress(self):
        record = self.store.suppress("other@example.net", reason="manual", source="webgui")
        self.assertTrue(record["active"])
        blocked = self.store.blocked_recipients(["other@example.net"])
        self.assertEqual(len(blocked), 1)
        result = self.store.unsuppress("other@example.net", source="webgui")
        self.assertFalse(result["active"])
        self.assertEqual(self.store.blocked_recipients(["other@example.net"]), [])

    def test_unsubscribe_suppression_is_explicit(self):
        record = self.store.suppress("person@example.net", reason="unsubscribe", source="manual")
        self.assertTrue(record["active"])
        self.assertEqual(record["reason"], "unsubscribe")

    def test_correlation_prefers_envid(self):
        dsn = {
            "correlation": {
                "envelope_id": self.delivery["id"],
                "message_id": "<different@example.com>",
                "in_reply_to": "",
                "references": "",
            },
            "derived": {"recipient": "person@example.net"},
        }
        result = self.store.correlate_delivery(dsn)
        self.assertEqual(result["delivery_id"], self.delivery["id"])
        self.assertEqual(result["method"], "original_envelope_id")
        self.assertEqual(result["confidence"], "high")

    def test_correlation_message_id(self):
        dsn = {
            "correlation": {"envelope_id":"", "message_id":"<m1@example.com>", "in_reply_to":"", "references":""},
            "derived": {"recipient":"person@example.net"},
        }
        self.assertEqual(self.store.correlate_delivery(dsn)["method"], "original_message_id")

    def test_human_reply_updates_conversation_state(self):
        raw = b"From: person@example.net\r\nTo: sender@example.com\r\nSubject: Re: hello\r\nIn-Reply-To: <m1@example.com>\r\nReferences: <m1@example.com>\r\nAuto-Submitted: no\r\n\r\nThanks\r\n"
        result = self.store.process_inbound(raw, account_id="sender")
        self.assertEqual(result["kind"], "replied")
        row = self.analytics.get_delivery(self.delivery["id"])
        self.assertEqual(row["conversation_state"], "replied")
        self.assertTrue(row["replied_at"])

    def test_auto_reply_does_not_count_as_human(self):
        raw = b"From: person@example.net\r\nTo: sender@example.com\r\nSubject: Automatic reply\r\nIn-Reply-To: <m1@example.com>\r\nAuto-Submitted: auto-replied\r\n\r\nAway\r\n"
        result = self.store.process_inbound(raw, account_id="sender")
        self.assertEqual(result["kind"], "auto_reply")
        row = self.analytics.get_delivery(self.delivery["id"])
        self.assertEqual(row["conversation_state"], "auto_reply")
        self.assertFalse(bool(row["replied_at"]))

    def test_metrics_keep_observed_inferred_semantics(self):
        metrics = self.store.metrics(account_id="sender")
        self.assertEqual(metrics["observed"]["deliveries"], 1)
        self.assertIn("observed", metrics["semantics"])
        self.assertIn("inferred", metrics["semantics"])
        self.assertIn("estimated", metrics["semantics"])


if __name__ == "__main__":
    unittest.main()
