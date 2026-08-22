from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from postmaster.delivery_reliability import ReliabilityStore
from postmaster.mail_bridge import Settings
from postmaster.mail_v950 import PostmasterV950MailClient
from postmaster.mail_v960 import PostmasterV960MailClient
from postmaster.outbound_safety import OutboundSafetyStore


class _StaticInboundClient(PostmasterV960MailClient):
    def __init__(self, settings, raw: bytes, *, db_path: str):
        self._test_raw = raw
        super().__init__(
            settings,
            reliability=ReliabilityStore(db_path),
            outbound_safety=OutboundSafetyStore(db_path),
        )

    @contextlib.contextmanager
    def _imap(self):
        yield object()

    def _select(self, conn, mailbox: str, *, readonly: bool = False):
        return None

    def _fetch_raw(self, conn, uid: str):
        return self._test_raw, False

    def _seen_for_uid(self, conn, uid: str) -> bool:
        return False


class CompatibilityV960Tests(unittest.TestCase):
    def settings(self) -> Settings:
        return Settings(
            email_address="sender@example.test",
            email_password="test-only",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            inbox_mailbox="INBOX",
            sent_mailbox="Sent",
        )

    def test_mail_client_idempotency_stops_second_call_before_v950_send_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "analytics.db")
            client = PostmasterV960MailClient(
                self.settings(),
                reliability=ReliabilityStore(db),
                outbound_safety=OutboundSafetyStore(db, duplicate_window_seconds=120),
            )
            backend_result = {
                "ok": True,
                "sent": True,
                "message_id": "<one@example.test>",
                "delivery_state": "sent",
            }
            with patch.object(PostmasterV950MailClient, "send_email", autospec=True, return_value=backend_result) as send:
                first = client.send_email(
                    to=["recipient@example.net"],
                    subject="Incident regression",
                    body="Only one underlying send",
                    idempotency_key="incident-duplicate-001",
                )
                second = client.send_email(
                    to=["recipient@example.net"],
                    subject="Incident regression",
                    body="Only one underlying send",
                    idempotency_key="incident-duplicate-001",
                )
            self.assertEqual(send.call_count, 1)
            self.assertTrue(first["smtp_send_performed"])
            self.assertFalse(second["smtp_send_performed"])
            self.assertTrue(second["idempotent_replay"])

    def test_legacy_get_email_keeps_original_html_but_marks_it_unsanitized(self):
        raw = b"""From: sender@example.net\r
To: me@example.test\r
Subject: Legacy compatibility\r
Content-Type: text/html; charset=utf-8\r
\r
<p>Hello</p><img src="https://tracker.example/open" width="1" height="1">"""
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "analytics.db")
            client = _StaticInboundClient(self.settings(), raw, db_path=db)
            result = client.get_email("INBOX", "10")
        self.assertIn("https://tracker.example/open", result["body_html"])
        self.assertEqual(result["content_safety"]["body_html"], "original_unsanitized")
        self.assertTrue(result["content_safety"]["legacy_compatibility"])

    def test_inspection_get_email_is_sanitized_by_default(self):
        raw = b"""From: sender@example.net\r
To: me@example.test\r
Subject: Safe mode\r
Content-Type: text/html; charset=utf-8\r
\r
<p>Hello <a href="https://example.org">link</a></p><img src="https://tracker.example/open" width="1" height="1">"""
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "analytics.db")
            client = _StaticInboundClient(self.settings(), raw, db_path=db)
            result = client.get_email("INBOX", "11", inspection="full")
        self.assertNotIn("<img", result["body_html"])
        self.assertIn("https://example.org", result["body_html"])
        self.assertEqual(result["content_safety"]["body_html"], "sanitized_static")
        self.assertEqual(result["privacy_inspection"]["network_requests_performed"], 0)
        self.assertEqual(result["privacy_inspection"]["tracking_pixel_count"], 1)
        self.assertIn("headers", result)
        self.assertIn("mime", result)

    def test_inspection_raw_requires_explicit_acknowledgement(self):
        raw = b"From: x@example.net\r\nTo: y@example.test\r\nContent-Type: text/html\r\n\r\n<img src=\"https://remote.example/x\">"
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "analytics.db")
            client = _StaticInboundClient(self.settings(), raw, db_path=db)
            with self.assertRaisesRegex(Exception, "acknowledge_unsanitized_content_risk"):
                client.get_email("INBOX", "12", inspection="summary", content_mode="raw")
            result = client.get_email(
                "INBOX",
                "12",
                inspection="summary",
                content_mode="raw",
                acknowledge_unsanitized_content_risk=True,
            )
        self.assertIn("https://remote.example/x", result["body_html"])
        self.assertIn("body_html_safe", result)


if __name__ == "__main__":
    unittest.main()
