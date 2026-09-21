from __future__ import annotations

from pathlib import Path
from email.message import EmailMessage
from datetime import UTC, datetime
import base64
import sys
import types

# Local candidate tests run without installing the production MCP SDK.
mcp_mod = types.ModuleType("mcp")
mcp_types = types.ModuleType("mcp.types")
class ToolAnnotations:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
mcp_types.ToolAnnotations = ToolAnnotations
sys.modules.setdefault("mcp", mcp_mod)
sys.modules.setdefault("mcp.types", mcp_types)
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from postmaster.runtime_v990 import MCP_COMMAND_COUNT_V990, install_runtime_v990
from postmaster.calendar_v990 import build_calendar_request


class FakeMCP:
    def __init__(self):
        self.tools = {}
    def remove_tool(self, name):
        self.tools.pop(name, None)
    def add_tool(self, fn, name=None, annotations=None):
        self.tools[name or fn.__name__] = (fn, annotations)


class FakeAccountStore:
    def __init__(self, tracking_default=False):
        self.tracking_default = tracking_default
    def get_account(self, account_id=None):
        return {"id": account_id or "acct", "tracking_default": self.tracking_default, "email_address": "me@example.com", "label": "Me"}


class FakeIndexEngine:
    pass


class FakeCore(SimpleNamespace):
    pass


class RuntimeV990Tests(unittest.TestCase):
    def make(self, td, *, tracking_default=False):
        mcp = FakeMCP()
        calls = []
        def sent(**kwargs):
            calls.append(("send", kwargs)); return {"ok": True, "sent": True}
        def threaded(name):
            def fn(**kwargs): calls.append((name, kwargs)); return {"ok": True, "sent": True}
            return fn
        ics = build_calendar_request(
            uid="mail-invite@example.com", summary="Inbound invite",
            start_utc=datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
            end_utc=datetime(2026, 9, 22, 10, 30, tzinfo=UTC),
            organizer_email="sender@example.com", attendees=["me@example.com"],
            video_url="https://meet.google.com/abc-defg-hij",
        )
        msg = EmailMessage()
        msg["Subject"] = "Invite"
        msg["From"] = "sender@example.com"
        msg["To"] = "me@example.com"
        msg.set_content("See invite")
        msg.add_alternative(ics, subtype="calendar", params={"method":"REQUEST"})
        raw_b64 = base64.b64encode(msg.as_bytes()).decode("ascii")
        core = FakeCore(
            mcp=mcp,
            send_email=sent,
            reply_email=threaded("reply"),
            follow_up_email=threaded("follow"),
            list_open_events=lambda **kw: {"ok": True, "events": [{"delivery_id":"d1","opened_at":"2026-09-20T10:01:01Z","user_agent":"GoogleImageProxy","client_source":"gmail_image_proxy"}]},
            list_tracking_deliveries=lambda **kw: {"ok": True, "deliveries": [{"delivery_id":"d1","sent_at":"2026-09-20T10:00:00Z"}]},
            search_emails=lambda **kw: [],
            get_email=lambda **kw: {"ok": True, "email": {"uid": str(kw.get("uid") or "1"), "subject":"Invite", "raw": raw_b64}},
            list_email_attachments=lambda **kw: {"attachments": []},
            read_email_attachment=lambda **kw: {},
            get_email_attachment=lambda **kw: {},
            get_job=lambda **kw: {"id":"j1","title":"Call","description":"Discuss","schedule_type":"once","schedule_value":"2026-09-21T10:00:00+00:00","timezone":"UTC","payload":{"calendar":{"attendees":["you@example.com"],"duration_minutes":30,"video_url":"https://meet.google.com/job-call"}}},
        )
        store = FakeAccountStore(tracking_default)
        base = SimpleNamespace(account_store=lambda: store, context_engine=lambda: FakeIndexEngine())
        for name in ("search_emails","get_email","list_email_attachments","read_email_attachment","get_email_attachment"):
            setattr(base, name, getattr(core, name))
        result = install_runtime_v990(base, core, lambda: {"ok": True, "mcp_command_count_expected":118}, tracking_approval_db=str(Path(td)/"approval.db"), email_search_db=str(Path(td)/"search.db"))
        return base, core, calls, result

    def test_effective_off_blocks_then_exact_one_use_approval_sends(self):
        with TemporaryDirectory() as td:
            base, core, calls, _ = self.make(td, tracking_default=False)
            first = core.send_email(to=["you@example.com"], subject="Hello", body="Body")
            self.assertTrue(first["approval_required"])
            self.assertFalse(first["send_performed"])
            self.assertEqual(calls, [])
            second = core.send_email(to=["you@example.com"], subject="Hello", body="Body", tracking_disable_approval_id=first["preview_id"])
            self.assertTrue(second["sent"])
            self.assertEqual(len(calls), 1)
            third = core.send_email(to=["you@example.com"], subject="Hello", body="Body", tracking_disable_approval_id=first["preview_id"])
            self.assertFalse(third["ok"])
            self.assertEqual(len(calls), 1)

    def test_default_on_sends_without_tracking_off_gate(self):
        with TemporaryDirectory() as td:
            _, core, calls, _ = self.make(td, tracking_default=True)
            result = core.send_email(to=["you@example.com"], subject="Hello")
            self.assertTrue(result["sent"])
            self.assertEqual(len(calls), 1)

    def test_open_events_are_enriched_with_60_second_policy(self):
        with TemporaryDirectory() as td:
            _, core, _, _ = self.make(td)
            result = core.list_open_events()
            cls = result["events"][0]["open_classification"]
            self.assertEqual(cls["classification"], "likely_human_via_proxy")
            self.assertEqual(cls["metadata_reliability"], "low")
            self.assertEqual(result["classification_policy"]["google_image_proxy_human_delay_seconds"], 60)


    def test_get_email_always_adds_local_calendar_analysis(self):
        with TemporaryDirectory() as td:
            _, core, _, _ = self.make(td, tracking_default=True)
            result = core.get_email(mailbox="INBOX", uid="1")
            self.assertTrue(result["calendar_analysis_policy"]["mandatory"])
            self.assertFalse(result["calendar_analysis_policy"]["external_actions"])
            analysis = result["email"]["calendar_analysis"]
            self.assertTrue(analysis.get("calendar_candidate") or analysis.get("calendar_detected"))
            self.assertFalse(analysis["network_access"])

    def test_open_event_resolves_sent_at_from_delivery(self):
        with TemporaryDirectory() as td:
            _, core, _, _ = self.make(td)
            result = core.list_open_events()
            cls = result["events"][0]["open_classification"]
            self.assertEqual(cls["classification"], "likely_human_via_proxy")
            self.assertEqual(cls["delay_seconds"], 61.0)

    def test_calendar_invite_is_explicit_send_and_preserves_video_link(self):
        with TemporaryDirectory() as td:
            _, core, calls, _ = self.make(td, tracking_default=True)
            result = core.send_job_calendar_invite(job_id="j1")
            self.assertTrue(result["sent"])
            self.assertEqual(len(calls), 1)
            payload = calls[0][1]
            self.assertEqual(payload["to"], ["you@example.com"])
            self.assertEqual(payload["attachments"][0]["filename"], "invite.ics")
            decoded = base64.b64decode(payload["attachments"][0]["content_base64"]).decode("utf-8")
            self.assertIn("METHOD:REQUEST", decoded)
            self.assertIn("https://meet.google.com/job-call", decoded)

    def test_runtime_status_declares_release_policy(self):
        with TemporaryDirectory() as td:
            _, core, _, result = self.make(td)
            status = core.runtime_status()
            self.assertEqual(status["version_capability"], "9.9.0")
            self.assertEqual(status["mcp_command_count_expected"], MCP_COMMAND_COUNT_V990)
            self.assertTrue(status["tracking_disable_requires_fresh_chat_approval"])
            self.assertTrue(status["stored_file_link_preferred_over_attachment"])

    def test_new_tool_descriptions_contain_storage_and_approval_rules(self):
        with TemporaryDirectory() as td:
            _, core, _, _ = self.make(td)
            self.assertIn("Stored File", core.send_email.__doc__)
            self.assertIn("fresh explicit chat approval", core.send_email.__doc__)
            self.assertIn("ASCII", core.send_email.__doc__)


if __name__ == "__main__":
    unittest.main()
