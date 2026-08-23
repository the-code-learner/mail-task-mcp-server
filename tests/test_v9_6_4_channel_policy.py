from __future__ import annotations

import contextlib
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount
from starlette.testclient import TestClient

from postmaster.delivery_reliability import ReliabilityStore
from postmaster.file_store import FileStore
from postmaster.mail_bridge import MailBridgeError, Settings
from postmaster.mail_v960_unsubscribe import PostmasterV960NewsletterMailClient
from postmaster.outbound_safety import OutboundSafetyStore
from postmaster.runtime_v964 import install_runtime_v964
from postmaster.unsubscribe import UnsubscribeManager
from postmaster.webgui_v964 import install_webgui_v964


def _file_store(db_path: str) -> FileStore:
    path = Path(db_path)
    return FileStore(str(path.with_name("files.db")), str(path.with_name("files")))


class ChannelPolicyClientTests(unittest.TestCase):
    def settings(self) -> Settings:
        return Settings(
            email_address="sender@example.test",
            email_password="test-only",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            inbox_mailbox="INBOX",
            sent_mailbox="Sent",
            allow_previous_sent_recipients=False,
        )

    def make_client(self, tmp: str) -> PostmasterV960NewsletterMailClient:
        db = str(Path(tmp) / "analytics.db")
        return PostmasterV960NewsletterMailClient(
            self.settings(),
            reliability=ReliabilityStore(db),
            outbound_safety=OutboundSafetyStore(db),
            file_store=_file_store(db),
            unsubscribe_manager=UnsubscribeManager(
                key_path=str(Path(tmp) / "unsubscribe.key"),
                public_base_url="https://postmaster.example.test",
            ),
        )

    def test_manual_webgui_policy_bypasses_automated_allowlist_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"RECIPIENT_POLICY_DB_PATH": str(Path(tmp) / "policy.db")},
            clear=False,
        ):
            client = self.make_client(tmp)
            client.authorize_domain("allowed.example", note="automated policy")
            before_domains = client.list_authorized_domains()
            before_recipients = client.list_authorized_recipients()

            with self.assertRaisesRegex(MailBridgeError, "not authorized for automated sending"):
                client._validate_recipients(["manual@other.example"])

            with client.manual_webgui_send():
                self.assertEqual(
                    client._validate_recipients(["manual@other.example"]),
                    ["manual@other.example"],
                )

            with self.assertRaisesRegex(MailBridgeError, "not authorized for automated sending"):
                client._validate_recipients(["manual@other.example"])
            self.assertEqual(client.list_authorized_domains(), before_domains)
            self.assertEqual(client.list_authorized_recipients(), before_recipients)

    def test_suppression_authorization_is_exact_and_resets_after_one_context(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"RECIPIENT_POLICY_DB_PATH": str(Path(tmp) / "policy.db")},
            clear=False,
        ):
            client = self.make_client(tmp)
            client._suppression_store.suppress(
                "blocked@example.net",
                reason="unsubscribe",
                source="test",
            )
            with self.assertRaisesRegex(MailBridgeError, "Suppressed recipient authorization required"):
                client._preflight_suppression(["blocked@example.net"])

            with client.manual_webgui_send(
                authorized_suppressed_recipients=["blocked@example.net"]
            ):
                client._preflight_suppression(["blocked@example.net"])
                with self.assertRaisesRegex(MailBridgeError, "other@example.net"):
                    client._suppression_store.suppress(
                        "other@example.net", reason="manual", source="test"
                    )
                    client._preflight_suppression(
                        ["blocked@example.net", "other@example.net"]
                    )

            with self.assertRaisesRegex(MailBridgeError, "Suppressed recipient authorization required"):
                client._preflight_suppression(["blocked@example.net"])


class FakeManualClient:
    def __init__(self):
        self.blocked: dict[str, dict] = {}
        self.send_calls: list[dict] = []
        self.context_calls: list[tuple[str, ...]] = []

    @staticmethod
    def _clean_unlisted_recipients(recipients):
        cleaned = []
        for value in recipients:
            value = str(value).strip()
            if not value or "@" not in value:
                raise ValueError(f"Invalid recipient address: {value}")
            if value not in cleaned:
                cleaned.append(value)
        return cleaned

    def suppressed_recipients(self, recipients):
        return [self.blocked[value.casefold()] for value in recipients if value.casefold() in self.blocked]

    @contextlib.contextmanager
    def manual_webgui_send(self, *, authorized_suppressed_recipients=None):
        values = tuple(authorized_suppressed_recipients or ())
        self.context_calls.append(values)
        yield self

    def send_email(self, **kwargs):
        self.send_calls.append(kwargs)
        return {"ok": True, "sent": True}


class FakeWebBase:
    def __init__(self, client: FakeManualClient):
        self.client = client
        self.verified_calls = 0
        self.draft_calls: list[dict] = []

    @staticmethod
    def _csrf_value():
        return "csrf-test"

    async def _verified_form(self, request):
        self.verified_calls += 1
        raw = (await request.body()).decode("utf-8")
        parsed = {
            key: values[-1]
            for key, values in parse_qs(raw, keep_blank_values=True).items()
        }
        if parsed.get("csrf") != "csrf-test":
            return None, PlainTextResponse("Invalid CSRF token", status_code=403)
        return parsed, None

    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def mail_client(self, account_id=None):
        return self.client

    def create_draft(self, **kwargs):
        self.draft_calls.append(kwargs)
        return {"ok": True, "draft_saved": True}

    def create_reply_draft(self, **kwargs):
        self.draft_calls.append({"mode": "reply", **kwargs})
        return {"ok": True, "draft_saved": True}

    def create_follow_up_draft(self, **kwargs):
        self.draft_calls.append({"mode": "follow_up", **kwargs})
        return {"ok": True, "draft_saved": True}


def make_web_app(client: FakeManualClient):
    base = FakeWebBase(client)
    fallback = Starlette(routes=[])
    app = Starlette(routes=[Mount("/", app=fallback)])
    install_webgui_v964(app, base)
    return app, base


class WebGuiChannelPolicyTests(unittest.TestCase):
    def post_data(self, **updates):
        data = {
            "csrf": "csrf-test",
            "thread_mode": "send",
            "compose_action": "send",
            "to": "manual@example.net",
            "subject": "Manual send",
            "body": "Hello",
            "idempotency_key": "webgui-v964-test",
        }
        data.update(updates)
        return data

    def test_unsuppressed_manual_send_executes_inside_manual_context(self):
        fake = FakeManualClient()
        app, base = make_web_app(fake)
        with TestClient(app) as browser:
            response = browser.post(
                "/dashboard/compose/send",
                data=self.post_data(),
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(base.verified_calls, 1)
        self.assertEqual(len(fake.send_calls), 1)
        self.assertEqual(fake.context_calls, [()])
        self.assertEqual(fake.send_calls[0]["to"], ["manual@example.net"])

    def test_suppressed_first_post_warns_and_second_explicit_confirmation_sends_once(self):
        fake = FakeManualClient()
        fake.blocked["manual@example.net"] = {
            "recipient": "manual@example.net",
            "reason": "unsubscribe",
            "source": "test",
        }
        app, base = make_web_app(fake)
        with TestClient(app) as browser:
            first = browser.post(
                "/dashboard/compose/send",
                data=self.post_data(),
                follow_redirects=False,
            )
            self.assertEqual(first.status_code, 409)
            self.assertIn("No email has been sent", first.text)
            self.assertIn("manual@example.net", first.text)
            self.assertEqual(fake.send_calls, [])
            second = browser.post(
                "/dashboard/compose/send",
                data=self.post_data(
                    confirm_suppressed="1",
                    warned_suppressed_recipients="manual@example.net",
                ),
                follow_redirects=False,
            )
        self.assertEqual(second.status_code, 303)
        self.assertEqual(base.verified_calls, 2)
        self.assertEqual(len(fake.send_calls), 1)
        self.assertEqual(fake.context_calls, [("manual@example.net",)])

    def test_new_suppression_between_warning_and_confirmation_forces_new_warning(self):
        fake = FakeManualClient()
        fake.blocked["first@example.net"] = {
            "recipient": "first@example.net", "reason": "manual", "source": "test"
        }
        app, _ = make_web_app(fake)
        data = self.post_data(to="first@example.net, second@example.net")
        with TestClient(app) as browser:
            first = browser.post("/dashboard/compose/send", data=data, follow_redirects=False)
            self.assertEqual(first.status_code, 409)
            fake.blocked["second@example.net"] = {
                "recipient": "second@example.net", "reason": "unsubscribe", "source": "race"
            }
            second = browser.post(
                "/dashboard/compose/send",
                data={
                    **data,
                    "confirm_suppressed": "1",
                    "warned_suppressed_recipients": "first@example.net",
                },
                follow_redirects=False,
            )
        self.assertEqual(second.status_code, 409)
        self.assertIn("second@example.net", second.text)
        self.assertEqual(fake.send_calls, [])

    def test_draft_path_verifies_csrf_once_and_never_enters_manual_send_context(self):
        fake = FakeManualClient()
        app, base = make_web_app(fake)
        with TestClient(app) as browser:
            response = browser.post(
                "/dashboard/compose/send",
                data=self.post_data(compose_action="draft"),
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(base.verified_calls, 1)
        self.assertEqual(len(base.draft_calls), 1)
        self.assertEqual(fake.context_calls, [])
        self.assertEqual(fake.send_calls, [])

    def test_overlay_route_is_registered_before_catch_all_mount(self):
        fake = FakeManualClient()
        app, _ = make_web_app(fake)
        routes = app.router.routes
        mount_index = next(i for i, route in enumerate(routes) if isinstance(route, Mount))
        send_index = next(
            i for i, route in enumerate(routes)
            if getattr(route, "path", "") == "/dashboard/compose/send"
        )
        self.assertLess(send_index, mount_index)


class FakeMcpRegistry:
    def __init__(self):
        self.tools = {f"placeholder_{index}" for index in range(86)} | {
            "build_status", "send_email", "reply_email", "follow_up_email"
        }

    def remove_tool(self, name):
        self.tools.discard(name)

    def add_tool(self, fn, *, name):
        self.tools.add(name)


class FakeRuntimeBase:
    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def mail_client(self, account_id=None):
        raise AssertionError("runtime contract test must not execute a send")


class FakeCore:
    def __init__(self):
        self.mcp = FakeMcpRegistry()


class RuntimeV964ContractTests(unittest.TestCase):
    def test_existing_mcp_tool_names_remain_90_and_gain_per_send_confirmation_argument(self):
        base = FakeRuntimeBase()
        core = FakeCore()
        before = set(core.mcp.tools)
        install_runtime_v964(base, core, lambda: {"ok": True})
        self.assertEqual(len(core.mcp.tools), 90)
        self.assertEqual(core.mcp.tools, before)
        for fn in (core.send_email, core.reply_email, core.follow_up_email):
            self.assertIn("confirm_suppressed_recipients", inspect.signature(fn).parameters)
        status = core.build_status()
        self.assertEqual(status["version_capability"], "9.6.4")
        self.assertEqual(status["mcp_command_count_expected"], 90)
        self.assertTrue(status["suppression_confirmation_per_send"])


if __name__ == "__main__":
    unittest.main()
