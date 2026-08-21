from __future__ import annotations

import asyncio
import os
import re
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from starlette.requests import Request

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.file_store import FileStore
from postmaster.link_tracking import LinkTrackingStore
from postmaster.mail_bridge import MailBridgeError, Settings
from postmaster.stored_file_delivery import (
    PostmasterV946MailClient,
    StoredFileLinkTrackingStore,
    public_tracking_target,
)
from postmaster.tracked_mail import _synchronize_transport_headers


def thread_source(*, outbound: bool) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "sender@example.test" if outbound else "external@example.net"
    msg["To"] = "external@example.net" if outbound else "sender@example.test"
    msg["Subject"] = "Tracked stored file"
    msg["Message-ID"] = "<source@example.test>"
    return msg


def html_part(msg: EmailMessage) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return str(part.get_content())
    return ""


class CaptureTrackedClient(PostmasterV946MailClient):
    def __init__(self, settings, *, file_store, analytics, tracking_store, source):
        super().__init__(
            settings,
            file_store=file_store,
            file_authorizer=lambda info: True,
            analytics=analytics,
            tracking_store=tracking_store,
        )
        self.source = source
        self.outbound: list[EmailMessage] = []
        self.sent_copy: list[EmailMessage] = []

    def _thread_source_message(self, mailbox: str, uid: str):
        return self.source

    def _validate_recipients(self, recipients):
        cleaned = [str(value).strip() for value in recipients if str(value).strip()]
        if not cleaned:
            raise MailBridgeError("At least one recipient is required")
        return cleaned

    def _send_message_with_clean_sent(self, outbound, sent_copy, recipients):
        _synchronize_transport_headers(outbound, sent_copy, self.settings.email_address)
        self.outbound.append(outbound)
        self.sent_copy.append(sent_copy)
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


class _Logger:
    def info(self, *args, **kwargs):
        return None


class TrackedStoredFileDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_public = os.environ.get("PUBLIC_EMAIL_BASE_URL")
        os.environ["PUBLIC_EMAIL_BASE_URL"] = "https://postmaster.example.test"
        self.analytics = EmailAnalyticsStore(
            db_path=str(root / "analytics.db"),
            key_path=str(root / "analytics.key"),
        )
        # First create the pre-v9.4.6 schema to exercise the additive migration.
        LinkTrackingStore(self.analytics)
        self.links = StoredFileLinkTrackingStore(self.analytics)
        self.files = FileStore(
            db_path=str(root / "files.db"),
            root=str(root / "files"),
            max_bytes=2 * 1024 * 1024,
            max_total_bytes=20 * 1024 * 1024,
        )
        self.first = self.files.save_bytes(
            owner_id="owner",
            project_id="project",
            filename="report.pdf",
            media_type="application/pdf",
            data=b"%PDF-stored-one",
        )
        self.second = self.files.save_bytes(
            owner_id="owner",
            project_id="project",
            filename="photo.jpg",
            media_type="image/jpeg",
            data=b"\xff\xd8stored-two\xff\xd9",
        )
        settings = Settings(
            email_address="sender@example.test",
            email_password="pw",
            enable_send=True,
            save_sent_copy=True,
            allow_previous_sent_recipients=False,
            account_id="acct",
        )
        self.client = CaptureTrackedClient(
            settings,
            file_store=self.files,
            analytics=self.analytics,
            tracking_store=self.links,
            source=thread_source(outbound=False),
        )

    def tearDown(self) -> None:
        if self.old_public is None:
            os.environ.pop("PUBLIC_EMAIL_BASE_URL", None)
        else:
            os.environ["PUBLIC_EMAIL_BASE_URL"] = self.old_public
        self.tmp.cleanup()

    @staticmethod
    def request(token: str, *, user_agent: str = "Mozilla/5.0") -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "https",
                "path": f"/t/c/{token}",
                "raw_path": f"/t/c/{token}".encode(),
                "query_string": b"",
                "headers": [(b"user-agent", user_agent.encode())],
                "client": ("203.0.113.4", 12345),
                "server": ("postmaster.example.test", 443),
                "path_params": {"token": token},
            }
        )

    def fetch(self, token: str, *, user_agent: str = "Mozilla/5.0"):
        return asyncio.run(
            public_tracking_target(
                self.request(token, user_agent=user_agent),
                tracking_store=self.links,
                file_store=self.files,
                logger=_Logger(),
            )
        )

    def share(self, file_id: str, *, label: str = "Download") -> tuple[str, dict]:
        result = self.client.send_email(
            to=["external@example.net"],
            subject="Shared file",
            body="Fallback",
            body_html=f'<html><body><a href="postmaster-file:{file_id}">{label}</a></body></html>',
            track_opens=False,
        )
        outbound = html_part(self.client.outbound[-1])
        match = re.search(r"/t/c/([A-Za-z0-9_-]+)", outbound)
        self.assertIsNotNone(match)
        return str(match.group(1)), result

    def test_schema_migration_is_idempotent_and_additive(self) -> None:
        again = StoredFileLinkTrackingStore(self.analytics)
        with again._connect() as conn:
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tracking_links)")}
        self.assertTrue(
            {
                "target_type",
                "stored_file_id",
                "download_filename",
                "download_media_type",
                "status",
                "expires_at",
                "revoked_at",
            }.issubset(columns)
        )

    def test_share_uses_opaque_tracking_url_and_never_exposes_file_id(self) -> None:
        token, result = self.share(self.first["id"])
        outbound = html_part(self.client.outbound[-1])
        sent = html_part(self.client.sent_copy[-1])
        self.assertNotIn(self.first["id"], outbound)
        self.assertNotIn(self.first["id"], sent)
        self.assertIn(f"/t/c/{token}", outbound)
        self.assertIn(f"/t/c/{token}", sent)
        self.assertNotIn("/files/", outbound)
        self.assertNotIn("?file_id=", outbound)
        self.assertNotIn("sig=", outbound)
        self.assertNotIn("exp=", outbound)
        self.assertFalse(result["tracked"])
        self.assertTrue(result["link_tracking"])
        self.assertTrue(result["stored_file_download_links"])
        safe_link = result["deliveries"][0]["links"][0]
        self.assertEqual(safe_link["target_type"], "stored_file")
        self.assertNotIn("tracking_token", safe_link)
        self.assertNotIn("stored_file_id", safe_link)

        internal = self.links.get_by_token(token)
        self.assertEqual(internal["stored_file_id"], self.first["id"])
        self.assertEqual(internal["target_type"], "stored_file")
        self.assertEqual(internal["campaign_id"], result["campaign_id"])
        self.assertEqual(internal["delivery_id"], result["deliveries"][0]["delivery_id"])

    def test_exact_token_downloads_exact_file_and_records_existing_event_pipeline(self) -> None:
        token, result = self.share(self.first["id"])
        response = self.fetch(token, user_agent="GoogleImageProxy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"%PDF-stored-one")
        self.assertTrue(response.headers["content-type"].startswith("application/pdf"))
        self.assertIn("report.pdf", response.headers["content-disposition"])
        events = self.links.list_click_events(delivery_id=result["deliveries"][0]["delivery_id"])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["campaign_id"], result["campaign_id"])
        self.assertEqual(event["delivery_id"], result["deliveries"][0]["delivery_id"])
        self.assertEqual(event["target_type"], "stored_file")
        self.assertEqual(event["download_filename"], "report.pdf")
        self.assertIn("provider_likelihood", event)
        self.assertIn("provider_classification", event)

    def test_tokens_are_non_enumerable_and_cannot_be_modified_to_select_another_file(self) -> None:
        first_token, _ = self.share(self.first["id"], label="First")
        second_token, _ = self.share(self.second["id"], label="Second")
        self.assertNotEqual(first_token, second_token)
        self.assertNotIn(self.first["id"], first_token)
        self.assertNotIn(self.second["id"], second_token)
        self.assertEqual(self.fetch(first_token).body, b"%PDF-stored-one")
        self.assertEqual(self.fetch(second_token).body, b"\xff\xd8stored-two\xff\xd9")
        mutated = first_token[:-1] + ("A" if first_token[-1:] != "A" else "B")
        missing = self.fetch(mutated)
        random_missing = self.fetch("totally-random-opaque-token")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(random_missing.status_code, 404)
        self.assertEqual(missing.body, random_missing.body)

    def test_revoked_expired_and_deleted_targets_have_uniform_public_failure(self) -> None:
        token, _ = self.share(self.first["id"])
        with self.links._connect() as conn:
            conn.execute("UPDATE tracking_links SET status='revoked',revoked_at=? WHERE tracking_token=?", ("2026-08-21T00:00:00+00:00", token))
        revoked = self.fetch(token)
        self.assertEqual(revoked.status_code, 404)

        token2, _ = self.share(self.second["id"])
        with self.links._connect() as conn:
            conn.execute("UPDATE tracking_links SET expires_at=? WHERE tracking_token=?", ("2000-01-01T00:00:00+00:00", token2))
        expired = self.fetch(token2)
        self.assertEqual(expired.status_code, 404)
        self.assertEqual(expired.body, revoked.body)

        third = self.files.save_bytes(
            owner_id="owner",
            project_id="project",
            filename="gone.txt",
            media_type="text/plain",
            data=b"gone",
        )
        token3, _ = self.share(third["id"])
        self.files.delete(third["id"])
        unavailable = self.fetch(token3)
        self.assertEqual(unavailable.status_code, 404)
        self.assertEqual(unavailable.body, revoked.body)

    def test_normal_web_redirect_tracking_remains_unchanged(self) -> None:
        campaign = self.analytics.create_campaign(
            account_id="acct",
            sender="sender@example.test",
            subject="Normal URL",
            track_opens=True,
            amp_used=False,
        )
        delivery = self.analytics.create_delivery(
            campaign_id=campaign["id"],
            account_id="acct",
            recipient="external@example.net",
            recipient_role="to",
        )
        rendered, meta, _ = self.links.instrument_html_with_shares(
            body_html='<a href="https://destination.example/path?q=1">Normal</a>',
            delivery=delivery,
            track_web_links=True,
        )
        token = re.search(r"/t/c/([A-Za-z0-9_-]+)", rendered).group(1)
        response = self.fetch(token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "https://destination.example/path?q=1")
        self.assertEqual(meta[0]["target_type"], "url")

    def test_reply_and_follow_up_share_links_work_without_enabling_open_tracking(self) -> None:
        html = f'<a href="postmaster-file:{self.first["id"]}">Download</a>'
        result = self.client.reply_email(
            mailbox="INBOX",
            uid="10",
            body="Reply",
            body_html=html,
            track_opens=False,
        )
        self.assertTrue(result["stored_file_download_links"])
        self.assertIn("/t/c/", html_part(self.client.outbound[-1]))
        self.assertNotIn("/track/open/", html_part(self.client.outbound[-1]))

        self.client.source = thread_source(outbound=True)
        result = self.client.follow_up_email(
            mailbox="Sent",
            uid="11",
            body="Follow-up",
            body_html=html,
            track_opens=False,
        )
        self.assertTrue(result["stored_file_download_links"])
        self.assertIn("/t/c/", html_part(self.client.outbound[-1]))
        self.assertNotIn("/track/open/", html_part(self.client.outbound[-1]))


if __name__ == "__main__":
    unittest.main()
