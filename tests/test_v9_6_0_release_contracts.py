from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.testclient import TestClient

from postmaster.inbound_inspection import inspect_message
from postmaster.mail_v960 import PostmasterV960MailClient
from postmaster.runtime_v960 import install_runtime_v960
from postmaster.unsubscribe import UnsubscribeError, UnsubscribeManager
from postmaster.webgui_v960_scopes import decode_scope_values


BASELINE_MCP_NAMES = set(
    """
list_email_accounts test_email_account amp_account_status set_amp_account_state validate_amp_email
mailbox_status list_mailboxes search_emails get_email list_known_contacts list_email_attachments
get_email_attachment read_email_attachment email_security_status recipient_authorization_status
list_authorized_recipients list_authorized_domains authorize_domain revoke_domain authorize_recipient
revoke_recipient list_tracking_campaigns list_tracking_deliveries list_open_events send_email reply_email
create_draft create_reply_draft move_email mark_not_spam mark_as_spam set_email_seen knowledge_status
create_memory get_memory update_memory delete_memory list_memories create_skill get_skill update_skill
delete_skill list_skills search_knowledge get_project_context get_knowledge_history
restore_knowledge_revision get_knowledge_audit export_knowledge import_knowledge reindex_knowledge
file_store_status save_file save_uploaded_file save_uploaded_files save_text_file list_files get_file_info
read_text_file get_file_base64 update_file_metadata delete_stored_file scheduler_status create_owner
list_owners create_project list_projects create_execution_profile list_execution_profiles preview_schedule
create_job list_jobs list_due_jobs get_job update_job pause_job resume_job approve_job complete_job delete_job
get_job_history build_status follow_up_email create_follow_up_draft get_stored_file_resource tracking_status
get_tracking_campaign get_tracking_summary list_tracking_links list_tracking_events
""".split()
)
V967_LIFECYCLE_MCP_NAMES = {
    "runtime_status",
    "runtime_version_change_preview",
    "runtime_version_change_execute",
    "privacy_proxy_status",
    "privacy_proxy_provisioning_preview",
    "privacy_proxy_provisioning_execute",
}


class CanonicalUnsubscribeUrlV960Tests(unittest.TestCase):
    def test_public_email_base_url_then_public_mcp_host_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = str(Path(tmp) / "unsubscribe.key")
            with patch.dict(
                os.environ,
                {
                    "PUBLIC_EMAIL_BASE_URL": "https://mail.example.test/base/",
                    "PUBLIC_MCP_HOST": "fallback.example.test",
                    "TRACKING_PUBLIC_BASE_URL": "https://parallel.invalid",
                    "PUBLIC_BASE_URL": "https://parallel2.invalid",
                },
                clear=False,
            ):
                manager = UnsubscribeManager(key_path=key)
                self.assertTrue(manager.url_for_delivery("delivery-1").startswith("https://mail.example.test/base/unsubscribe/"))

            with patch.dict(
                os.environ,
                {
                    "PUBLIC_EMAIL_BASE_URL": "",
                    "PUBLIC_MCP_HOST": "fallback.example.test",
                    "TRACKING_PUBLIC_BASE_URL": "https://parallel.invalid",
                    "PUBLIC_BASE_URL": "https://parallel2.invalid",
                },
                clear=False,
            ):
                manager = UnsubscribeManager(key_path=key)
                self.assertTrue(manager.url_for_delivery("delivery-2").startswith("https://fallback.example.test/unsubscribe/"))

    def test_parallel_public_url_variables_are_not_fallbacks(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "PUBLIC_EMAIL_BASE_URL": "",
                "PUBLIC_MCP_HOST": "",
                "TRACKING_PUBLIC_BASE_URL": "https://parallel.invalid",
                "PUBLIC_BASE_URL": "https://parallel2.invalid",
            },
            clear=False,
        ):
            manager = UnsubscribeManager(key_path=str(Path(tmp) / "unsubscribe.key"))
            with self.assertRaisesRegex(UnsubscribeError, "PUBLIC_EMAIL_BASE_URL or PUBLIC_MCP_HOST"):
                manager.url_for_delivery("delivery-3")


class StaticInspectionNetworkV960Tests(unittest.TestCase):
    def test_inspection_never_performs_dns_or_http(self):
        raw = b"""From: sender@example.net\r
To: me@example.test\r
Subject: no network\r
Content-Type: text/html; charset=utf-8\r
\r
<a href=\"https://redirect.example/r?url=https%3A%2F%2Fexample.org\">example.org</a>
<img src=\"https://tracker.example/open\" width=\"1\" height=\"1\">"""
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        html = str(msg.get_content())
        with patch("socket.getaddrinfo", side_effect=AssertionError("DNS must not be called")), patch(
            "urllib.request.urlopen", side_effect=AssertionError("HTTP must not be called")
        ):
            result = inspect_message(msg, body_html=html, body_text="", mode="full")
        self.assertEqual(result["network_requests_performed"], 0)
        self.assertTrue(result["static_only"])


class SeenUnseenV960Tests(unittest.TestCase):
    def test_seen_flag_parser_distinguishes_seen_and_unseen(self):
        self.assertTrue(PostmasterV960MailClient._seen_from_fetch([(b"1 (FLAGS (\\Seen \\Flagged))", b"")]))
        self.assertFalse(PostmasterV960MailClient._seen_from_fetch([(b"1 (FLAGS (\\Flagged))", b"")]))


class KnowledgeScopeWebGuiV960Tests(unittest.TestCase):
    def test_scope_form_values_round_trip_and_deduplicate(self):
        one = json.dumps({"owner_id": "owner-a", "project_id": "project-a"})
        two = json.dumps({"owner_id": "owner-b", "project_id": None})
        self.assertEqual(
            decode_scope_values([one, one, two]),
            [
                {"owner_id": "owner-a", "project_id": "project-a"},
                {"owner_id": "owner-b", "project_id": None},
            ],
        )


class _FakeMcp:
    def remove_tool(self, name):
        return None

    def add_tool(self, fn, name=None):
        return None


class _FakeAnalytics:
    def __init__(self, recipient: str):
        self.recipient = recipient

    def get_delivery(self, delivery_id: str):
        return {"id": delivery_id, "recipient": self.recipient}


class _FakeReliability:
    def __init__(self):
        self.calls = []

    def suppress(self, recipient: str, *, reason: str, source: str):
        self.calls.append({"recipient": recipient, "reason": reason, "source": source})
        return {"ok": True}


class _FakeBase(SimpleNamespace):
    def _safe_call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class UnsubscribeEndpointV960Tests(unittest.TestCase):
    def test_get_is_non_destructive_post_suppresses_and_one_click_is_identified(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = UnsubscribeManager(
                key_path=str(Path(tmp) / "unsubscribe.key"),
                public_base_url="https://mail.example.test",
            )
            token = manager.sign_delivery("delivery-123")
            reliability = _FakeReliability()
            base = _FakeBase(
                analytics_store=lambda: _FakeAnalytics("recipient@example.net"),
            )
            core = SimpleNamespace(mcp=_FakeMcp())
            app = Starlette()
            with patch("postmaster.runtime_v960.unsubscribe_manager", return_value=manager), patch(
                "postmaster.runtime_v960.reliability_store", return_value=reliability
            ):
                install_runtime_v960(app, base, core, None, lambda: {})
                with TestClient(app) as client:
                    response = client.get(f"/unsubscribe/{token}")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("Opening this page alone does not unsubscribe", response.text)
                    self.assertEqual(reliability.calls, [])

                    response = client.post(f"/unsubscribe/{token}", data={})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(reliability.calls[-1]["reason"], "unsubscribe")
                    self.assertEqual(reliability.calls[-1]["source"], "web_confirmation")

                    response = client.post(
                        f"/unsubscribe/{token}",
                        data={"List-Unsubscribe": "One-Click"},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(reliability.calls[-1]["reason"], "unsubscribe")
                    self.assertEqual(reliability.calls[-1]["source"], "list_unsubscribe_one_click")


class McpNameCompatibilityV960Tests(unittest.TestCase):
    def test_composed_runtime_keeps_exact_90_baseline_tool_names(self):
        self.assertEqual(len(BASELINE_MCP_NAMES), 90)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = os.environ.copy()
            env.update(
                {
                    "MAIL_EMAIL": "test@example.invalid",
                    "MAIL_PASSWORD": "test-only-password",
                    "IMAP_HOST": "imap.example.com",
                    "SMTP_HOST": "smtp.example.com",
                    "ENABLE_SEND": "false",
                    "CONTEXT_SEMANTIC_ENABLED": "false",
                    "CONTEXT_DB_PATH": str(root / "knowledge.db"),
                    "SCHEDULER_DB_PATH": str(root / "scheduler.db"),
                    "MAIL_ACCOUNTS_DB_PATH": str(root / "accounts.db"),
                    "MAIL_ACCOUNTS_KEY_PATH": str(root / "accounts.key"),
                    "EMAIL_ANALYTICS_DB_PATH": str(root / "analytics.db"),
                    "EMAIL_ANALYTICS_KEY_PATH": str(root / "analytics.key"),
                    "RECIPIENT_POLICY_DB_PATH": str(root / "recipient-policy.db"),
                    "FILE_STORE_DB_PATH": str(root / "files.db"),
                    "FILE_STORE_ROOT": str(root / "files"),
                    "UNSUBSCRIBE_KEY_PATH": str(root / "unsubscribe.key"),
                    "POSTMASTER_STRUCTURED_DATA_DB": str(root / "structured.db"),
                }
            )
            script = (
                "import json; import postmaster.runtime as runtime; "
                "tools=runtime.mcp._tool_manager.list_tools(); "
                "print('MCP_NAMES='+json.dumps(sorted(t.name for t in tools)))"
            )
            completed = subprocess.run(
                [sys.executable, "-c", script],
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + "\n" + completed.stderr)
            line = next(
                (value for value in completed.stdout.splitlines() if value.startswith("MCP_NAMES=")),
                "",
            )
            self.assertTrue(line, completed.stdout)
            actual = set(json.loads(line.split("=", 1)[1]))
            legacy_extensions = V967_LIFECYCLE_MCP_NAMES | {"fetch_email_remote_content"}
            self.assertTrue(BASELINE_MCP_NAMES <= actual)
            self.assertTrue(legacy_extensions <= actual)
            structured_extensions = actual - BASELINE_MCP_NAMES - legacy_extensions
            self.assertEqual(len(structured_extensions), 21)
            self.assertTrue(all(name.startswith("db_") for name in structured_extensions))
            self.assertEqual(len(actual), 118)


if __name__ == "__main__":
    unittest.main()
