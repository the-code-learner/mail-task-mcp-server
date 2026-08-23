from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

from starlette.requests import Request

from postmaster import webgui_v960 as v960
from postmaster.runtime_v961 import classify_mailbox_role_v961
from postmaster.webgui_v961 import (
    _scope_chips_v961,
    _scope_labels_v961,
    inbox_prefetch_limit,
    install_webgui_v961,
    project_color,
    render_inbox_v961,
)


ROOT = Path(__file__).resolve().parents[1]


def _request(query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": query.encode("utf-8"),
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


def _git_blob_sha1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


class _MailBase:
    def __init__(self, mailbox: str = "INBOX", role: str = "received") -> None:
        self.mailbox = mailbox
        self.role = role
        self.search_limits: list[int] = []
        self.get_calls: list[dict] = []
        self.rows = [
            {
                "uid": str(index),
                "from": f"sender{index}@example.test",
                "to": f"recipient{index}@example.test",
                "subject": f"Subject {index}",
                "date": "2026-08-23T00:00:00+00:00",
                "seen": index % 2 == 0,
                "message_id": f"<message-{index}@example.test>",
            }
            for index in range(1, 80)
        ]

    def _safe_call(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    @staticmethod
    def _csrf_value() -> str:
        return "csrf-test"

    @staticmethod
    def list_email_accounts():
        return {
            "accounts": [
                {
                    "id": "acct",
                    "label": "Test account",
                    "email_address": "me@example.test",
                    "enabled": True,
                    "is_default": True,
                }
            ]
        }

    def list_mailboxes(self, **kwargs):
        return {
            "mailbox_roles": [
                {"name": self.mailbox, "role": self.role, "flags": []},
            ],
            "timings_ms": {"imap_list": 1.0},
        }

    def search_emails(self, **kwargs):
        limit = int(kwargs["limit"])
        self.search_limits.append(limit)
        return {
            "emails": self.rows[:limit],
            "timings_ms": {"imap_search": 1.0, "imap_fetch": 2.0, "imap_flags": 1.0},
        }

    def get_email(self, **kwargs):
        self.get_calls.append(dict(kwargs))
        uid = str(kwargs["uid"])
        return {
            "uid": uid,
            "from": "sender@example.test",
            "to": "me@example.test",
            "subject": "Representative detail",
            "date": "2026-08-23T00:00:00+00:00",
            "body_html": "<p>Sanitized representative body</p>",
            "privacy_inspection": {"links": [], "network_requests_performed": 0},
            "headers": [{"name": "Message-ID", "value": "<detail@example.test>"}],
            "mime": {"content_type": "text/html"},
            "performance_ms": {"inspection": 0.5},
        }


class MailboxRoleV961Tests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            inbox_mailbox="INBOX",
            sent_mailbox="Sent",
            junk_mailbox="Junk",
            draft_mailbox="Drafts",
        )

    def test_hierarchical_and_provider_mailboxes_share_logical_roles(self):
        cases = {
            "INBOX": "received",
            "INBOX.Sent": "sent",
            "INBOX.Junk": "spam",
            "INBOX.Drafts": "drafts",
            "INBOX.Trash": "trash",
            "[Gmail]/Sent Mail": "sent",
            "[Gmail]/Trash": "trash",
            "Archive/Deleted Items": "trash",
        }
        for mailbox, expected in cases.items():
            with self.subTest(mailbox=mailbox):
                self.assertEqual(classify_mailbox_role_v961(mailbox, [], self.settings), expected)


class FragmentVisibilityV961Tests(unittest.TestCase):
    def test_progressive_fragment_replacement_preserves_active_panel(self):
        install_webgui_v961()
        source = v960.SCRIPT
        inherit = source.index("target.classList.contains('active')")
        replace = source.index("target.replaceWith(next)")
        self.assertLess(inherit, replace)
        self.assertIn("next.classList.add('active')", source)


class InboxRenderingV961Tests(unittest.TestCase):
    def setUp(self):
        install_webgui_v961()

    def test_click_detail_renders_non_empty_safe_inline_reader(self):
        base = _MailBase()
        request = _request("account_id=acct&mailbox=INBOX&message_uid=1&page=1")
        html = render_inbox_v961(base, request)
        self.assertIn('data-v960-href=', html)
        self.assertIn('class="v960-inline-detail"', html)
        self.assertIn('class="v960-reader"', html)
        self.assertIn("Sanitized representative body", html)
        self.assertIn('data-v960-detail-tab="privacy"', html)
        self.assertIn('data-v960-detail-tab="links"', html)
        self.assertIn('data-v960-detail-tab="headers"', html)
        self.assertIn('data-v960-detail-tab="mime"', html)
        self.assertEqual(base.search_limits, [26])
        self.assertEqual(base.get_calls[0]["inspection"], "full")
        self.assertEqual(base.get_calls[0]["content_mode"], "safe")
        self.assertIn("v960-unread", html)

    def test_sent_rows_use_to_column_and_remain_non_empty(self):
        base = _MailBase(mailbox="INBOX.Sent", role="sent")
        request = _request("account_id=acct&mailbox=INBOX.Sent&page=1")
        with patch.object(v960.v954, "_build_tracking_read_model", return_value={}):
            html = render_inbox_v961(base, request)
        self.assertIn("<th>To</th>", html)
        self.assertIn("recipient1@example.test", html)
        self.assertNotIn("No messages matched.", html)

    def test_webgui_prefetches_only_current_page_plus_next_sentinel(self):
        self.assertEqual(inbox_prefetch_limit(_request("page=1")), 26)
        self.assertEqual(inbox_prefetch_limit(_request("page=2")), 51)
        self.assertEqual(inbox_prefetch_limit(_request("page=4")), 100)
        base = _MailBase()
        render_inbox_v961(base, _request("account_id=acct&mailbox=INBOX&page=2"))
        self.assertEqual(base.search_limits, [51])


class KnowledgeScopeUxV961Tests(unittest.TestCase):
    def test_project_colors_and_scope_order_are_deterministic(self):
        scopes = [
            {"owner_id": "davide", "project_id": "beta", "is_primary": False},
            {"owner_id": "davide", "project_id": "alpha", "is_primary": True},
            {"owner_id": "davide", "project_id": None, "is_primary": False},
        ]
        one = _scope_labels_v961({"scopes": scopes}, {"alpha": "Alpha", "beta": "Beta"})
        two = _scope_labels_v961({"scopes": list(reversed(scopes))}, {"alpha": "Alpha", "beta": "Beta"})
        self.assertEqual(one, two)
        self.assertEqual(one.count("v961-project-chip"), 3)
        self.assertIn("Global", one)
        self.assertIn("Alpha · primary", one)
        self.assertEqual(project_color("alpha", "davide"), project_color("alpha", "davide"))

    def test_empty_scope_is_explicitly_unassigned(self):
        html = _scope_labels_v961({"scopes": []}, {})
        self.assertIn("Unassigned", html)
        self.assertNotIn("Global", html)

    def test_project_filter_toggle_links_keep_multi_project_state(self):
        request = _request("ui_view=knowledge&projects=alpha,beta")
        projects = [
            {"id": "beta", "owner_id": "davide", "name": "Beta"},
            {"id": "alpha", "owner_id": "davide", "name": "Alpha"},
        ]
        html = _scope_chips_v961(request, projects, ["alpha", "beta"], False)
        self.assertEqual(html.count("v961-project-filter active"), 2)
        self.assertIn("--project-color:", html)
        alpha_href = html.split("davide / Alpha", 1)[0].rsplit('href="', 1)[1].split('"', 1)[0]
        query = alpha_href.split("?", 1)[1].split("#", 1)[0].replace("&amp;", "&")
        self.assertEqual(parse_qs(query).get("projects"), ["beta"])


class ReleaseBoundaryV961Tests(unittest.TestCase):
    def test_v961_history_and_preserved_single_yaml_contract(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## 9.6.1 - 2026-08-23", changelog)
        self.assertLess(changelog.index("## 9.6.1 - 2026-08-23"), changelog.index("## 9.6.0 - 2026-08-23"))
        yaml_payload = (ROOT / "postmaster-mcp.yml").read_bytes()
        self.assertEqual(
            _git_blob_sha1(yaml_payload),
            "f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9",
        )


if __name__ == "__main__":
    unittest.main()
