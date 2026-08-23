from __future__ import annotations

import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from postmaster.outbound_operations_v969 import OutboundOperationStore
from postmaster import outbound_archive_v969 as archive
from postmaster import webgui_v969


class _FakeClient:
    def __init__(self):
        self.settings = SimpleNamespace(account_id="acct", email_address="sender@example.test")
        self.appended = []

    def _validate_recipients(self, values):
        return [str(value).strip() for value in values if str(value).strip()]


class OutboundArchiveV969Tests(unittest.TestCase):
    def test_logical_fanout_has_one_canonical_archive_and_private_bcc_mapping(self):
        client = _FakeClient()
        deliveries = [
            {"delivery_id": "d1", "recipient": "to@example.test", "role": "to", "message_id": "<m1@example.test>", "sent_copy_saved": True},
            {"delivery_id": "d2", "recipient": "cc@example.test", "role": "cc", "message_id": "<m2@example.test>", "sent_copy_saved": True},
            {"delivery_id": "d3", "recipient": "bcc@example.test", "role": "bcc", "message_id": "<m3@example.test>", "sent_copy_saved": True},
        ]

        canonical = EmailMessage()
        canonical["From"] = "sender@example.test"
        canonical["To"] = "to@example.test"
        canonical["Cc"] = "cc@example.test"
        canonical["Message-ID"] = "<m1@example.test>"
        canonical.set_content("hello")
        self.assertIsNone(canonical.get("Bcc"))

        def original_send(self, *args, **kwargs):
            archive._ARCHIVE_CONTEXT.get()["canonical_message"] = canonical
            return {"sent": True, "campaign_id": "cmp_1", "deliveries": [dict(row) for row in deliveries]}

        def original_save(self, msg):
            self.appended.append(msg)
            return True, None

        with tempfile.TemporaryDirectory() as temp:
            store = OutboundOperationStore(str(Path(temp) / "outbound.db"))
            with patch.object(archive, "outbound_operation_store", return_value=store), patch.object(
                archive, "_ORIGINAL_SAVE_SENT_COPY", original_save
            ), patch.object(
                archive.PostmasterV960NewsletterMailClient,
                "_send_individualized",
                original_send,
            ):
                archive._install_outbound_archive_boundary()
                wrapped = archive.PostmasterV960NewsletterMailClient._send_individualized
                result = wrapped(
                    client,
                    to=["to@example.test"],
                    cc=["cc@example.test"],
                    bcc=["bcc@example.test"],
                    subject="Subject",
                    track_opens=False,
                )

            self.assertEqual(len(client.appended), 1)
            self.assertEqual(result["sent_append_count"], 1)
            self.assertEqual(result["canonical_sent_message_id"], "<m1@example.test>")
            canonical_rows = [row for row in result["deliveries"] if row["sent_copy_saved"]]
            self.assertEqual(len(canonical_rows), 1)
            self.assertEqual(canonical_rows[0]["message_id"], "<m1@example.test>")
            self.assertTrue(canonical_rows[0]["canonical_sent_archive"])
            for row in result["deliveries"][1:]:
                self.assertFalse(row["sent_copy_saved"])
                self.assertFalse(row["canonical_sent_archive"])

            meta = store.by_message_id("acct", "<m3@example.test>")
            self.assertEqual(meta["bcc"], ["bcc@example.test"])
            self.assertEqual(meta["to"], ["to@example.test"])
            self.assertEqual(meta["cc"], ["cc@example.test"])
            self.assertTrue(meta["canonical_sent_archived"])
            self.assertNotIn("Bcc:", canonical.as_string())

    def test_archive_failure_does_not_turn_successful_fanout_into_retryable_send_failure(self):
        client = _FakeClient()
        canonical = EmailMessage()
        canonical["Message-ID"] = "<m1@example.test>"
        canonical.set_content("hello")

        def original_send(self, *args, **kwargs):
            archive._ARCHIVE_CONTEXT.get()["canonical_message"] = canonical
            return {
                "sent": True,
                "campaign_id": "cmp_fail",
                "deliveries": [{
                    "delivery_id": "d1", "recipient": "to@example.test", "role": "to",
                    "message_id": "<m1@example.test>", "sent_copy_saved": True,
                }],
            }

        def original_save(self, msg):
            return False, "OSError"

        with tempfile.TemporaryDirectory() as temp:
            store = OutboundOperationStore(str(Path(temp) / "outbound.db"))
            with patch.object(archive, "outbound_operation_store", return_value=store), patch.object(
                archive, "_ORIGINAL_SAVE_SENT_COPY", original_save
            ), patch.object(
                archive.PostmasterV960NewsletterMailClient, "_send_individualized", original_send
            ):
                archive._install_outbound_archive_boundary()
                result = archive.PostmasterV960NewsletterMailClient._send_individualized(
                    client, to=["to@example.test"], subject="Subject", track_opens=False
                )
        self.assertTrue(result["sent"])
        self.assertFalse(result["canonical_sent_copy_saved"])
        self.assertEqual(result["sent_append_count"], 0)
        self.assertEqual(result["canonical_sent_error"], "OSError")
        self.assertFalse(result["deliveries"][0]["sent_copy_saved"])


class WebGuiSentMetadataV969Tests(unittest.TestCase):
    def _request(self):
        return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})

    def test_sent_detail_adds_private_to_cc_bcc_and_preserves_date(self):
        msg = EmailMessage()
        msg["Message-ID"] = "<canonical@example.test>"
        msg["To"] = "mime-to@example.test"
        msg["Cc"] = "mime-cc@example.test"
        raw = msg.as_bytes()

        fake_v963 = SimpleNamespace(
            confirm_full_html=lambda *a, **k: None,
            _detail=lambda *a, **k: '<p class="small muted">To: old@example.test · 2026-08-24 01:00:00+02:00</p><div>body</div>',
        )
        base = SimpleNamespace(
            mailbox_cache_store=lambda: SimpleNamespace(raw_message=lambda *a: raw),
        )
        metadata = {
            "to": ["to@example.test"],
            "cc": ["cc@example.test"],
            "bcc": ["bcc@example.test"],
        }
        with patch.object(webgui_v969, "_sender_metadata", return_value=metadata):
            webgui_v969._install_webgui_v969(base, fake_v963, SimpleNamespace())
            rendered = fake_v963._detail(base, {}, "acct", "Sent", "sent", "1", self._request())
        self.assertIn("To: to@example.test", rendered)
        self.assertIn("Cc: cc@example.test", rendered)
        self.assertIn("Bcc: bcc@example.test", rendered)
        self.assertIn("2026-08-24 01:00:00+02:00", rendered)

    def test_received_detail_is_not_enriched_with_bcc(self):
        fake_v963 = SimpleNamespace(
            confirm_full_html=lambda *a, **k: None,
            _detail=lambda *a, **k: '<p class="small muted">From: sender@example.test · 2026-08-24</p>',
        )
        base = SimpleNamespace(mailbox_cache_store=lambda: SimpleNamespace())
        webgui_v969._install_webgui_v969(base, fake_v963, SimpleNamespace())
        rendered = fake_v963._detail(base, {}, "acct", "INBOX", "received", "1", self._request())
        self.assertIn("From: sender@example.test", rendered)
        self.assertNotIn("Bcc:", rendered)


if __name__ == "__main__":
    unittest.main()
