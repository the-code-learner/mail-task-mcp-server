from __future__ import annotations

import os
import tempfile
import unittest
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from postmaster import outbound_archive_v969 as archive
from postmaster.mail_extensions import EnhancedMailClient
from postmaster.mail_v950 import PostmasterV950MailClient
from postmaster.mail_v960_unsubscribe import PostmasterV960NewsletterMailClient
from postmaster.outbound_operations_v969 import OutboundOperationStore
from postmaster.webgui_v969 import _sender_metadata
from postmaster import webgui_v969


class _FakeClient:
    def __init__(self):
        self.settings = SimpleNamespace(
            account_id="acct",
            email_address="sender@example.com",
        )

    def _validate_recipients(self, values):
        return [str(value).strip() for value in values if str(value).strip()]

    def resolve_thread_recipients(self, mailbox, uid, *, mode):
        if mode == "reply":
            return {"to": ["reply@example.com"], "cc": []}
        return {"to": ["follow@example.com"], "cc": ["thread-cc@example.com"]}


class OutboundWebGuiV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OutboundOperationStore(
            os.path.join(self.temp.name, "outbound.db")
        )

    def test_normal_to_bcc_private_metadata_and_recipient_mime(self):
        client = _FakeClient()
        result = {
            "outbound_operation_id": "out_normal_bcc",
            "message_id": "<normal-bcc@example>",
            "sent_copy_saved": True,
            "sent": True,
            "to": ["to@example.com", "blind@example.com"],
        }
        with patch.object(archive, "outbound_operation_store", return_value=self.store):
            archive._record_logical_operation(
                client,
                action="send_email",
                kwargs={
                    "to": ["to@example.com"],
                    "bcc": ["blind@example.com"],
                    "subject": "Subject",
                    "body": "Body",
                },
                result=result,
            )

        saved = self.store.get_operation("out_normal_bcc")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["to"], ["to@example.com"])
        self.assertEqual(saved["cc"], [])
        self.assertEqual(saved["bcc"], ["blind@example.com"])
        self.assertEqual(result["logical_outbound_operation_id"], "out_normal_bcc")

        builder = object.__new__(EnhancedMailClient)
        builder.settings = SimpleNamespace(
            email_address="sender@example.com",
            account_id="acct",
            amp_enabled=False,
        )
        builder._validate_recipients = lambda values: list(values)
        msg, recipients, _ = EnhancedMailClient._build_message(
            builder,
            to=["to@example.com"],
            bcc=["blind@example.com"],
            subject="Subject",
            body="Body",
        )
        self.assertEqual(recipients, ["to@example.com", "blind@example.com"])
        self.assertIsNone(msg.get("Bcc"))
        self.assertEqual(msg.get("To"), "to@example.com")

    def test_normal_to_cc_bcc_private_groups(self):
        client = _FakeClient()
        result = {
            "outbound_operation_id": "out_normal_cc_bcc",
            "message_id": "<normal-cc-bcc@example>",
            "sent_copy_saved": True,
            "sent": True,
            "to": ["to@example.com", "cc@example.com", "blind@example.com"],
        }
        with patch.object(archive, "outbound_operation_store", return_value=self.store):
            archive._record_logical_operation(
                client,
                action="send_email",
                kwargs={
                    "to": ["to@example.com"],
                    "cc": ["cc@example.com"],
                    "bcc": ["blind@example.com"],
                },
                result=result,
            )
        saved = self.store.get_operation("out_normal_cc_bcc")
        self.assertEqual(saved["to"], ["to@example.com"])
        self.assertEqual(saved["cc"], ["cc@example.com"])
        self.assertEqual(saved["bcc"], ["blind@example.com"])

    def test_same_campaign_never_becomes_logical_operation_identity(self):
        client = _FakeClient()
        with patch.object(archive, "outbound_operation_store", return_value=self.store):
            first = archive._record_logical_operation(
                client,
                action="send_email",
                kwargs={"to": ["a@example.com"], "campaign_id": "X"},
                result={
                    "outbound_operation_id": "out_first",
                    "campaign_id": "X",
                    "message_id": "<m-first@example>",
                    "sent_copy_saved": True,
                },
            )
            second = archive._record_logical_operation(
                client,
                action="send_email",
                kwargs={"to": ["b@example.com"], "campaign_id": "X"},
                result={
                    "outbound_operation_id": "out_second",
                    "campaign_id": "X",
                    "message_id": "<m-second@example>",
                    "sent_copy_saved": True,
                },
            )

        self.assertEqual(first["logical_outbound_operation_id"], "out_first")
        self.assertEqual(second["logical_outbound_operation_id"], "out_second")
        self.assertIsNotNone(self.store.get_operation("out_first"))
        self.assertIsNotNone(self.store.get_operation("out_second"))
        self.assertIsNone(self.store.get_operation("X"))

    def test_delivery_message_ids_and_canonical_sent_resolve_one_operation(self):
        client = _FakeClient()
        result = {
            "outbound_operation_id": "out_fanout",
            "campaign_id": "campaign-secondary",
            "canonical_sent_message_id": "<m1@example>",
            "canonical_sent_copy_saved": True,
            "deliveries": [
                {
                    "delivery_id": "delivery_1",
                    "message_id": "<m1@example>",
                    "recipient": "a@example.com",
                    "recipient_role": "to",
                },
                {
                    "delivery_id": "delivery_2",
                    "message_id": "<m2@example>",
                    "recipient": "b@example.com",
                    "recipient_role": "to",
                },
            ],
        }
        with patch.object(archive, "outbound_operation_store", return_value=self.store):
            archive._record_logical_operation(
                client,
                action="send_email",
                kwargs={"to": ["a@example.com", "b@example.com"]},
                result=result,
            )

        first = self.store.by_message_id("acct", "<m1@example>")
        second = self.store.by_message_id("acct", "<m2@example>")
        self.assertEqual(first["operation_id"], "out_fanout")
        self.assertEqual(second["operation_id"], "out_fanout")
        self.assertEqual(len(first["deliveries"]), 2)
        self.assertNotEqual(
            first["deliveries"][0]["message_id"],
            first["deliveries"][1]["message_id"],
        )

    def test_individualized_two_deliveries_one_sent_append(self):
        def original_send(client, *args, **kwargs):
            for index in (1, 2):
                msg = EmailMessage()
                msg["Message-ID"] = f"<m{index}@example>"
                msg["To"] = f"user{index}@example.com"
                PostmasterV950MailClient._save_sent_copy(client, msg)
            return {
                "campaign_id": "cmp_shared",
                "deliveries": [
                    {
                        "delivery_id": "d1",
                        "message_id": "<m1@example>",
                        "recipient": "user1@example.com",
                        "recipient_role": "to",
                    },
                    {
                        "delivery_id": "d2",
                        "message_id": "<m2@example>",
                        "recipient": "user2@example.com",
                        "recipient_role": "to",
                    },
                ],
            }

        saved: list[str] = []

        def original_save(client, msg):
            saved.append(str(msg.get("Message-ID") or ""))
            return True, None

        with (
            patch.object(
                PostmasterV960NewsletterMailClient,
                "_send_individualized",
                original_send,
            ),
            patch.object(archive, "_ORIGINAL_SAVE_SENT_COPY", original_save),
        ):
            archive._install_outbound_archive_boundary()
            client = _FakeClient()
            result = PostmasterV960NewsletterMailClient._send_individualized(
                client,
                to=["user1@example.com", "user2@example.com"],
                cc=[],
                bcc=[],
            )

        self.assertEqual(saved, ["<m1@example>"])
        self.assertEqual(result["sent_append_count"], 1)
        self.assertEqual(result["canonical_sent_message_id"], "<m1@example>")
        self.assertTrue(result["deliveries"][0]["canonical_sent_archive"])
        self.assertFalse(result["deliveries"][1]["canonical_sent_archive"])

    def test_archive_failure_does_not_claim_success(self):
        def original_send(client, *args, **kwargs):
            msg = EmailMessage()
            msg["Message-ID"] = "<m@example>"
            PostmasterV950MailClient._save_sent_copy(client, msg)
            return {
                "campaign_id": "cmp_failure",
                "deliveries": [
                    {
                        "delivery_id": "d1",
                        "message_id": "<m@example>",
                        "recipient": "a@example.com",
                        "recipient_role": "to",
                    }
                ],
            }

        with (
            patch.object(
                PostmasterV960NewsletterMailClient,
                "_send_individualized",
                original_send,
            ),
            patch.object(
                archive,
                "_ORIGINAL_SAVE_SENT_COPY",
                lambda _client, _msg: (False, "append failed"),
            ),
        ):
            archive._install_outbound_archive_boundary()
            client = _FakeClient()
            result = PostmasterV960NewsletterMailClient._send_individualized(
                client,
                to=["a@example.com"],
            )

        self.assertFalse(result["canonical_sent_copy_saved"])
        self.assertEqual(result["sent_append_count"], 0)
        self.assertFalse(result["deliveries"][0]["sent_copy_saved"])

    def test_sender_metadata_private_and_received_fallback_never_invents_bcc(self):
        self.store.record_operation(
            operation_id="out_web",
            account_id="acct",
            canonical_message_id="<sent@example>",
            canonical_sent_archived=True,
            to=["to@example.com"],
            cc=["cc@example.com"],
            bcc=["hidden@example.com"],
            deliveries=[],
        )
        sent = EmailMessage()
        sent["Message-ID"] = "<sent@example>"
        sent["To"] = "legacy-to@example.com"
        sent["Cc"] = "legacy-cc@example.com"
        with patch(
            "postmaster.webgui_v969.outbound_operation_store",
            return_value=self.store,
        ):
            meta = _sender_metadata("acct", sent.as_bytes())
        self.assertEqual(meta["to"], ["to@example.com"])
        self.assertEqual(meta["cc"], ["cc@example.com"])
        self.assertEqual(meta["bcc"], ["hidden@example.com"])

        received = EmailMessage()
        received["Message-ID"] = "<received@example>"
        received["To"] = "visible@example.com"
        received["Cc"] = "visible-cc@example.com"
        with patch(
            "postmaster.webgui_v969.outbound_operation_store",
            return_value=self.store,
        ):
            fallback = _sender_metadata("acct", received.as_bytes())
        self.assertEqual(fallback["cc"], ["visible-cc@example.com"])
        self.assertEqual(fallback["bcc"], [])


class WebGuiSentMetadataV969Tests(unittest.TestCase):
    def _request(self):
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [],
                "query_string": b"",
            }
        )

    def test_sent_detail_adds_private_to_cc_bcc_and_preserves_date(self):
        msg = EmailMessage()
        msg["Message-ID"] = "<canonical@example.test>"
        msg["To"] = "mime-to@example.test"
        msg["Cc"] = "mime-cc@example.test"
        raw = msg.as_bytes()

        fake_v963 = SimpleNamespace(
            confirm_full_html=lambda *a, **k: None,
            _detail=lambda *a, **k: (
                '<p class="small muted">To: old@example.test · '
                '2026-08-24 01:00:00+02:00</p><div>body</div>'
            ),
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
            rendered = fake_v963._detail(
                base,
                {},
                "acct",
                "Sent",
                "sent",
                "1",
                self._request(),
            )
        self.assertIn("To: to@example.test", rendered)
        self.assertIn("Cc: cc@example.test", rendered)
        self.assertIn("Bcc: bcc@example.test", rendered)
        self.assertIn("2026-08-24 01:00:00+02:00", rendered)

    def test_received_detail_is_not_enriched_with_bcc(self):
        fake_v963 = SimpleNamespace(
            confirm_full_html=lambda *a, **k: None,
            _detail=lambda *a, **k: (
                '<p class="small muted">From: sender@example.test · 2026-08-24</p>'
            ),
        )
        base = SimpleNamespace(mailbox_cache_store=lambda: SimpleNamespace())
        webgui_v969._install_webgui_v969(base, fake_v963, SimpleNamespace())
        rendered = fake_v963._detail(
            base,
            {},
            "acct",
            "INBOX",
            "received",
            "1",
            self._request(),
        )
        self.assertIn("From: sender@example.test", rendered)
        self.assertNotIn("Bcc:", rendered)


if __name__ == "__main__":
    unittest.main()
