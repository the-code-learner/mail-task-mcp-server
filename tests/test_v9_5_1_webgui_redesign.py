from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from starlette.requests import Request

from postmaster.webgui_v951 import (
    MCP_COVERAGE,
    _nav_html,
    compose_send,
    filter_chronological_rows,
    parse_window,
    render_compose,
    render_coverage,
    render_overview,
    render_tracking_summary,
    window_cutoff,
)


def request(params: dict[str, str] | None = None, method: str = "GET") -> Request:
    query = urlencode(params or {})
    return Request({
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "path": "/", "raw_path": b"/", "query_string": query.encode(),
        "headers": [], "client": ("127.0.0.1", 12345), "server": ("localhost", 8000),
    })


class FakeAnalytics:
    def list_campaigns(self, **kwargs):
        now = datetime.now(timezone.utc)
        return [
            {"id": "recent", "created_at": (now - timedelta(hours=2)).isoformat()},
            {"id": "old", "created_at": (now - timedelta(days=120)).isoformat()},
        ]

    def list_deliveries(self, **kwargs):
        now = datetime.now(timezone.utc)
        return [
            {"id": "recent", "sent_at": (now - timedelta(hours=2)).isoformat()},
            {"id": "old", "sent_at": (now - timedelta(days=120)).isoformat()},
        ]

    def list_open_events(self, **kwargs):
        now = datetime.now(timezone.utc)
        return [
            {"opened_at": (now - timedelta(hours=1)).isoformat()},
            {"opened_at": (now - timedelta(days=120)).isoformat()},
        ]


class FakeAccounts:
    @staticmethod
    def list_accounts():
        return [{"id": "a"}, {"id": "b"}]


class FakeScheduler:
    @staticmethod
    def list_jobs(**kwargs):
        return [{"id": "j1"}, {"id": "j2"}, {"id": "j3"}]


class FakeFiles:
    @staticmethod
    def status():
        return {"files": 7}


class FakeContext:
    @staticmethod
    def status():
        return {"embedded_chunks": 440}


class FakeLinkStore:
    @staticmethod
    def unified_events(**kwargs):
        now = datetime.now(timezone.utc)
        return [
            {"event_type": "link", "observed_at": (now - timedelta(hours=1)).isoformat()},
            {"event_type": "link", "observed_at": (now - timedelta(days=120)).isoformat()},
        ]


class FakeCore:
    @staticmethod
    def link_store():
        return FakeLinkStore()


class FakeBase:
    def __init__(self):
        self.sent = None
        self.verified_called = False

    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def analytics_store():
        return FakeAnalytics()

    @staticmethod
    def account_store():
        return FakeAccounts()

    @staticmethod
    def scheduler():
        return FakeScheduler()

    @staticmethod
    def file_store():
        return FakeFiles()

    @staticmethod
    def context_engine():
        return FakeContext()

    @staticmethod
    def build_status():
        return {"ok": True, "version": "9.5.1", "new_mail_mcp_commands": 0}

    @staticmethod
    def _csrf_value():
        return "csrf-test"

    async def _verified_form(self, request):
        self.verified_called = True
        return {
            "to": "person@example.com",
            "subject": "Subject",
            "body": "Body",
            "track_opens": "1",
            "newsletter_mode": "1",
            "unsubscribe_url": "https://example.com/u",
            "unsubscribe_email": "unsubscribe@example.com",
            "one_click_unsubscribe": "1",
            "dsn_notify_success": "1",
        }, None

    def send_email(self, **kwargs):
        self.sent = kwargs
        return {"ok": True}


class V951RedesignTests(unittest.TestCase):
    def test_historical_windows_use_real_timestamps_and_all_time_is_unbounded(self):
        now = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)
        rows = [
            {"id": "recent", "observed_at": (now - timedelta(hours=12)).isoformat()},
            {"id": "week", "observed_at": (now - timedelta(days=6)).isoformat()},
            {"id": "old", "observed_at": (now - timedelta(days=120)).isoformat()},
            {"id": "missing", "observed_at": ""},
        ]
        assert parse_window("garbage") == "30d"
        assert window_cutoff("all", now=now) is None
        assert [row["id"] for row in filter_chronological_rows(rows, "1d", ("observed_at",), now=now)] == ["recent"]
        assert [row["id"] for row in filter_chronological_rows(rows, "7d", ("observed_at",), now=now)] == ["recent", "week"]
        assert [row["id"] for row in filter_chronological_rows(rows, "all", ("observed_at",), now=now)] == ["recent", "week", "old", "missing"]

    def test_dashboard_filters_activity_but_keeps_inventory_snapshots(self):
        base = FakeBase()
        one_day = render_overview(base, FakeCore(), request({"dashboard_window": "1d"}))
        all_time = render_overview(base, FakeCore(), request({"dashboard_window": "all"}))
        for html in (one_day, all_time):
            assert "Accounts</span><strong>2</strong>" in html
            assert "Tasks</span><strong>3</strong>" in html
            assert "Files</span><strong>7</strong>" in html
            assert "Knowledge chunks</span><strong>440</strong>" in html
            assert "not proof of human reading" in html
            assert "tracking alone does not imply newsletter" in html
        assert "Deliveries</span><strong>1</strong>" in one_day
        assert "Deliveries</span><strong>2</strong>" in all_time

    def test_tracking_range_uses_real_events_and_preserves_truthful_semantics(self):
        html = render_tracking_summary(FakeBase(), FakeCore(), request({"tracking_window": "1d"}))
        assert "Campaigns</span><strong>1</strong>" in html
        assert "Deliveries</span><strong>1</strong>" in html
        assert "not proof of human reading" in html
        assert "delivery_id + link_id + client_fingerprint" in html
        assert "tracking alone does not imply newsletter" in html

    def test_navigation_matches_approved_information_architecture(self):
        html = _nav_html()
        for label in (
            "Dashboard", "Accounts", "Mail Health", "Inbox", "Compose", "Tracking",
            "Deliveries", "Suppressions", "Projects", "Tasks", "Knowledge", "Files",
            "Security", "AMP", "System", "MCP Coverage",
        ):
            assert label in html
        assert 'data-tab="domains"' in html
        assert 'data-tab="recipients"' in html

    def test_compose_route_requires_verified_form_and_preserves_explicit_controls(self):
        base = FakeBase()
        response = asyncio.run(compose_send(base, request(method="POST")))
        assert base.verified_called is True
        assert response.status_code == 303
        assert base.sent is not None
        assert base.sent["track_opens"] is True
        assert base.sent["newsletter_mode"] is True
        assert base.sent["one_click_unsubscribe"] is True
        assert base.sent["dsn_notify_success"] is True

    def test_compose_copy_keeps_tracking_newsletter_semantics_explicit(self):
        html = render_compose(FakeBase(), request())
        assert "Tracking alone never enables unsubscribe headers" in html
        assert 'action="/dashboard/compose/send"' in html
        assert 'name="csrf"' in html

    def test_mcp_coverage_is_exactly_existing_90_functions(self):
        assert sum(len(items) for items in MCP_COVERAGE.values()) == 90
        html = render_coverage(FakeBase(), request())
        assert "Mapped MCP functions</span><strong>90</strong>" in html
        assert "New v9.5 mail command names</span><strong>0</strong>" in html

    def test_new_webgui_layer_is_provider_neutral_and_does_not_touch_mcp_registration(self):
        source = Path(__file__).resolve().parents[1] / "src" / "postmaster" / "webgui_v951.py"
        text = source.read_text(encoding="utf-8")
        for forbidden in (
            "host" + "inger",
            "g" + "mail",
            "out" + "look",
            "mcp.add_tool",
            "mcp.remove_tool",
            "@mcp.tool",
            "sqlite3",
            "migration",
        ):
            assert forbidden.casefold() not in text.casefold()
