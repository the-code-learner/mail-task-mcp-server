from __future__ import annotations

import os
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from postmaster.delivery_reliability import ReliabilityStore, RetryPolicy, ThrottleController
from postmaster.file_store import FileStore
from postmaster.mail_bridge import Settings
from postmaster.mail_v950 import PostmasterV950MailClient
from postmaster.mail_v960_unsubscribe import PostmasterV960NewsletterMailClient
from postmaster.outbound_safety import OutboundSafetyStore
from postmaster.outbound_operations_v969 import OutboundOperationStore
from postmaster import outbound_archive_v969 as archive
from postmaster import webgui_v969


class _NoopUnsubscribeManager:
    pass


class _CachedSuccessService:
    def render_cached_message(self, **kwargs):
        return {
            "ok": True,
            "render_state": "success",
            "cache_only": True,
            "network_requests_performed": 0,
            "diagnostics": {"negative_cache_hits": 0},
        }


class _UiBase:
    def __init__(self, raw: bytes):
        self.raw = raw

    def mailbox_cache_store(self):
        return SimpleNamespace(raw_message=lambda *args: self.raw)

    def _csrf_value(self):
        return "csrf"


class NormalSendIntegrationV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = self.temp.name
        self.env_patch = patch.dict(
            os.environ,
            {"RECIPIENT_POLICY_DB_PATH": os.path.join(root, "mail_policy.db")},
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.operation_store = OutboundOperationStore(os.path.join(root, "logical.db"))
        self.safety = OutboundSafetyStore(
            os.path.join(root, "safety.db"), duplicate_window_seconds=0
        )
        self.reliability = ReliabilityStore(os.path.join(root, "reliability.db"))
        self.file_store = FileStore(
            db_path=os.path.join(root, "files.db"),
            root=os.path.join(root, "files"),
        )
        self.settings = Settings(
            email_address="sender@example.com",
            email_password="pw",
            account_id="acct",
            enable_send=True,
            save_sent_copy=True,
            send_recipient_allowlist=("example.com",),
            allow_previous_sent_recipients=False,
            tracking_default=False,
        )
        self.client = PostmasterV960NewsletterMailClient(
            self.settings,
            outbound_safety=self.safety,
            reliability=self.reliability,
            throttle=ThrottleController(
                global_per_second=1000,
                account_per_second=1000,
                domain_per_second=1000,
                sleeper=lambda _seconds: None,
            ),
            retry_policy=RetryPolicy(max_attempts=1),
            unsubscribe_manager=_NoopUnsubscribeManager(),
            file_store=self.file_store,
            sleeper=lambda _seconds: None,
        )
        self.smtp: list[tuple[bytes, list[str]]] = []
        self.sent_appends: list[bytes] = []

    @staticmethod
    def _request() -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [],
                "query_string": b"",
            }
        )

    def _transport_patches(self):
        def fake_smtp(_client, msg, recipients, delivery_id):
            self.smtp.append((msg.as_bytes(policy=policy.SMTP), list(recipients)))
            return {
                "dsn_supported": False,
                "dsn_envid": "",
                "dsn_notify": "",
                "smtp_capabilities": {},
                "smtp_capabilities_pre_tls": None,
                "smtp_capabilities_post_tls": {},
                "smtp_security": "test",
                "smtp_tls": {},
            }

        def fake_save(_client, msg):
            self.sent_appends.append(msg.as_bytes(policy=policy.SMTP))
            return True, None

        return (
            patch.object(PostmasterV950MailClient, "_smtp_send_once", fake_smtp),
            patch.object(archive, "_ORIGINAL_SAVE_SENT_COPY", fake_save),
            patch.object(archive, "outbound_operation_store", return_value=self.operation_store),
        )

    def _send(self, *, to, cc=None, bcc=None):
        patches = self._transport_patches()
        with patches[0], patches[1], patches[2]:
            archive._install_outbound_archive_boundary()
            return self.client.send_email(
                to=list(to),
                cc=list(cc or []),
                bcc=list(bcc or []),
                subject="Subject",
                body="Body",
                track_opens=False,
                automatic_unsubscribe=False,
            )

    def _follow_up(self, source: EmailMessage, *, bcc=None):
        patches = self._transport_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patch.object(self.client, "_thread_source_message", return_value=source),
        ):
            archive._install_outbound_archive_boundary()
            return self.client.follow_up_email(
                mailbox="Sent",
                uid="42",
                body="Follow-up body",
                bcc=list(bcc or []),
                track_opens=False,
            )

    def _assert_sent_ui(self, raw: bytes, *, expected_to, expected_cc=None, expected_bcc=None):
        fake_v963 = SimpleNamespace(
            confirm_full_html=lambda *a, **k: None,
            _detail=lambda *a, **k: (
                '<p class="small muted">To: old@example.test · 2026-08-24</p>'
                '<span class="v963-chip ok">Email sicura · default</span>'
            ),
        )
        base = _UiBase(raw)
        with patch.object(
            webgui_v969, "outbound_operation_store", return_value=self.operation_store
        ):
            webgui_v969._install_webgui_v969(base, fake_v963, _CachedSuccessService())
            rendered = fake_v963._detail(
                base, {}, "acct", "Sent", "sent", "1", self._request()
            )
        self.assertIn("To: " + expected_to, rendered)
        if expected_cc:
            self.assertIn("Cc: " + expected_cc, rendered)
        if expected_bcc:
            self.assertIn("Bcc: " + expected_bcc, rendered)

        received_v963 = SimpleNamespace(
            confirm_full_html=lambda *a, **k: None,
            _detail=lambda *a, **k: (
                '<p class="small muted">From: sender@example.com · 2026-08-24</p>'
                '<span class="v963-chip ok">Email sicura · default</span>'
            ),
        )
        webgui_v969._install_webgui_v969(base, received_v963, _CachedSuccessService())
        received = received_v963._detail(
            base, {}, "acct", "INBOX", "received", "1", self._request()
        )
        self.assertNotIn("Bcc:", received)

    def test_real_normal_send_to_bcc_keeps_logical_mapping_private_and_one_canonical_sent(self):
        result = self._send(to=["a@example.com"], bcc=["b@example.com"])
        self.assertTrue(result["outbound_operation_id"].startswith("out_"))
        self.assertEqual(result["logical_outbound_operation_id"], result["outbound_operation_id"])
        self.assertFalse(result.get("deliveries", []))
        self.assertNotIn("delivery_", repr(result))

        self.assertEqual(len(self.smtp), 1)
        smtp_raw, rcpts = self.smtp[0]
        self.assertEqual(rcpts, ["a@example.com", "b@example.com"])
        smtp_msg = BytesParser(policy=policy.default).parsebytes(smtp_raw)
        self.assertEqual(str(smtp_msg.get("To") or ""), "a@example.com")
        self.assertIsNone(smtp_msg.get("Bcc"))

        self.assertEqual(len(self.sent_appends), 1)
        sent_msg = BytesParser(policy=policy.default).parsebytes(self.sent_appends[0])
        self.assertIsNone(sent_msg.get("Bcc"))
        saved = self.operation_store.get_operation(result["outbound_operation_id"])
        self.assertEqual(saved["to"], ["a@example.com"])
        self.assertEqual(saved["cc"], [])
        self.assertEqual(saved["bcc"], ["b@example.com"])
        self.assertEqual(saved["deliveries"], [])
        self.assertEqual(
            {(row["recipient"], row["role"]) for row in saved["recipient_mappings"]},
            {("a@example.com", "to"), ("b@example.com", "bcc")},
        )
        self.assertTrue(saved["canonical_sent_archived"])
        self._assert_sent_ui(
            self.sent_appends[0],
            expected_to="a@example.com",
            expected_bcc="b@example.com",
        )

    def test_real_normal_send_to_cc_bcc_has_three_mappings_but_zero_pseudo_deliveries(self):
        result = self._send(
            to=["a@example.com"],
            cc=["b@example.com"],
            bcc=["c@example.com"],
        )
        self.assertTrue(result["outbound_operation_id"].startswith("out_"))
        self.assertFalse(result.get("deliveries", []))
        self.assertNotIn("delivery_", repr(result))
        self.assertEqual(len(self.smtp), 1)
        raw, rcpts = self.smtp[0]
        self.assertEqual(rcpts, ["a@example.com", "b@example.com", "c@example.com"])
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual(str(msg.get("To") or ""), "a@example.com")
        self.assertEqual(str(msg.get("Cc") or ""), "b@example.com")
        self.assertIsNone(msg.get("Bcc"))
        self.assertEqual(len(self.sent_appends), 1)

        saved = self.operation_store.get_operation(result["outbound_operation_id"])
        self.assertEqual(saved["to"], ["a@example.com"])
        self.assertEqual(saved["cc"], ["b@example.com"])
        self.assertEqual(saved["bcc"], ["c@example.com"])
        self.assertEqual(saved["deliveries"], [])
        self.assertEqual(
            {(row["recipient"], row["role"]) for row in saved["recipient_mappings"]},
            {
                ("a@example.com", "to"),
                ("b@example.com", "cc"),
                ("c@example.com", "bcc"),
            },
        )
        self._assert_sent_ui(
            self.sent_appends[0],
            expected_to="a@example.com",
            expected_cc="b@example.com",
            expected_bcc="c@example.com",
        )

    def test_follow_up_from_canonical_sent_preserves_visible_recipients_not_historical_bcc(self):
        original_operation = "out_original"
        original_mid = "<original@example.test>"
        self.operation_store.record_operation(
            operation_id=original_operation,
            account_id="acct",
            canonical_message_id=original_mid,
            canonical_sent_archived=True,
            to=["a@example.com"],
            cc=["b@example.com"],
            bcc=["c@example.com"],
            deliveries=[],
            recipient_mappings=[
                {"message_id": original_mid, "recipient": "a@example.com", "role": "to"},
                {"message_id": original_mid, "recipient": "b@example.com", "role": "cc"},
                {"message_id": original_mid, "recipient": "c@example.com", "role": "bcc"},
            ],
        )
        source = EmailMessage()
        source["From"] = "sender@example.com"
        source["To"] = "a@example.com"
        source["Cc"] = "b@example.com"
        source["Subject"] = "Subject"
        source["Message-ID"] = original_mid
        source["References"] = "<root@example.test>"
        source.set_content("Original")

        result = self._follow_up(source)
        self.assertTrue(result["sent"])
        self.assertNotEqual(result["outbound_operation_id"], original_operation)
        self.assertEqual(result["in_reply_to"], original_mid)
        self.assertIn("<root@example.test>", result["references"])
        self.assertIn(original_mid, result["references"])
        self.assertEqual(result["resolved_to"], ["a@example.com"])
        self.assertEqual(result["resolved_cc"], ["b@example.com"])

        self.assertEqual(len(self.smtp), 1)
        outbound = BytesParser(policy=policy.default).parsebytes(self.smtp[0][0])
        self.assertEqual(self.smtp[0][1], ["a@example.com", "b@example.com"])
        self.assertEqual(str(outbound.get("To") or ""), "a@example.com")
        self.assertEqual(str(outbound.get("Cc") or ""), "b@example.com")
        self.assertIsNone(outbound.get("Bcc"))
        self.assertEqual(str(outbound.get("In-Reply-To") or ""), original_mid)
        self.assertEqual(len(self.sent_appends), 1)
        follow_sent = BytesParser(policy=policy.default).parsebytes(self.sent_appends[0])
        self.assertIsNone(follow_sent.get("Bcc"))
        self.assertNotIn("/t/c/", self.sent_appends[0].decode("utf-8", errors="replace"))
        self.assertNotIn("/track/open/", self.sent_appends[0].decode("utf-8", errors="replace"))

        original_after = self.operation_store.get_operation(original_operation)
        self.assertEqual(original_after["bcc"], ["c@example.com"])
        self.assertEqual(len(original_after["recipient_mappings"]), 3)
        follow_operation = self.operation_store.get_operation(result["outbound_operation_id"])
        self.assertEqual(follow_operation["to"], ["a@example.com"])
        self.assertEqual(follow_operation["cc"], ["b@example.com"])
        self.assertEqual(follow_operation["bcc"], [])
        self.assertEqual(follow_operation["deliveries"], [])
        self.assertEqual(
            {(row["recipient"], row["role"]) for row in follow_operation["recipient_mappings"]},
            {("a@example.com", "to"), ("b@example.com", "cc")},
        )

    def test_follow_up_new_bcc_is_only_explicit_input_and_stays_private(self):
        source = EmailMessage()
        source["From"] = "sender@example.com"
        source["To"] = "a@example.com"
        source["Cc"] = "b@example.com"
        source["Subject"] = "Subject"
        source["Message-ID"] = "<canonical@example.test>"
        source.set_content("Original")

        result = self._follow_up(source, bcc=["newblind@example.com"])
        self.assertTrue(result["sent"])
        self.assertEqual(
            self.smtp[0][1],
            ["a@example.com", "b@example.com", "newblind@example.com"],
        )
        outbound = BytesParser(policy=policy.default).parsebytes(self.smtp[0][0])
        self.assertIsNone(outbound.get("Bcc"))
        saved = self.operation_store.get_operation(result["outbound_operation_id"])
        self.assertEqual(saved["bcc"], ["newblind@example.com"])
        self.assertEqual(
            {(row["recipient"], row["role"]) for row in saved["recipient_mappings"]},
            {
                ("a@example.com", "to"),
                ("b@example.com", "cc"),
                ("newblind@example.com", "bcc"),
            },
        )
        self.assertEqual(len(self.sent_appends), 1)


if __name__ == "__main__":
    unittest.main()
