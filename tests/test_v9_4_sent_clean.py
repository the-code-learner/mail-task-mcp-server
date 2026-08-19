from __future__ import annotations

import base64
import inspect
import os
import re
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.link_tracking import LinkTrackingStore
from postmaster.link_tracking_html import eligible_web_url
from postmaster.tracked_mail import LinkTrackingMailClient, _sent_clean_html, _synchronize_transport_headers
from postmaster.mail_bridge import MailClient, Settings

class CapturingV94MailClient(LinkTrackingMailClient):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.outbound: list[EmailMessage] = []
        self.sent: list[EmailMessage] = []

    def _validate_recipients(self, recipients):
        return [str(x).strip() for x in recipients if str(x).strip()]

    def _send_message_with_clean_sent(self, outbound, sent_copy, recipients):
        _synchronize_transport_headers(outbound, sent_copy, self.settings.email_address)
        self.outbound.append(outbound)
        self.sent.append(sent_copy)
        return {"sent": True,"from": self.settings.email_address,"to": recipients,"subject": str(outbound.get("Subject", "")),"message_id": str(outbound.get("Message-ID", "")),"sent_copy_saved": True,"sent_copy_error": None,"sent_copy_tracking_sanitized": True}


class SentVariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_public = {k: os.environ.get(k) for k in ("PUBLIC_EMAIL_BASE_URL", "PUBLIC_MCP_HOST")}
        os.environ["PUBLIC_EMAIL_BASE_URL"] = "https://postmaster.example.test"
        os.environ["PUBLIC_MCP_HOST"] = ""
        self.analytics = EmailAnalyticsStore(db_path=str(root / "analytics.db"), key_path=str(root / "analytics.key"))
        self.links = LinkTrackingStore(self.analytics)
        self.settings = Settings(email_address="sender@example.test", email_password="pw", enable_send=True, save_sent_copy=True, send_recipient_allowlist=("example.test",), allow_previous_sent_recipients=False, account_id="acct", smtp_username="sender@example.test", smtp_password="pw")
        self.client = CapturingV94MailClient(self.settings)

    def tearDown(self) -> None:
        for key, value in self.old_public.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        self.tmp.cleanup()

    @staticmethod
    def _part_text(msg: EmailMessage, content_type: str) -> str:
        for part in msg.walk():
            if part.get_content_type() == content_type:
                return str(part.get_content())
        return ""

    @staticmethod
    def _attachment_bytes(msg: EmailMessage) -> list[bytes]:
        return [part.get_payload(decode=True) or b"" for part in msg.walk() if part.get_content_disposition() == "attachment"]

    def test_pre_v94_sent_behavior_is_same_instrumented_message(self) -> None:
        source = inspect.getsource(MailClient._send_message)
        self.assertIn("smtp.send_message(msg", source)
        self.assertIn("msg.as_bytes(policy=policy.SMTP)", source)
        self.assertIn("conn.append", source)

    def test_recipient_is_tracked_but_sent_is_clean_with_headers_and_attachment_identity(self) -> None:
        payload = b"\x00v9.4-attachment-bytes\xff"
        body_html = '<html><body><a href="https://one.example/a?x=1&amp;y=2#frag">One</a><a href="https://two.example/project">Two</a></body></html>'
        attachments = [{"filename":"asset.bin","content_type":"application/octet-stream","content_base64":base64.b64encode(payload).decode("ascii")}]
        with patch("postmaster.tracked_mail.analytics_store", return_value=self.analytics), patch("postmaster.tracked_mail.link_store", return_value=self.links):
            result = self.client._send_individualized(to=["reader@example.test"], subject="Tracked test", body="Plain fallback", body_html=body_html, attachments=attachments, track_opens=True, in_reply_to="<parent@example.test>", references="<root@example.test> <parent@example.test>")
        self.assertTrue(result["sent"])
        self.assertTrue(result["sent_copy_tracking_sanitized"])
        outbound, sent = self.client.outbound[0], self.client.sent[0]
        outbound_html = self._part_text(outbound, "text/html")
        sent_html = self._part_text(sent, "text/html")
        self.assertIn("/track/open/", outbound_html)
        self.assertGreaterEqual(outbound_html.count("/t/c/"), 2)
        self.assertNotIn("/track/open/", sent_html)
        self.assertNotIn("/t/c/", sent_html)
        self.assertIn("https://one.example/a?x=1&amp;y=2#frag", sent_html)
        self.assertIn("https://two.example/project", sent_html)
        self.assertIn("One", sent_html)
        self.assertIn("Two", sent_html)
        for header in ("Message-ID","Date","Subject","In-Reply-To","References"):
            self.assertEqual(str(outbound.get(header, "")), str(sent.get(header, "")), header)
        self.assertEqual(self._attachment_bytes(outbound), [payload])
        self.assertEqual(self._attachment_bytes(sent), [payload])
        delivery_id = result["deliveries"][0]["delivery_id"]
        delivery = self.analytics.get_delivery(delivery_id)
        self.assertEqual(delivery["campaign_id"], result["campaign_id"])
        self.assertEqual(delivery["message_id"], str(outbound["Message-ID"]))
        links = self.links.list_links(delivery_id=delivery_id)
        self.assertEqual(len(links), 2)
        self.assertTrue(all(row["message_id"] == str(outbound["Message-ID"]) for row in links))
        sent_hrefs = re.findall(r'href="([^"]+)"', sent_html)
        self.assertTrue(sent_hrefs)
        self.assertTrue(all("/t/c/" not in href for href in sent_hrefs))
        self.assertTrue(all(eligible_web_url(href.replace("&amp;", "&")) for href in sent_hrefs))
        self.assertEqual(self.links.summary(delivery_id=delivery_id)["total_clicks"], 0)
        self.assertEqual(len(self.analytics.list_open_events(delivery_id=delivery_id)), 0)

    def test_old_messages_are_not_retroactively_mutated(self) -> None:
        legacy = b'<a href="https://postmaster.example.test/t/c/legacy">old</a>'
        before = bytes(legacy)
        _ = _sent_clean_html("<p>new</p>", {"recipient":"r@example.test","campaign_id":"c","id":"d"})
        self.assertEqual(legacy, before)
