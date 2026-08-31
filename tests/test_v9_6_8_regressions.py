from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp.server import MCPServer

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.link_tracking import LinkTrackingStore
from postmaster.mail_bridge import MailBridgeError, Settings
from postmaster.mail_v960_unsubscribe import PostmasterV960NewsletterMailClient
from postmaster.runtime_v964 import install_runtime_v964
from postmaster.runtime_v968 import install_runtime_v968, _install_webgui_seen_boundary
from postmaster.stored_file_delivery import StoredFileLinkTrackingStore
from postmaster.tracked_mail import _synchronize_transport_headers
from postmaster import webgui_v963 as v963
from postmaster import webgui_v963_high_noise as v963_high_noise


class _Reliability:
    def blocked_recipients(self, recipients):
        return []


class _OutboundSafety:
    def execute(self, *, callback, **kwargs):
        return callback()


class _Unsubscribe:
    def placeholder_url(self):
        return "https://postmaster.example.test/unsubscribe/{{DELIVERY_ID}}"

    def url_for_delivery(self, delivery_id):
        return f"https://postmaster.example.test/unsubscribe/{delivery_id}"


class FinalComposedCapture(PostmasterV960NewsletterMailClient):
    """Exact final runtime mail-client class with transport/storage side effects captured locally."""

    def __init__(self, settings, *, analytics, tracking_store):
        # Deliberately avoid production constructors that allocate unrelated persistent stores.
        self.settings = settings
        self._v946_analytics = analytics
        self._v946_tracking_store = tracking_store
        self._stored_file_store = SimpleNamespace()
        self._stored_file_authorizer = None
        self.unsubscribe_manager = _Unsubscribe()
        self._suppression_store = _Reliability()
        self.reliability = _Reliability()
        self.outbound_safety = _OutboundSafety()
        self.outbound_messages: list[EmailMessage] = []
        self.sent_messages: list[EmailMessage] = []
        self.group_messages: list[EmailMessage] = []
        self.source: EmailMessage | None = None

    def _validate_recipients(self, recipients):
        cleaned = [str(value).strip() for value in recipients if str(value).strip()]
        if not cleaned:
            raise MailBridgeError("At least one recipient is required")
        return cleaned

    def _thread_source_message(self, mailbox: str, uid: str):
        if self.source is None:
            raise AssertionError("thread source not configured")
        return self.source

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


class _McpBase:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def mail_client(self, account_id=None):
        return self.client


class _McpCore:
    def __init__(self):
        self.mcp = MCPServer("v9.6.8 final outbound regression")
        for name in ("build_status", "send_email", "reply_email", "follow_up_email"):
            self.mcp.add_tool(lambda: None, name=name)


def _html_part(msg: EmailMessage) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return str(part.get_content())
    return ""


def _source_message(*, outbound: bool) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "sender@example.test" if outbound else "reader@example.net"
    msg["To"] = "reader@example.net" if outbound else "sender@example.test"
    msg["Subject"] = "Topic"
    msg["Message-ID"] = "<selected@example.test>"
    msg["References"] = "<root@example.test>"
    return msg


class FinalRuntimeOutboundDetrackingV968Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_public = os.environ.get("PUBLIC_EMAIL_BASE_URL")
        os.environ["PUBLIC_EMAIL_BASE_URL"] = "https://postmaster.example.test"
        self.analytics = EmailAnalyticsStore(
            db_path=str(root / "analytics.db"),
            key_path=str(root / "analytics.key"),
        )
        # Exercise the same additive schema that the final v9.4.6+ client uses.
        LinkTrackingStore(self.analytics)
        self.links = StoredFileLinkTrackingStore(self.analytics)
        self.settings = Settings(
            email_address="sender@example.test",
            email_password="pw",
            enable_send=True,
            save_sent_copy=True,
            allow_previous_sent_recipients=False,
            account_id="acct",
            smtp_username="sender@example.test",
            smtp_password="pw",
        )
        self.client = FinalComposedCapture(
            self.settings,
            analytics=self.analytics,
            tracking_store=self.links,
        )
        # The release installer is what makes the lower individualized boundary mandatory.
        install_runtime_v968(_McpBase(self.client), _McpCore())

    def tearDown(self):
        if self.old_public is None:
            os.environ.pop("PUBLIC_EMAIL_BASE_URL", None)
        else:
            os.environ["PUBLIC_EMAIL_BASE_URL"] = self.old_public
        self.tmp.cleanup()

    def historical_html(self, destination: str = "https://destination.example/original"):
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
            recipient="reader@example.net",
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
        self.assertIn("/t/c/", tracked)
        self.assertIn("/track/open/", tracked)
        return tracked

    def test_final_mcp_newsletter_path_canonicalizes_before_untracked_delivery_and_sent_copy(self):
        historical = self.historical_html()
        base = _McpBase(self.client)
        core = _McpCore()
        install_runtime_v964(base, core, lambda: {"ok": True, "version": "9.6.7", "build": "v9.6.7"})

        with patch("postmaster.tracked_mail.link_store", return_value=self.links):
            result = base.send_email(
                to=["reader@example.net"],
                subject="Final composed path",
                body="Plain",
                body_html=historical,
                track_opens=False,
                newsletter_mode=True,
                automatic_unsubscribe=True,
                account_id="acct",
            )

        self.assertTrue(result["sent"])
        recipient_html = _html_part(self.client.outbound_messages[-1])
        sent_html = _html_part(self.client.sent_messages[-1])
        for value in (recipient_html, sent_html):
            self.assertIn("https://destination.example/original", value)
            self.assertNotIn("/t/c/", value)
            self.assertNotIn("/track/open/", value)

    def test_final_tracked_path_retracks_once_and_repeated_generation_does_not_nest(self):
        historical = self.historical_html()
        with patch("postmaster.tracked_mail.link_store", return_value=self.links):
            first = self.client.send_email(
                to=["reader@example.net"], subject="Generation one", body="Plain",
                body_html=historical, track_opens=True,
            )
            first_html = _html_part(self.client.outbound_messages[-1])
            second = self.client.send_email(
                to=["reader@example.net"], subject="Generation two", body="Plain",
                body_html=first_html, track_opens=True,
            )
        second_html = _html_part(self.client.outbound_messages[-1])
        second_sent = _html_part(self.client.sent_messages[-1])
        self.assertTrue(first["sent"] and second["sent"])
        self.assertEqual(first_html.count("/t/c/"), 1)
        self.assertEqual(first_html.count("/track/open/"), 1)
        self.assertEqual(second_html.count("/t/c/"), 1)
        self.assertEqual(second_html.count("/track/open/"), 1)
        self.assertIn("https://destination.example/original", second_sent)
        self.assertNotIn("/t/c/", second_sent)
        self.assertNotIn("/track/open/", second_sent)

    def test_final_reply_and_follow_up_use_same_lower_boundary(self):
        historical = self.historical_html()
        self.client.source = _source_message(outbound=False)
        with patch("postmaster.tracked_mail.link_store", return_value=self.links):
            reply = self.client.reply_email(
                mailbox="INBOX", uid="1", body="Reply", body_html=historical,
                track_opens=True,
            )
            self.client.source = _source_message(outbound=True)
            follow = self.client.follow_up_email(
                mailbox="Sent", uid="2", body="Follow-up", body_html=historical,
                track_opens=False,
            )
        self.assertTrue(reply["sent"] and follow["sent"])
        reply_html = _html_part(self.client.outbound_messages[-1])
        self.assertEqual(reply_html.count("/t/c/"), 1)
        self.assertEqual(reply_html.count("/track/open/"), 1)
        follow_html = _html_part(self.client.group_messages[-1])
        self.assertIn("https://destination.example/original", follow_html)
        self.assertNotIn("/t/c/", follow_html)
        self.assertNotIn("/track/open/", follow_html)

    def test_unresolved_active_origin_token_fails_closed_before_transport(self):
        before = len(self.client.outbound_messages) + len(self.client.group_messages)
        with patch("postmaster.tracked_mail.link_store", return_value=self.links):
            with self.assertRaisesRegex(MailBridgeError, "unresolved Postmaster tracking artifact"):
                self.client.send_email(
                    to=["reader@example.net"], subject="Broken", body="Plain",
                    body_html='<a href="https://postmaster.example.test/t/c/missing">Broken</a>',
                    track_opens=False,
                )
        self.assertEqual(len(self.client.outbound_messages) + len(self.client.group_messages), before)

    def test_final_client_is_the_runtime_v972_factory_class(self):
        from postmaster import runtime_v960

        source = inspect.getsource(runtime_v960.install_runtime_v960)
        self.assertIn("PostmasterV972MailClient", source)
        self.assertIsInstance(self.client, PostmasterV960NewsletterMailClient)


class _ProxyStore:
    def status(self):
        return {
            "configured": True,
            "enabled": True,
            "tracking_obfuscation": True,
            "high_noise_decoy_enabled": True,
        }


class _Sync:
    def ensure_body(self, client, *, account_id, mailbox, uid):
        return {
            "body_html": '<img src="https://img.example/a.png">',
            "body": "",
            "seen": False,
        }


class _WebBase:
    def __init__(self):
        self.client = SimpleNamespace()

    async def _verified_form(self, request):
        return {"account_id": "acct", "mailbox": "INBOX", "uid": "7"}, None

    def privacy_proxy_store(self):
        return _ProxyStore()

    def privacy_proxy_client(self):
        return SimpleNamespace()

    def mailbox_cache_synchronizer(self):
        return _Sync()

    def mailbox_cache_store(self):
        return SimpleNamespace()

    def mail_client(self, account_id):
        return self.client


class WebGuiPrivacyWiringV968Tests(unittest.TestCase):
    def test_final_high_noise_confirmation_does_not_claim_success_when_all_render_fetches_fail(self):
        original_confirm = v963.confirm_full_html
        original_detail = v963._detail
        original_renderer = v963.render_inbox_v963
        try:
            v963_high_noise.install_webgui_v963_high_noise(v963)
            install_runtime_v968(SimpleNamespace(), SimpleNamespace(), v963)
            inventory = {
                "urls": [{
                    "url": "https://img.example/a.png",
                    "source_type": "img src",
                    "source_snippet": '<img src="https://img.example/a.png">',
                    "passive_resource": True,
                    "classification": "remote image",
                    "tracking_score": 5,
                }]
            }
            failed = {
                "https://img.example/a.png": {
                    "http_status": None,
                    "body": None,
                    "error_state": "RuntimeError: sensitive internal detail must not leak",
                }
            }
            with patch.object(v963, "inventory_message", return_value=inventory), patch.object(
                v963, "fetch_passive_resources", return_value=failed
            ), patch(
                "postmaster.runtime_v968.fetch_high_noise_decoys", return_value={"requests": 1}
            ):
                response = asyncio.run(v963.confirm_full_html(_WebBase(), object()))
            location = response.headers["location"]
            self.assertNotIn("full_html=1", location)
            self.assertIn("failure", location.casefold())
            self.assertNotIn("sensitive", location.casefold())
            self.assertNotIn("internal+detail", location.casefold())
        finally:
            v963.confirm_full_html = original_confirm
            v963._detail = original_detail
            v963.render_inbox_v963 = original_renderer

    def test_partial_success_renders_cached_subset_and_reports_safe_state(self):
        original_confirm = v963.confirm_full_html
        original_detail = v963._detail
        original_renderer = v963.render_inbox_v963
        try:
            install_runtime_v968(SimpleNamespace(), SimpleNamespace(), v963)
            inventory = {"urls": []}
            results = {
                "https://img.example/ok.png": {"http_status": 200, "body": b"png", "error_state": ""},
                "https://img.example/fail.png": {"http_status": None, "body": None, "error_state": "hidden"},
            }
            with patch.object(v963, "inventory_message", return_value=inventory), patch.object(
                v963, "fetch_passive_resources", return_value=results
            ), patch(
                "postmaster.runtime_v968.fetch_high_noise_decoys", return_value={"requests": 0}
            ):
                response = asyncio.run(v963.confirm_full_html(_WebBase(), object()))
            location = response.headers["location"]
            self.assertIn("full_html=1", location)
            self.assertIn("partial+success", location)
            self.assertNotIn("hidden", location)
        finally:
            v963.confirm_full_html = original_confirm
            v963._detail = original_detail
            v963.render_inbox_v963 = original_renderer


class SeenBoundaryV968Tests(unittest.TestCase):
    def _fixture(self, *, detail_raises: bool = False):
        calls: list[tuple] = []

        def detail(base, params, account_id, mailbox, role, uid, request):
            if detail_raises:
                raise RuntimeError("detail failed")
            return '<div class="detail">ok</div>'

        def renderer(base, request):
            return (
                '<tr class="v963-mail-row unread" '
                'data-v960-href="/?ui_view=inbox&amp;message_uid=7">'
                '<td><span class="v963-unread-dot" title="Unread"></span></td><td>message</td></tr>'
            )

        fake = SimpleNamespace(
            _detail=detail,
            render_inbox_v963=renderer,
        )
        _install_webgui_seen_boundary(fake)

        class Store:
            seen = False

            def get_message(self, account_id, mailbox, uid, include_body=False):
                return {"seen": self.seen, "flags": []}

            def update_flags(self, account_id, mailbox, values):
                calls.append(("update_flags", account_id, mailbox, values))
                self.seen = True

        store = Store()

        class Client:
            def set_seen(self, mailbox, uid, seen):
                calls.append(("set_seen", mailbox, uid, seen))
                return {"ok": True}

        base = SimpleNamespace(
            mailbox_cache_store=lambda: store,
            mail_client=lambda account_id: Client(),
        )
        return fake, base, store, calls

    def test_successful_received_detail_sets_imap_and_cache_seen(self):
        fake, base, store, calls = self._fixture()
        result = fake._detail(
            base, {}, "acct", "INBOX", "received", "7", SimpleNamespace(query_params={})
        )
        self.assertIn("ok", result)
        self.assertTrue(store.seen)
        self.assertEqual(calls[0], ("set_seen", "INBOX", "7", True))
        self.assertEqual(calls[1][0], "update_flags")
        self.assertEqual(calls[1][3], {7: [r"\Seen"]})

    def test_failed_detail_does_not_mark_seen(self):
        fake, base, store, calls = self._fixture(detail_raises=True)
        with self.assertRaisesRegex(RuntimeError, "detail failed"):
            fake._detail(
                base, {}, "acct", "INBOX", "received", "7", SimpleNamespace(query_params={})
            )
        self.assertFalse(store.seen)
        self.assertEqual(calls, [])

    def test_same_response_clears_selected_unread_row_after_success(self):
        fake, base, store, calls = self._fixture()
        fake._detail(base, {}, "acct", "INBOX", "received", "7", SimpleNamespace(query_params={}))
        html = fake.render_inbox_v963(
            base,
            SimpleNamespace(query_params={"account_id": "acct", "mailbox": "INBOX", "message_uid": "7"}),
        )
        self.assertNotIn("v963-mail-row unread", html)
        self.assertNotIn("v963-unread-dot", html)
        self.assertIn("v963-mail-row", html)


if __name__ == "__main__":
    unittest.main()
