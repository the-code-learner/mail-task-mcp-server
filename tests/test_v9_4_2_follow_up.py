from __future__ import annotations

import base64
import os
import tempfile
import unittest
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from unittest.mock import patch

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.link_tracking import LinkTrackingStore
from postmaster.mail_bridge import MailBridgeError, Settings
from postmaster.thread_recipients import resolve_thread_recipients, sender_identity_addresses
from postmaster.tracked_mail import LinkTrackingMailClient, _synchronize_transport_headers


def source_message(
    *,
    sender: str,
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    subject: str = "Topic",
    message_id: str = "<selected@example.test>",
    references: str = "<root@example.test>",
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if references:
        msg["References"] = references
    return msg


class CapturingThreadClient(LinkTrackingMailClient):
    def __init__(self, settings: Settings, source: EmailMessage):
        super().__init__(settings)
        self.source = source
        self.group_messages: list[EmailMessage] = []
        self.group_recipients: list[list[str]] = []
        self.outbound_messages: list[EmailMessage] = []
        self.sent_messages: list[EmailMessage] = []
        self.draft_messages: list[EmailMessage] = []
        self.validation_calls: list[list[str]] = []

    def _thread_source_message(self, mailbox: str, uid: str):
        return self.source

    def _validate_recipients(self, recipients):
        cleaned = [str(value).strip() for value in recipients if str(value).strip()]
        self.validation_calls.append(cleaned)
        if not cleaned:
            raise MailBridgeError("At least one recipient is required")
        return cleaned

    def _send_message(self, msg, recipients):
        self.group_messages.append(msg)
        self.group_recipients.append(list(recipients))
        return {
            "sent": True,
            "from": self.settings.email_address,
            "to": list(recipients),
            "subject": str(msg.get("Subject", "")),
            "message_id": "<new@example.test>",
            "sent_copy_saved": True,
            "sent_copy_error": None,
        }

    def _send_message_with_clean_sent(self, outbound, sent_copy, recipients):
        _synchronize_transport_headers(outbound, sent_copy, self.settings.email_address)
        self.outbound_messages.append(outbound)
        self.sent_messages.append(sent_copy)
        return {
            "sent": True,
            "from": self.settings.email_address,
            "to": list(recipients),
            "subject": str(outbound.get("Subject", "")),
            "message_id": str(outbound.get("Message-ID", "")),
            "sent_copy_saved": True,
            "sent_copy_error": None,
            "sent_copy_tracking_sanitized": True,
        }

    def _save_draft(self, msg):
        self.draft_messages.append(msg)
        return {
            "draft_saved": True,
            "mailbox": "Drafts",
            "from": self.settings.email_address,
            "to": [a for _, a in getaddresses([msg.get("To", "")]) if a],
            "cc": [a for _, a in getaddresses([msg.get("Cc", "")]) if a],
            "bcc": [a for _, a in getaddresses([msg.get("Bcc", "")]) if a],
            "subject": str(msg.get("Subject", "")),
            "message_id": "<draft@example.test>",
        }

    def recipient_authorization_status(self, recipients):
        return {
            "ok": True,
            "results": [
                {"address": value, "authorized_for_automated_send": True}
                for value in recipients
            ],
        }


class FollowUpRecipientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            email_address="sender@example.test",
            email_password="pw",
            enable_send=True,
            save_sent_copy=True,
            allow_previous_sent_recipients=False,
            account_id="acct",
            smtp_username="alias@example.test",
            smtp_password="pw",
        )

    def client(self, source: EmailMessage) -> CapturingThreadClient:
        return CapturingThreadClient(self.settings, source)

    def test_inbound_reply_prefers_reply_to_and_falls_back_to_from(self) -> None:
        with_reply_to = source_message(
            sender="from@example.net",
            to=["sender@example.test"],
            reply_to="reply@example.net",
        )
        client = self.client(with_reply_to)
        result = client.reply_email(mailbox="INBOX", uid="1", body="Reply", track_opens=False)
        self.assertEqual(result["resolved_to"], ["reply@example.net"])
        self.assertEqual(str(client.group_messages[0]["To"]), "reply@example.net")

        fallback = resolve_thread_recipients(
            source_message(sender="from@example.net", to=["sender@example.test"]),
            mode="reply",
            sender_identities=sender_identity_addresses(self.settings),
        )
        self.assertEqual(fallback["to"], ["from@example.net"])

    def test_follow_up_uses_original_to_and_preserves_original_cc(self) -> None:
        client = self.client(source_message(
            sender="sender@example.test",
            to=["one@example.net", "two@example.net"],
            cc=["copy@example.net"],
        ))
        result = client.follow_up_email(mailbox="Sent", uid="2", body="Following up", track_opens=False)
        self.assertEqual(result["resolved_to"], ["one@example.net", "two@example.net"])
        self.assertEqual(result["resolved_cc"], ["copy@example.net"])
        self.assertEqual(str(client.group_messages[0]["To"]), "one@example.net, two@example.net")
        self.assertEqual(str(client.group_messages[0]["Cc"]), "copy@example.net")

    def test_sender_primary_and_alias_removed_and_addresses_deduped_case_insensitive(self) -> None:
        client = self.client(source_message(
            sender="sender@example.test",
            to=[
                "sender@example.test",
                "A@example.net",
                "a@EXAMPLE.NET",
                "alias@example.test",
            ],
            cc=[
                "B@example.net",
                "A@EXAMPLE.NET",
                "ALIAS@example.test",
                "b@EXAMPLE.NET",
            ],
        ))
        result = client.follow_up_email(mailbox="Sent", uid="3", body="Follow-up", track_opens=False)
        self.assertEqual(result["resolved_to"], ["A@example.net"])
        self.assertEqual(result["resolved_cc"], ["B@example.net"])

    def test_original_bcc_is_never_rediscovered_or_exposed(self) -> None:
        client = self.client(source_message(
            sender="sender@example.test",
            to=["one@example.net"],
            cc=["copy@example.net"],
            bcc=["secret@example.net"],
        ))
        client.follow_up_email(mailbox="Sent", uid="4", body="Follow-up", track_opens=False)
        outgoing = client.group_messages[0]
        self.assertIsNone(outgoing.get("Bcc"))
        self.assertNotIn("secret@example.net", client.group_recipients[0])

    def test_zero_external_recipients_errors_without_send(self) -> None:
        client = self.client(source_message(
            sender="sender@example.test",
            to=["sender@example.test", "alias@example.test"],
            cc=["ALIAS@example.test"],
        ))
        with self.assertRaisesRegex(MailBridgeError, "No external recipients"):
            client.follow_up_email(mailbox="Sent", uid="5", body="Nope", track_opens=False)
        self.assertEqual(client.group_messages, [])
        self.assertEqual(client.outbound_messages, [])

    def test_reply_on_outbound_errors_use_follow_up_without_send(self) -> None:
        client = self.client(source_message(
            sender="sender@example.test",
            to=["external@example.net"],
        ))
        with self.assertRaisesRegex(MailBridgeError, "use follow_up_email"):
            client.reply_email(mailbox="Sent", uid="6", body="Nope", track_opens=False)
        self.assertEqual(client.group_messages, [])
        self.assertEqual(client.outbound_messages, [])

    def test_follow_up_on_inbound_is_rejected(self) -> None:
        client = self.client(source_message(
            sender="external@example.net",
            to=["sender@example.test"],
        ))
        with self.assertRaisesRegex(MailBridgeError, "use reply_email"):
            client.follow_up_email(mailbox="INBOX", uid="7", body="Nope", track_opens=False)
        self.assertEqual(client.group_messages, [])

    def test_thread_headers_and_subject_are_normalized(self) -> None:
        client = self.client(source_message(
            sender="sender@example.test",
            to=["external@example.net"],
            subject=" RE: re: Launch plan",
            message_id="<selected@example.test>",
            references="<root@example.test> <prior@example.test>",
        ))
        result = client.follow_up_email(mailbox="Sent", uid="8", body="Ping", track_opens=False)
        msg = client.group_messages[0]
        self.assertEqual(str(msg["Subject"]), "Re: Launch plan")
        self.assertEqual(str(msg["In-Reply-To"]), "<selected@example.test>")
        self.assertEqual(
            str(msg["References"]),
            "<root@example.test> <prior@example.test> <selected@example.test>",
        )
        self.assertEqual(result["in_reply_to"], "<selected@example.test>")

    def test_authorization_receives_only_resolved_external_recipients(self) -> None:
        client = self.client(source_message(
            sender="sender@example.test",
            to=["sender@example.test", "one@example.net", "alias@example.test"],
            cc=["two@example.net", "sender@example.test"],
        ))
        client.follow_up_email(mailbox="Sent", uid="9", body="Authorized", track_opens=False)
        flattened = [value.casefold() for call in client.validation_calls for value in call]
        self.assertIn("one@example.net", flattened)
        self.assertIn("two@example.net", flattened)
        self.assertNotIn("sender@example.test", flattened)
        self.assertNotIn("alias@example.test", flattened)

    def test_create_follow_up_draft_is_addressed_and_threaded_without_send(self) -> None:
        client = self.client(source_message(
            sender="sender@example.test",
            to=["one@example.net"],
            cc=["copy@example.net"],
            bcc=["old-secret@example.net"],
            subject="Re: Topic",
            message_id="<selected@example.test>",
            references="<root@example.test>",
        ))
        result = client.create_follow_up_draft(mailbox="Sent", uid="10", body="Draft body")
        self.assertTrue(result["draft_saved"])
        self.assertEqual(result["to"], ["one@example.net"])
        self.assertEqual(result["cc"], ["copy@example.net"])
        self.assertEqual(result["bcc"], [])
        draft = client.draft_messages[0]
        self.assertEqual(str(draft["In-Reply-To"]), "<selected@example.test>")
        self.assertEqual(str(draft["References"]), "<root@example.test> <selected@example.test>")
        self.assertEqual(client.group_messages, [])
        self.assertEqual(client.outbound_messages, [])


class FollowUpTrackedPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_public = {key: os.environ.get(key) for key in ("PUBLIC_EMAIL_BASE_URL", "PUBLIC_MCP_HOST")}
        os.environ["PUBLIC_EMAIL_BASE_URL"] = "https://postmaster.example.test"
        os.environ["PUBLIC_MCP_HOST"] = ""
        self.analytics = EmailAnalyticsStore(
            db_path=str(root / "analytics.db"),
            key_path=str(root / "analytics.key"),
        )
        self.links = LinkTrackingStore(self.analytics)
        self.settings = Settings(
            email_address="sender@example.test",
            email_password="pw",
            enable_send=True,
            save_sent_copy=True,
            allow_previous_sent_recipients=False,
            account_id="acct",
            smtp_username="alias@example.test",
            smtp_password="pw",
        )
        self.client = CapturingThreadClient(
            self.settings,
            source_message(
                sender="sender@example.test",
                to=["one@example.net", "two@example.net", "alias@example.test"],
                cc=["copy@example.net", "sender@example.test"],
                bcc=["old-secret@example.net"],
                subject="Topic",
                message_id="<selected@example.test>",
                references="<root@example.test>",
            ),
        )

    def tearDown(self) -> None:
        for key, value in self.old_public.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    @staticmethod
    def part_text(msg: EmailMessage, content_type: str) -> str:
        for part in msg.walk():
            if part.get_content_type() == content_type:
                return str(part.get_content())
        return ""

    @staticmethod
    def attachment_bytes(msg: EmailMessage) -> list[bytes]:
        return [
            part.get_payload(decode=True) or b""
            for part in msg.walk()
            if part.get_content_disposition() == "attachment"
        ]

    def test_tracked_follow_up_preserves_visible_to_cc_and_sent_clean_attachment_bytes(self) -> None:
        payload = b"\x00follow-up-attachment\xff"
        attachments = [{
            "filename": "asset.bin",
            "content_type": "application/octet-stream",
            "content_base64": base64.b64encode(payload).decode("ascii"),
        }]
        html = '<html><body><a href="https://destination.example/path?q=1">Open link</a></body></html>'
        with patch("postmaster.tracked_mail.analytics_store", return_value=self.analytics), patch(
            "postmaster.tracked_mail.link_store", return_value=self.links
        ):
            result = self.client.follow_up_email(
                mailbox="Sent",
                uid="11",
                body="Plain fallback",
                body_html=html,
                attachments=attachments,
                track_opens=True,
            )

        self.assertTrue(result["sent"])
        self.assertTrue(result["sent_copy_tracking_sanitized"])
        self.assertEqual(result["resolved_to"], ["one@example.net", "two@example.net"])
        self.assertEqual(result["resolved_cc"], ["copy@example.net"])
        self.assertEqual(len(self.client.outbound_messages), 3)
        self.assertEqual(len(self.client.sent_messages), 3)

        for outbound, sent in zip(self.client.outbound_messages, self.client.sent_messages):
            self.assertEqual(str(outbound["To"]), "one@example.net, two@example.net")
            self.assertEqual(str(outbound["Cc"]), "copy@example.net")
            self.assertEqual(str(sent["To"]), "one@example.net, two@example.net")
            self.assertEqual(str(sent["Cc"]), "copy@example.net")
            self.assertIsNone(outbound.get("Bcc"))
            self.assertIsNone(sent.get("Bcc"))
            self.assertEqual(str(outbound["In-Reply-To"]), "<selected@example.test>")
            self.assertEqual(str(sent["In-Reply-To"]), "<selected@example.test>")
            outbound_html = self.part_text(outbound, "text/html")
            sent_html = self.part_text(sent, "text/html")
            self.assertIn("/track/open/", outbound_html)
            self.assertIn("/t/c/", outbound_html)
            self.assertNotIn("/track/open/", sent_html)
            self.assertNotIn("/t/c/", sent_html)
            self.assertIn("https://destination.example/path?q=1", sent_html)
            self.assertEqual(self.attachment_bytes(outbound), [payload])
            self.assertEqual(self.attachment_bytes(sent), [payload])

        recipients = {row["recipient"] for row in result["deliveries"]}
        self.assertEqual(recipients, {"one@example.net", "two@example.net", "copy@example.net"})
        self.assertNotIn("sender@example.test", recipients)
        self.assertNotIn("alias@example.test", recipients)
        self.assertNotIn("old-secret@example.net", recipients)


if __name__ == "__main__":
    unittest.main()
