from __future__ import annotations

import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path

from postmaster.inbound_inspection import inspect_message
from postmaster.inbound_inspection_html import sanitize_html
from postmaster.mail_bridge import Settings
from postmaster.mail_v960 import classify_mailbox_role
from postmaster.outbound_safety import OutboundSafetyError, OutboundSafetyStore


class OutboundSafetyV960Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "analytics.db")
        self.store = OutboundSafetyStore(self.db, duplicate_window_seconds=120)
        self.calls = 0

    def tearDown(self):
        self.tmp.cleanup()

    def smtp_send(self):
        self.calls += 1
        return {
            "sent": True,
            "message_id": "<v960@example.com>",
            "delivery_state": "sent",
        }

    def test_incident_regression_same_key_same_payload_only_one_smtp_send(self):
        payload = {
            "to": ["creator@example.net"],
            "subject": "Partnership",
            "body": "Hello",
        }
        first = self.store.execute(
            account_id="sender",
            action="send_email",
            payload=payload,
            duplicate_payload=payload,
            callback=self.smtp_send,
            idempotency_key="creator-outreach-001",
        )
        second = self.store.execute(
            account_id="sender",
            action="send_email",
            payload=payload,
            duplicate_payload=payload,
            callback=self.smtp_send,
            idempotency_key="creator-outreach-001",
        )
        self.assertEqual(self.calls, 1, "equivalent outbound calls must not perform two SMTP sends")
        self.assertTrue(first["smtp_send_performed"])
        self.assertFalse(second["smtp_send_performed"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(first["outbound_operation_id"], second["outbound_operation_id"])

    def test_same_key_different_payload_is_rejected(self):
        first_payload = {"to": ["a@example.net"], "subject": "A", "body": "one"}
        second_payload = {"to": ["a@example.net"], "subject": "A", "body": "two"}
        self.store.execute(
            account_id="sender",
            action="send_email",
            payload=first_payload,
            duplicate_payload=first_payload,
            callback=self.smtp_send,
            idempotency_key="same-key",
        )
        with self.assertRaisesRegex(OutboundSafetyError, "different outbound payload"):
            self.store.execute(
                account_id="sender",
                action="send_email",
                payload=second_payload,
                duplicate_payload=second_payload,
                callback=self.smtp_send,
                idempotency_key="same-key",
            )
        self.assertEqual(self.calls, 1)

    def test_duplicate_guard_and_force_send(self):
        payload = {"to": ["a@example.net"], "subject": "A", "body": "same visible message"}
        first = self.store.execute(
            account_id="sender",
            action="send_email",
            payload={**payload, "track_opens": False},
            duplicate_payload=payload,
            callback=self.smtp_send,
        )
        second = self.store.execute(
            account_id="sender",
            action="send_email",
            payload={**payload, "track_opens": True},
            duplicate_payload=payload,
            callback=self.smtp_send,
        )
        self.assertEqual(self.calls, 1)
        self.assertTrue(second["duplicate_guard_replay"])
        forced = self.store.execute(
            account_id="sender",
            action="send_email",
            payload={**payload, "track_opens": True},
            duplicate_payload=payload,
            callback=self.smtp_send,
            force_send=True,
        )
        self.assertEqual(self.calls, 2)
        self.assertTrue(first["sent"])
        self.assertTrue(forced["sent"])

        keyed_payload = {
            "to": ["b@example.net"],
            "subject": "B",
            "body": "same visible message with fresh keys",
        }
        self.store.execute(
            account_id="sender",
            action="send_email",
            payload=keyed_payload,
            duplicate_payload=keyed_payload,
            callback=self.smtp_send,
            idempotency_key="guard-key-1",
        )
        guarded_with_fresh_key = self.store.execute(
            account_id="sender",
            action="send_email",
            payload=keyed_payload,
            duplicate_payload=keyed_payload,
            callback=self.smtp_send,
            idempotency_key="guard-key-2",
        )
        self.assertEqual(self.calls, 3)
        self.assertTrue(guarded_with_fresh_key["duplicate_guard_replay"])
        self.assertFalse(guarded_with_fresh_key["smtp_send_performed"])
        forced_with_fresh_key = self.store.execute(
            account_id="sender",
            action="send_email",
            payload=keyed_payload,
            duplicate_payload=keyed_payload,
            callback=self.smtp_send,
            idempotency_key="guard-key-3",
            force_send=True,
        )
        self.assertEqual(self.calls, 4)
        self.assertTrue(forced_with_fresh_key["smtp_send_performed"])

    def test_delivery_uncertain_never_auto_retries(self):
        calls = 0

        def uncertain():
            nonlocal calls
            calls += 1
            raise RuntimeError("SMTP delivery_uncertain after DATA response timeout")

        payload = {"to": ["a@example.net"], "subject": "A", "body": "uncertain"}
        with self.assertRaisesRegex(RuntimeError, "delivery_uncertain"):
            self.store.execute(
                account_id="sender",
                action="send_email",
                payload=payload,
                duplicate_payload=payload,
                callback=uncertain,
                idempotency_key="uncertain-key",
            )
        with self.assertRaisesRegex(OutboundSafetyError, "delivery_uncertain"):
            self.store.execute(
                account_id="sender",
                action="send_email",
                payload=payload,
                duplicate_payload=payload,
                callback=uncertain,
                idempotency_key="uncertain-key",
            )
        self.assertEqual(calls, 1)


class InboundInspectionV960Tests(unittest.TestCase):
    def test_static_inspection_detects_remote_privacy_signals(self):
        raw = b"""From: sender@example.net\r
To: me@example.com\r
Subject: privacy\r
Authentication-Results: mx.example; spf=pass\r
Content-Type: text/html; charset=utf-8\r
\r
<html><head>
<link rel="stylesheet" href="https://cdn.example.net/mail.css">
<style>.hero{background-image:url('https://img.example.net/bg.jpg')}</style>
</head><body>
<img src="https://track.example.net/open?id=1" width="1" height="1">
<a href="https://click.example.net/r?url=https%3A%2F%2Fexample.org%2Flanding&utm_source=email">example.com</a>
<img src="cid:logo">
</body></html>"""
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        html = str(msg.get_content())
        result = inspect_message(msg, body_html=html, body_text="", mode="full")
        self.assertTrue(result["static_only"])
        self.assertEqual(result["network_requests_performed"], 0)
        self.assertEqual(result["tracking_pixel_count"], 1)
        self.assertEqual(result["remote_css_count"], 1)
        self.assertEqual(result["remote_background_image_count"], 1)
        self.assertGreaterEqual(result["embedded_resource_count"], 1)
        self.assertIn("tracking_parameters", result["risk_flags"])
        self.assertIn("redirectors", result["risk_flags"])
        self.assertIn("anchor_href_mismatch", result["risk_flags"])
        link = result["links"][0]
        self.assertEqual(link["canonical_destination"], "https://example.org/landing")

    def test_sanitizer_removes_automatic_remote_resources_but_keeps_links(self):
        html = (
            '<p>Hello <a href="https://example.org/path?utm_source=mail">site</a></p>'
            '<img src="https://tracker.example/open">'
            '<style>body{background:url(https://tracker.example/bg)}</style>'
            '<script>fetch("https://tracker.example/x")</script>'
        )
        safe = sanitize_html(html)
        self.assertIn('href="https://example.org/path?utm_source=mail"', safe)
        self.assertNotIn("<img", safe)
        self.assertNotIn("<style", safe)
        self.assertNotIn("<script", safe)

    def test_mailbox_role_prefers_special_use_then_configured_and_localized(self):
        settings = Settings(
            email_address="me@example.com",
            email_password="x",
            inbox_mailbox="Posta in arrivo",
            sent_mailbox="Archivio inviati",
            junk_mailbox="Junk custom",
            draft_mailbox="Draft custom",
        )
        self.assertEqual(classify_mailbox_role("Anything", [r"\Sent"], settings), "sent")
        self.assertEqual(classify_mailbox_role("Archivio inviati", [], settings), "sent")
        self.assertEqual(classify_mailbox_role("Cestino", [], settings), "trash")
        self.assertEqual(classify_mailbox_role("Posta in arrivo", [], settings), "received")


if __name__ == "__main__":
    unittest.main()
