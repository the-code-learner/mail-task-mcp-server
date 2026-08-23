from __future__ import annotations

import os
import socket
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from postmaster.email_analytics import AnalyticsError, EmailAnalyticsStore
from postmaster.link_tracking import LinkTrackingStore
from postmaster.mail_bridge import MailBridgeError, Settings
from postmaster.tracked_mail import LinkTrackingMailClient, _synchronize_transport_headers


class CapturingClient(LinkTrackingMailClient):
    def __init__(self, settings: Settings, source: EmailMessage | None = None):
        super().__init__(settings)
        self.source = source
        self.group_messages: list[EmailMessage] = []
        self.outbound_messages: list[EmailMessage] = []
        self.sent_messages: list[EmailMessage] = []
        self.draft_messages: list[EmailMessage] = []

    def _validate_recipients(self, recipients):
        cleaned = [str(value).strip() for value in recipients if str(value).strip()]
        if not cleaned:
            raise MailBridgeError("At least one recipient is required")
        return cleaned

    def _send_message(self, msg, recipients):
        self.group_messages.append(msg)
        return {
            "sent": True,
            "from": self.settings.email_address,
            "to": list(recipients),
            "subject": str(msg.get("Subject", "")),
            "message_id": "<group@example.test>",
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

    def _thread_source_message(self, mailbox: str, uid: str):
        if self.source is None:
            raise AssertionError("thread source not configured")
        return self.source

    def _save_draft(self, msg):
        self.draft_messages.append(msg)
        return {
            "draft_saved": True,
            "mailbox": "Drafts",
            "from": self.settings.email_address,
            "to": [],
            "cc": [],
            "bcc": [],
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


class OutboundDetrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_public = {
            key: os.environ.get(key)
            for key in ("PUBLIC_EMAIL_BASE_URL", "PUBLIC_MCP_HOST")
        }
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

    def tearDown(self) -> None:
        for key, value in self.old_public.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    @staticmethod
    def html_part(msg: EmailMessage) -> str:
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return str(part.get_content())
        return ""

    @staticmethod
    def source_message(*, outbound: bool) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = "sender@example.test" if outbound else "reader@example.net"
        msg["To"] = "reader@example.net" if outbound else "sender@example.test"
        msg["Subject"] = "Topic"
        msg["Message-ID"] = "<selected@example.test>"
        msg["References"] = "<root@example.test>"
        return msg

    def make_historical_html(self, destination: str = "https://destination.example/path?q=1") -> str:
        campaign = self.analytics.create_campaign(
            account_id="acct",
            sender="sender@example.test",
            subject="historical",
            track_opens=True,
            amp_used=False,
        )
        delivery = self.analytics.create_delivery(
            campaign_id=campaign["id"],
            account_id="acct",
            recipient="reader@example.test",
            recipient_role="to",
        )
        rendered, _ = self.analytics.render_for_recipient(
            body_html=f'<html><body><a href="{destination}">Open</a></body></html>',
            body_amp=None,
            delivery=delivery,
            track_opens=True,
        )
        tracked, meta = self.links.instrument_html(body_html=rendered, delivery=delivery)
        self.assertEqual(len(meta), 1)
        return tracked

    def test_normalization_resolves_link_and_removes_pixel_without_network_or_events(self) -> None:
        historical = self.make_historical_html()
        with patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
            clean = self.links.normalize_postmaster_html(historical)

        self.assertIn("https://destination.example/path?q=1", clean)
        self.assertNotIn("/t/c/", clean)
        self.assertNotIn("/track/open/", clean)
        with self.links._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tracking_clicks").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tracking_opens").fetchone()[0], 0)

    def test_nested_historical_tracking_chain_resolves_to_authoritative_original(self) -> None:
        historical = self.make_historical_html("https://destination.example/original")
        first_token = historical.split("/t/c/", 1)[1].split('"', 1)[0]
        first_url = f"https://postmaster.example.test/t/c/{first_token}"

        campaign = self.analytics.create_campaign(
            account_id="acct", sender="sender@example.test", subject="nested",
            track_opens=True, amp_used=False,
        )
        delivery = self.analytics.create_delivery(
            campaign_id=campaign["id"], account_id="acct",
            recipient="reader@example.test", recipient_role="to",
        )
        nested = self.links._insert_link(
            delivery=delivery,
            original_url=first_url,
            position=0,
            anchor_text="Open",
        )
        nested_html = (
            f'<a href="https://postmaster.example.test/t/c/{nested["tracking_token"]}">Open</a>'
        )
        clean = self.links.normalize_postmaster_html(nested_html)
        self.assertIn('href="https://destination.example/original"', clean)
        self.assertNotIn("/t/c/", clean)

    def test_unknown_current_postmaster_artifacts_fail_closed(self) -> None:
        with self.assertRaisesRegex(AnalyticsError, "Unknown Postmaster tracking-link token"):
            self.links.normalize_postmaster_html(
                '<a href="https://postmaster.example.test/t/c/missing-token">Broken</a>'
            )
        with self.assertRaisesRegex(AnalyticsError, "Unknown Postmaster open-pixel token"):
            self.links.normalize_postmaster_html(
                '<img src="https://postmaster.example.test/track/open/missing-token.gif" width="1">'
            )

    def test_third_party_tracking_like_paths_are_not_mistaken_for_postmaster(self) -> None:
        html = '<a href="https://third.example/t/c/not-postmaster">Third party</a>'
        clean = self.links.normalize_postmaster_html(html)
        self.assertEqual(clean, html)

    def test_untracked_send_and_draft_use_canonical_clean_html(self) -> None:
        historical = self.make_historical_html()
        client = CapturingClient(self.settings)
        with patch("postmaster.tracked_mail.link_store", return_value=self.links):
            result = client.send_email(
                to=["reader@example.net"], subject="Untracked", body="Plain",
                body_html=historical, track_opens=False,
            )
            draft = client.create_draft(
                to=["reader@example.net"], subject="Draft", body="Plain",
                body_html=historical,
            )

        self.assertTrue(result["sent"])
        self.assertTrue(draft["draft_saved"])
        for msg in (client.group_messages[-1], client.draft_messages[-1]):
            html = self.html_part(msg)
            self.assertIn("https://destination.example/path?q=1", html)
            self.assertNotIn("/t/c/", html)
            self.assertNotIn("/track/open/", html)

    def test_tracked_send_retracks_once_and_sent_copy_stays_clean_across_generations(self) -> None:
        historical = self.make_historical_html()
        client = CapturingClient(self.settings)
        with patch("postmaster.tracked_mail.analytics_store", return_value=self.analytics), patch(
            "postmaster.tracked_mail.link_store", return_value=self.links
        ):
            first = client.send_email(
                to=["reader@example.net"], subject="Generation one", body="Plain",
                body_html=historical, track_opens=True,
            )
            first_html = self.html_part(client.outbound_messages[-1])
            first_sent = self.html_part(client.sent_messages[-1])
            second = client.send_email(
                to=["reader@example.net"], subject="Generation two", body="Plain",
                body_html=first_html, track_opens=True,
            )

        second_html = self.html_part(client.outbound_messages[-1])
        second_sent = self.html_part(client.sent_messages[-1])
        self.assertTrue(first["sent"])
        self.assertTrue(second["sent"])
        self.assertEqual(first_html.count("/t/c/"), 1)
        self.assertEqual(first_html.count("/track/open/"), 1)
        self.assertEqual(second_html.count("/t/c/"), 1)
        self.assertEqual(second_html.count("/track/open/"), 1)
        self.assertNotIn("/t/c/", first_sent)
        self.assertNotIn("/track/open/", first_sent)
        self.assertNotIn("/t/c/", second_sent)
        self.assertNotIn("/track/open/", second_sent)
        self.assertIn("https://destination.example/path?q=1", second_sent)
        self.assertEqual(second["deliveries"][0]["links"][0]["original_url"], "https://destination.example/path?q=1")
        with self.links._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tracking_clicks").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tracking_opens").fetchone()[0], 0)

    def test_reply_and_follow_up_normalize_before_both_tracked_and_untracked_paths(self) -> None:
        historical = self.make_historical_html()
        client = CapturingClient(self.settings, self.source_message(outbound=False))
        with patch("postmaster.tracked_mail.analytics_store", return_value=self.analytics), patch(
            "postmaster.tracked_mail.link_store", return_value=self.links
        ):
            reply = client.reply_email(
                mailbox="INBOX", uid="1", body="Reply", body_html=historical,
                track_opens=True,
            )
            client.source = self.source_message(outbound=True)
            follow_up = client.follow_up_email(
                mailbox="Sent", uid="2", body="Follow-up", body_html=historical,
                track_opens=False,
            )

        self.assertTrue(reply["sent"])
        reply_html = self.html_part(client.outbound_messages[-1])
        self.assertEqual(reply_html.count("/t/c/"), 1)
        self.assertEqual(reply_html.count("/track/open/"), 1)
        follow_html = self.html_part(client.group_messages[-1])
        self.assertIn("https://destination.example/path?q=1", follow_html)
        self.assertNotIn("/t/c/", follow_html)
        self.assertNotIn("/track/open/", follow_html)

    def test_client_surfaces_unresolved_artifact_as_mail_error_before_send(self) -> None:
        client = CapturingClient(self.settings)
        with patch("postmaster.tracked_mail.link_store", return_value=self.links):
            with self.assertRaisesRegex(MailBridgeError, "unresolved Postmaster tracking artifact"):
                client.send_email(
                    to=["reader@example.net"], subject="Broken", body="Plain",
                    body_html='<a href="https://postmaster.example.test/t/c/missing">Broken</a>',
                    track_opens=False,
                )
        self.assertEqual(client.group_messages, [])
        self.assertEqual(client.outbound_messages, [])


if __name__ == "__main__":
    unittest.main()
