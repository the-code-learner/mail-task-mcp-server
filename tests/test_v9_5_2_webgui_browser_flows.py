from __future__ import annotations

import unittest
from pathlib import Path
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from postmaster import webgui_v951 as v951
from postmaster.webgui_v952 import (
    _inject_project_filter_views,
    dashboard_url,
    install_webgui_v952,
)


class FakeBase:
    def __init__(self):
        self.verified_calls = 0
        self.health_calls = []
        self.search_calls = []
        self.get_calls = []

    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _csrf_value():
        return "csrf-test"

    async def _verified_form(self, request):
        self.verified_calls += 1
        raw = (await request.body()).decode("utf-8")
        parsed = {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}
        if parsed.get("csrf") != "csrf-test":
            return None, PlainTextResponse("Invalid CSRF token", status_code=403)
        return parsed, None

    def test_email_account(self, **kwargs):
        self.health_calls.append(kwargs)
        return {
            "ok": True,
            "smtp": {"ok": True},
            "imap": {"ok": True},
            "dns": {"spf": "observed"},
            "tls": {"ok": True},
            "errors": [],
        }

    def search_emails(self, **kwargs):
        self.search_calls.append(kwargs)
        return [
            {
                "uid": "42",
                "from": "sender@example.invalid",
                "subject": "Fixture result",
                "date": "2026-08-21T18:00:00+00:00",
            }
        ]

    def get_email(self, **kwargs):
        self.get_calls.append(kwargs)
        return {
            "uid": kwargs["uid"],
            "subject": "Fixture result",
            "body_text": "Existing parsed message body",
            "authentication_results": {"spf": "pass"},
        }


class FakeCore:
    pass


def make_app():
    base = FakeBase()

    async def root(request: Request):
        return HTMLResponse(v951.render_mail_health(base, request) + v951.render_inbox(base, request))

    fallback = Starlette(routes=[])
    app = Starlette(routes=[Route("/", root, methods=["GET"]), Mount("/", app=fallback)])
    install_webgui_v952(app, base, root)
    return app, base


class V952BrowserFlowTests(unittest.TestCase):
    def test_forms_use_post_csrf_and_canonical_inbox_fields(self):
        app, _ = make_app()
        with TestClient(app) as client:
            html = client.get("/?ui_view=mail-health#mail-health").text
        assert 'method="post" action="/dashboard/mail-health/refresh"' in html
        assert 'name="csrf" value="csrf-test"' in html
        assert 'method="get" action="/dashboard/inbox/search"' in html
        for field in ("account_id", "mailbox", "subject", "text", "since_days", "unread_only"):
            assert f'name="{field}"' in html

    def test_browser_routes_are_registered_before_catch_all_mount(self):
        app, _ = make_app()
        routes = app.router.routes
        mount_index = next(i for i, route in enumerate(routes) if isinstance(route, Mount))
        matches = [
            i for i, route in enumerate(routes)
            if isinstance(route, Route)
            and route.path in {"/dashboard/mail-health/refresh", "/dashboard/inbox/search"}
        ]
        assert matches
        assert all(index < mount_index for index in matches)

    def test_mail_health_post_verifies_csrf_refreshes_existing_path_and_returns_to_view(self):
        app, base = make_app()
        with TestClient(app) as client:
            response = client.post(
                "/dashboard/mail-health/refresh",
                data={"csrf": "csrf-test", "account_id": "acct", "dkim_selector": "sel"},
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert response.headers["location"].endswith("#mail-health")
            assert "health_refreshed=1" in response.headers["location"]
            assert base.verified_calls == 1
            assert base.health_calls == [
                {"account_id": "acct", "refresh": True, "dkim_selector": "sel"}
            ]
            rendered = client.get(response.headers["location"])
            assert rendered.status_code == 200
            assert "Mail Health diagnostics refreshed" in rendered.text
            assert base.health_calls[-1] == {
                "account_id": "acct", "refresh": False, "dkim_selector": "sel"
            }

    def test_mail_health_invalid_csrf_is_rejected_without_refresh(self):
        app, base = make_app()
        with TestClient(app) as client:
            response = client.post(
                "/dashboard/mail-health/refresh",
                data={"csrf": "wrong"},
                follow_redirects=False,
            )
        assert response.status_code == 403
        assert base.verified_calls == 1
        assert base.health_calls == []

    def test_mail_health_get_is_defensive_redirect_and_never_executes_refresh(self):
        app, base = make_app()
        with TestClient(app) as client:
            response = client.get(
                "/dashboard/mail-health/refresh?account_id=acct&dkim_selector=sel",
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert response.headers["location"].endswith("#mail-health")
        assert "health_snapshot" not in response.headers["location"]
        assert base.health_calls == []

    def test_inbox_search_forwards_real_existing_search_arguments_and_renders_uid(self):
        app, base = make_app()
        with TestClient(app) as client:
            redirect = client.get(
                "/dashboard/inbox/search?account_id=acct&mailbox=Archive&subject=Hello&text=Needle&since_days=12&unread_only=1&project=",
                follow_redirects=False,
            )
            assert redirect.status_code == 303
            location = redirect.headers["location"]
            assert location.endswith("#inbox")
            assert "project=" not in location
            assert location.index("?") < location.index("#")
            response = client.get(location)
        assert response.status_code == 200
        assert base.search_calls == [{
            "mailbox": "Archive",
            "subject": "Hello",
            "text": "Needle",
            "unread_only": True,
            "since_days": 12,
            "limit": 50,
            "account_id": "acct",
        }]
        assert "42" in response.text
        assert ">View</a>" in response.text

    def test_inbox_detail_uses_existing_get_path_and_back_preserves_search_state(self):
        app, base = make_app()
        location = (
            "/?ui_view=inbox&inbox_search=1&account_id=acct&mailbox=Archive&subject=Hello"
            "&text=Needle&since_days=12&unread_only=1&message_uid=42#inbox"
        )
        with TestClient(app) as client:
            response = client.get(location)
        assert response.status_code == 200
        assert base.get_calls == [{"mailbox": "Archive", "uid": "42", "account_id": "acct"}]
        assert "Existing parsed message body" in response.text
        assert "Back to results" in response.text
        back = response.text.split('← Back to results</a>', 1)[0].rsplit('href="', 1)[1].split('"', 1)[0]
        assert "message_uid=" not in back
        for expected in (
            "account_id=acct", "mailbox=Archive", "subject=Hello", "text=Needle",
            "since_days=12", "unread_only=1", "inbox_search=1",
        ):
            assert expected in back
        assert "project=" not in back
        assert back.index("?") < back.index("#")

    def test_url_builder_drops_empty_or_irrelevant_project_and_preserves_project_scoped_views(self):
        request = Request({
            "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/", "raw_path": b"/",
            "query_string": b"project=&account=acct", "headers": [],
            "client": ("127.0.0.1", 1234), "server": ("localhost", 8000),
        })
        inbox = dashboard_url(request, view="inbox")
        assert "project=" not in inbox
        assert inbox.endswith("#inbox")

        scoped = Request({
            "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/", "raw_path": b"/",
            "query_string": b"project=proj-1&account=acct", "headers": [],
            "client": ("127.0.0.1", 1234), "server": ("localhost", 8000),
        })
        assert "project=proj-1" not in dashboard_url(scoped, view="inbox")
        assert "project=proj-1" in dashboard_url(scoped, view="scheduler")

    def test_project_filter_forms_preserve_their_selected_view(self):
        sample = (
            '<section class="tab-panel" id="panel-scheduler" data-panel="scheduler">'
            '<form class="project-filter" method="get" action="/"><select name="project"></select></form>'
            '<section class="tab-panel" id="panel-inbox" data-panel="inbox"></section>'
        )
        rendered = _inject_project_filter_views(sample)
        assert 'name="ui_view" value="scheduler"' in rendered

    def test_patch_layer_does_not_add_mcp_or_protocol_backend_implementation(self):
        source = Path(__file__).resolve().parents[1] / "src" / "postmaster" / "webgui_v952.py"
        text = source.read_text(encoding="utf-8").casefold()
        for forbidden in ("mcp.add_tool", "mcp.remove_tool", "@mcp.tool", "imaplib", "sqlite3", "migration"):
            assert forbidden not in text
        assert sum(len(items) for items in v951.MCP_COVERAGE.values()) == 90
