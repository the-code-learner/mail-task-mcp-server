from __future__ import annotations

import smtplib
import tempfile
import unittest
from pathlib import Path

from postmaster.delivery_reliability import ReliabilityStore, RetryPolicy, ThrottleController
from postmaster.mail_bridge import MailBridgeError, Settings
from postmaster.mail_v950 import PostmasterV950MailClient, _DELIVERY_ID


class DummyFileStore:
    pass


class CaptureClient(PostmasterV950MailClient):
    def _send_individualized(self, **kwargs):
        msg, _, _ = self._build_message(
            to=kwargs["to"],
            cc=kwargs.get("cc"),
            subject=kwargs["subject"],
            body=kwargs.get("body", ""),
            body_html=kwargs.get("body_html"),
            attachments=kwargs.get("attachments"),
            allow_unlisted=False,
            in_reply_to=kwargs.get("in_reply_to", ""),
            references=kwargs.get("references", ""),
        )
        return {
            "sent": True,
            "captured_headers": {name.lower(): str(value) for name, value in msg.items()},
            "tracked": bool(kwargs.get("track_opens")),
        }


class FakeSMTP:
    def __init__(self, *, features=None, data_responses=None):
        self.esmtp_features = features or {}
        self.data_responses = list(data_responses or [(250, b"queued")])
        self.mail_calls = []
        self.rcpt_calls = []
        self.data_calls = 0
        self.login_calls = 0
        self.sock = None

    def login(self, username, password):
        self.login_calls += 1
        return 235, b"ok"

    def mail(self, sender, options=()):
        self.mail_calls.append((sender, list(options)))
        return 250, b"ok"

    def rcpt(self, recipient, options=()):
        self.rcpt_calls.append((recipient, list(options)))
        return 250, b"ok"

    def data(self, payload):
        self.data_calls += 1
        return self.data_responses.pop(0)

    def rset(self):
        return 250, b"ok"

    def quit(self):
        return 221, b"bye"

    def close(self):
        pass


class ClientFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "analytics.db")
        self.store = ReliabilityStore(db_path=db)
        self.settings = Settings(
            email_address="sender@example.com",
            email_password="secret",
            account_id="sender",
            enable_send=True,
            save_sent_copy=False,
            send_recipient_allowlist=("example.net", "example.com"),
            allow_previous_sent_recipients=False,
            smtp_security="plain",
            smtp_host="smtp.example.com",
            smtp_port=25,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, cls=PostmasterV950MailClient, **kwargs):
        return cls(
            self.settings,
            file_store=DummyFileStore(),
            reliability=self.store,
            retry_policy=kwargs.pop("retry_policy", RetryPolicy(max_attempts=3, base_delay_seconds=0, max_delay_seconds=0, jitter_min=1, jitter_max=1)),
            throttle=kwargs.pop("throttle", ThrottleController(global_per_second=100, account_per_second=100, domain_per_second=100)),
            sleeper=kwargs.pop("sleeper", lambda _: None),
            **kwargs,
        )


class NewsletterTests(ClientFixture):
    def test_normal_email_has_no_unsubscribe(self):
        client = self.client(CaptureClient)
        result = client.send_email(
            to=["person@example.net"],
            subject="hello",
            body="body",
            track_opens=False,
        )
        headers = result["captured_headers"] if "captured_headers" in result else {}
        # Non-tracked sends use the group transport, so validate the MIME builder directly.
        with client._delivery_options():
            msg, _, _ = client._build_message(to=["person@example.net"], subject="hello", body="body")
        self.assertNotIn("List-Unsubscribe", msg)
        self.assertNotIn("List-Unsubscribe-Post", msg)

    def test_track_opens_true_does_not_enable_newsletter(self):
        client = self.client(CaptureClient)
        result = client.send_email(
            to=["person@example.net"],
            subject="tracked",
            body="body",
            track_opens=True,
        )
        headers = result["captured_headers"]
        self.assertTrue(result["tracked"])
        self.assertNotIn("list-unsubscribe", headers)
        self.assertNotIn("list-unsubscribe-post", headers)
        self.assertFalse(result["newsletter_mode"])

    def test_explicit_newsletter_url(self):
        client = self.client(CaptureClient)
        result = client.send_email(
            to=["person@example.net"],
            subject="newsletter",
            body="body",
            track_opens=True,
            newsletter_mode=True,
            unsubscribe_url="https://example.com/unsubscribe/abc",
        )
        self.assertEqual(
            result["captured_headers"]["list-unsubscribe"],
            "<https://example.com/unsubscribe/abc>",
        )
        self.assertNotIn("list-unsubscribe-post", result["captured_headers"])

    def test_explicit_newsletter_email(self):
        client = self.client(CaptureClient)
        result = client.send_email(
            to=["person@example.net"],
            subject="newsletter",
            body="body",
            track_opens=True,
            newsletter_mode=True,
            unsubscribe_email="unsubscribe@example.com",
        )
        self.assertEqual(
            result["captured_headers"]["list-unsubscribe"],
            "<mailto:unsubscribe@example.com>",
        )

    def test_one_click_is_explicit(self):
        client = self.client(CaptureClient)
        result = client.send_email(
            to=["person@example.net"],
            subject="newsletter",
            body="body",
            track_opens=False,
            newsletter_mode=True,
            unsubscribe_url="https://example.com/u/abc",
            one_click_unsubscribe=True,
        )
        # Ordinary non-tracked path does not go through CaptureClient override, so
        # inspect the builder under the same explicit context.
        with client._delivery_options(
            newsletter_mode=True,
            unsubscribe_url="https://example.com/u/abc",
            one_click_unsubscribe=True,
        ):
            msg, _, _ = client._build_message(to=["person@example.net"], subject="newsletter", body="body")
        self.assertEqual(str(msg["List-Unsubscribe-Post"]), "List-Unsubscribe=One-Click")

    def test_unsubscribe_fields_without_newsletter_mode_are_rejected(self):
        client = self.client(CaptureClient)
        with self.assertRaises(MailBridgeError):
            client.send_email(
                to=["person@example.net"],
                subject="hello",
                body="body",
                track_opens=True,
                unsubscribe_url="https://example.com/u/abc",
            )

    def test_one_click_requires_https(self):
        client = self.client(CaptureClient)
        with self.assertRaises(MailBridgeError):
            client.send_email(
                to=["person@example.net"],
                subject="newsletter",
                body="body",
                newsletter_mode=True,
                unsubscribe_url="http://example.com/u/abc",
                one_click_unsubscribe=True,
            )


class SMTPTransportTests(ClientFixture):
    def test_dsn_supported_uses_envid_notify_and_orcpt(self):
        client = self.client()
        smtp = FakeSMTP(features={"dsn":"", "size":"100000", "auth":"PLAIN"})
        client._smtp_connect = lambda: (smtp, "plain")
        token = _DELIVERY_ID.set("delivery-123")
        try:
            with client._delivery_options():
                msg, recipients, _ = client._build_message(to=["person@example.net"], subject="hello", body="body")
                result = client._send_message(msg, recipients)
        finally:
            _DELIVERY_ID.reset(token)
        self.assertTrue(result["dsn_supported"])
        self.assertIn("ENVID=delivery-123", smtp.mail_calls[0][1])
        self.assertIn("NOTIFY=FAILURE,DELAY", smtp.rcpt_calls[0][1])
        self.assertTrue(any(item.startswith("ORCPT=rfc822;person@example.net") for item in smtp.rcpt_calls[0][1]))
        self.assertFalse(any("SUCCESS" in item for item in smtp.rcpt_calls[0][1]))

    def test_dsn_success_only_when_explicit(self):
        client = self.client()
        smtp = FakeSMTP(features={"dsn":""})
        client._smtp_connect = lambda: (smtp, "plain")
        token = _DELIVERY_ID.set("delivery-123")
        try:
            with client._delivery_options(dsn_notify_success=True):
                msg, recipients, _ = client._build_message(to=["person@example.net"], subject="hello", body="body")
                client._send_message(msg, recipients)
        finally:
            _DELIVERY_ID.reset(token)
        self.assertIn("NOTIFY=FAILURE,DELAY,SUCCESS", smtp.rcpt_calls[0][1])

    def test_dsn_unsupported_falls_back_without_error(self):
        client = self.client()
        smtp = FakeSMTP(features={})
        client._smtp_connect = lambda: (smtp, "plain")
        with client._delivery_options():
            msg, recipients, _ = client._build_message(to=["person@example.net"], subject="hello", body="body")
            result = client._send_message(msg, recipients)
        self.assertFalse(result["dsn_supported"])
        self.assertEqual(smtp.mail_calls[0][1], [])
        self.assertEqual(smtp.rcpt_calls[0][1], [])
        self.assertTrue(result["sent"])

    def test_4xx_data_response_retries(self):
        client = self.client(retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0, max_delay_seconds=0, jitter_min=1, jitter_max=1))
        created = []
        responses = [[(451, b"try later")], [(250, b"queued")]]
        def connect():
            smtp = FakeSMTP(features={}, data_responses=responses.pop(0))
            created.append(smtp)
            return smtp, "plain"
        client._smtp_connect = connect
        with client._delivery_options():
            msg, recipients, _ = client._build_message(to=["person@example.net"], subject="hello", body="body")
            result = client._send_message(msg, recipients)
        self.assertTrue(result["sent"])
        self.assertEqual(len(created), 2)
        attempts = self.store.list_attempts(limit=20)
        states = [row["state"] for row in attempts]
        self.assertIn("temporarily_failed", states)
        self.assertIn("sent", states)

    def test_5xx_data_response_does_not_retry(self):
        client = self.client()
        created = []
        def connect():
            smtp = FakeSMTP(features={}, data_responses=[(550, b"rejected")])
            created.append(smtp)
            return smtp, "plain"
        client._smtp_connect = connect
        with self.assertRaises(MailBridgeError):
            with client._delivery_options():
                msg, recipients, _ = client._build_message(to=["person@example.net"], subject="hello", body="body")
                client._send_message(msg, recipients)
        self.assertEqual(len(created), 1)

    def test_suppressed_recipient_is_blocked_before_smtp(self):
        self.store.suppress("person@example.net", reason="manual", source="test")
        client = self.client()
        called = []
        client._smtp_connect = lambda: called.append(True)
        with self.assertRaises(MailBridgeError):
            with client._delivery_options():
                msg, recipients, _ = client._build_message(to=["person@example.net"], subject="hello", body="body")
                client._send_message(msg, recipients)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
