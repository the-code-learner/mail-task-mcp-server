from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from postmaster import runtime_control
from postmaster import webgui_v951 as v951
from postmaster.runtime_v953 import install_runtime_v953
from postmaster.webgui_v953 import (
    _account_rows,
    _nav_html,
    account_color_map,
    install_webgui_v953,
    render_compose,
    render_inbox,
    render_mail_health,
    render_system,
    render_tracking_summary,
)


ACCOUNTS = [
    {
        "id": "alpha",
        "label": "Primary",
        "email_address": "primary@example.invalid",
        "enabled": True,
        "is_default": True,
        "imap_password_enc": "DO-NOT-RENDER-IMAP",
        "smtp_password_enc": "DO-NOT-RENDER-SMTP",
    },
    {
        "id": "beta",
        "label": "Support",
        "email_address": "support@example.invalid",
        "enabled": True,
        "is_default": False,
    },
    {
        "id": "disabled",
        "label": "Disabled",
        "email_address": "disabled@example.invalid",
        "enabled": False,
        "is_default": False,
    },
]


def request_for(query: str = "") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": query.encode("utf-8"),
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("localhost", 8000),
    })


class FakeAnalytics:
    def __init__(self):
        self.calls = []

    def _rows(self, kind, account_id):
        self.calls.append((kind, account_id))
        suffix = account_id or "all"
        if kind == "campaigns":
            return [{"id": f"campaign-{suffix}", "created_at": "2026-08-21T20:00:00+00:00"}]
        if kind == "deliveries":
            return [{"id": f"delivery-{suffix}", "sent_at": "2026-08-21T20:00:00+00:00"}]
        return [{"id": f"open-{suffix}", "opened_at": "2026-08-21T20:00:00+00:00"}]

    def list_campaigns(self, *, account_id=None, limit=500):
        return self._rows("campaigns", account_id)

    def list_deliveries(self, *, account_id=None, limit=1000):
        return self._rows("deliveries", account_id)

    def list_open_events(self, *, account_id=None, limit=1000):
        return self._rows("opens", account_id)


class FakeLinkStore:
    def unified_events(self, *, account_id=None, limit=1000):
        return [{
            "event_type": "link",
            "account_id": account_id or "all",
            "observed_at": "2026-08-21T20:00:00+00:00",
            "delivery_id": "d",
            "link_id": "l",
            "client_fingerprint": "f",
        }]


class FakeCore:
    def __init__(self):
        self._link_store = FakeLinkStore()

    def link_store(self):
        return self._link_store


class FakeStore:
    def status(self):
        return {"ok": True, "healthy": True, "feature_flag": True}


class FakeScheduler:
    def status(self):
        return {"ok": True, "mode": "task_registry_only", "worker": False}


class FakeBase:
    def __init__(self):
        self.verified_calls = 0
        self.health_calls = []
        self.search_calls = []
        self.get_calls = []
        self.send_calls = []
        self.mailbox_calls = []
        self.analytics = FakeAnalytics()

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

    def list_email_accounts(self):
        return {"ok": True, "accounts": [dict(row) for row in ACCOUNTS]}

    def list_mailboxes(self, *, account_id=None):
        self.mailbox_calls.append(account_id)
        if account_id == "beta":
            return ["INBOX", "Support", "Archive"]
        return ["INBOX", "PrimaryOnly", "Archive"]

    def test_email_account(self, **kwargs):
        self.health_calls.append(kwargs)
        return {"ok": True, "smtp": {"ok": True}, "imap": {"ok": True}, "dns": {}, "tls": {}}

    def search_emails(self, **kwargs):
        self.search_calls.append(kwargs)
        return [{
            "uid": "42",
            "from": "sender@example.invalid",
            "subject": "Fixture",
            "date": "2026-08-21T20:00:00+00:00",
        }]

    def get_email(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"uid": kwargs["uid"], "subject": "Fixture", "body_text": "Message body"}

    def send_email(self, **kwargs):
        self.send_calls.append(kwargs)
        return {"ok": True}

    def analytics_store(self):
        return self.analytics

    def tracking_status(self):
        return {"ok": True, "unique_click_definition": "delivery_id + link_id + client_fingerprint"}

    def context_engine(self):
        return FakeStore()

    def file_store(self):
        return FakeStore()

    def scheduler(self):
        return FakeScheduler()

    def build_status(self):
        return {
            "ok": True,
            "version": "9.5.3",
            "build": "v9.5.3",
            "requested_version": "latest",
            "latest_version": "9.5.3",
            "update_available": False,
            "update_check_status": "ok",
            "mail_standards_v950": True,
            "smtp_capability_discovery": True,
            "new_mail_mcp_commands": 0,
        }


class V953AccountUxTests(unittest.TestCase):
    def test_account_rows_are_enabled_and_selects_render_human_labels_without_credentials(self):
        base = FakeBase()
        rows = _account_rows(base)
        assert [_row["id"] for _row in rows] == ["alpha", "beta"]
        html = render_compose(base, request_for("ui_view=compose"))
        assert '<select name="account_id">' in html
        assert 'value="alpha" selected' in html
        assert "Primary — primary@example.invalid (default)" in html
        assert "Support — support@example.invalid" in html
        assert "disabled@example.invalid" not in html
        assert "DO-NOT-RENDER-IMAP" not in html
        assert "DO-NOT-RENDER-SMTP" not in html

    def test_inbox_initial_view_auto_loads_default_account_and_real_mailboxes(self):
        base = FakeBase()
        html = render_inbox(base, request_for("ui_view=inbox"))
        assert base.search_calls == [{
            "mailbox": "INBOX",
            "subject": None,
            "text": None,
            "unread_only": False,
            "since_days": 90,
            "limit": 50,
            "account_id": "alpha",
        }]
        assert base.mailbox_calls == ["alpha"]
        assert '<select name="account_id">' in html
        assert '<select name="mailbox">' in html
        assert 'value="PrimaryOnly"' in html
        assert ">View</a>" in html
        assert "Run a search to load Inbox results" not in html

    def test_inbox_account_switch_resets_mailbox_that_does_not_exist_on_new_account(self):
        base = FakeBase()
        html = render_inbox(base, request_for("ui_view=inbox&account_id=beta&mailbox=PrimaryOnly&message_uid=42"))
        assert base.mailbox_calls == ["beta"]
        assert base.search_calls[-1]["account_id"] == "beta"
        assert base.search_calls[-1]["mailbox"] == "INBOX"
        assert base.get_calls == []
        assert 'value="beta" selected' in html
        assert 'value="INBOX" selected' in html

    def test_inbox_search_detail_and_back_keep_selected_account_and_filters(self):
        base = FakeBase()
        html = render_inbox(base, request_for(
            "ui_view=inbox&inbox_search=1&account_id=beta&mailbox=Archive&subject=Hello&text=Needle&since_days=12&unread_only=1&message_uid=42"
        ))
        assert base.get_calls == [{"mailbox": "Archive", "uid": "42", "account_id": "beta"}]
        back = html.split('← Back to results</a>', 1)[0].rsplit('href="', 1)[1].split('"', 1)[0]
        assert "message_uid=" not in back
        for expected in ("account_id=beta", "mailbox=Archive", "subject=Hello", "text=Needle", "since_days=12", "unread_only=1"):
            assert expected in back

    def test_mail_health_uses_all_accounts_and_default_selection(self):
        base = FakeBase()
        html = render_mail_health(base, request_for("ui_view=mail-health"))
        assert '<select name="account_id">' in html
        assert 'value="alpha" selected' in html
        assert 'value="beta"' in html
        assert 'name="account_id" value="alpha"' in html
        assert base.health_calls == []

    def test_compose_selected_account_still_forwards_through_existing_send_semantics(self):
        base = FakeBase()

        async def route(request):
            return await v951.compose_send(base, request)

        app = Starlette(routes=[Route("/dashboard/compose/send", route, methods=["POST"])])
        with TestClient(app) as client:
            response = client.post(
                "/dashboard/compose/send",
                data={
                    "csrf": "csrf-test",
                    "account_id": "beta",
                    "to": "person@example.invalid",
                    "subject": "Subject",
                    "body": "Body",
                    "track_opens": "1",
                    "newsletter_mode": "1",
                    "dsn_notify_success": "1",
                },
                follow_redirects=False,
            )
        assert response.status_code == 303
        call = base.send_calls[0]
        assert call["account_id"] == "beta"
        assert call["track_opens"] is True
        assert call["newsletter_mode"] is True
        assert call["dsn_notify_success"] is True


class V953TrackingTests(unittest.TestCase):
    def test_account_colors_are_deterministic_distinct_and_text_labels_remain(self):
        first = account_color_map(ACCOUNTS[:2])
        second = account_color_map(list(reversed(ACCOUNTS[:2])))
        assert first == second
        assert first["alpha"] != first["beta"]
        html = render_tracking_summary(FakeBase(), FakeCore(), request_for("ui_view=tracking"))
        assert "Primary — primary@example.invalid" in html
        assert "Support — support@example.invalid" in html
        assert first["alpha"] in html
        assert first["beta"] in html
        assert "delivery_id + link_id + client_fingerprint" in html
        assert "query-time interpretation" in html

    def test_tracking_render_does_not_mutate_or_replace_raw_store_semantics(self):
        base = FakeBase()
        render_tracking_summary(base, FakeCore(), request_for("ui_view=tracking&account=beta"))
        assert ("deliveries", "beta") in base.analytics.calls
        assert all(kind in {"campaigns", "deliveries", "opens"} for kind, _ in base.analytics.calls)


class V953VersionCleanupTests(unittest.TestCase):
    def test_sidebar_has_no_decorative_release_version(self):
        html = _nav_html()
        assert "WebGUI v" not in html
        assert "v9.5.1" not in html
        assert "Operator console" in html

    def test_tracking_stale_release_labels_are_removed_by_patch_source(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "postmaster" / "webgui_v953.py").read_text(encoding="utf-8")
        assert 'replace("v9.4.1 query-time heuristic", "query-time heuristic")' in source
        assert 'replace("v9.4 click analytics", "click analytics")' in source


class V953RuntimeControlTests(unittest.TestCase):
    def test_stable_release_filter_excludes_draft_prerelease_and_model_releases(self):
        payload = [
            {"tag_name": "v9.5.3", "draft": False, "prerelease": False},
            {"tag_name": "v9.5.2", "draft": False, "prerelease": False},
            {"tag_name": "v10.0.0-rc1", "draft": False, "prerelease": True},
            {"tag_name": "v9.6.0", "draft": True, "prerelease": False},
            {"tag_name": "context-model-v1", "draft": False, "prerelease": False},
        ]
        assert runtime_control.parse_stable_release_tags(payload) == ["v9.5.3", "v9.5.2"]

    def test_control_state_is_atomic_stable_only_and_contains_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "control.json"
            runtime_control.write_control(selector="latest", check_updates_once=True, path=path)
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data == {"check_updates_once": True, "selector": "latest"}
            assert "password" not in path.read_text(encoding="utf-8").casefold()
            runtime_control.write_control(selector="9.5.2", restart_ref_once="v9.5.3", path=path)
            assert runtime_control.read_control(path) == {"selector": "v9.5.2", "restart_ref_once": "v9.5.3"}
            with self.assertRaises(ValueError):
                runtime_control.write_control(selector="main", path=path)

    @patch("postmaster.runtime_control.os.kill")
    @patch("postmaster.runtime_control.os.getpid", return_value=321)
    def test_restart_trigger_terminates_only_current_process(self, _pid, kill):
        runtime_control.terminate_current_process()
        kill.assert_called_once_with(321, 15)

    def test_runtime_build_status_reports_effective_requested_selector_without_new_mcp_name(self):
        class FakeMCP:
            def __init__(self):
                self.removed = []
                self.added = []
            def remove_tool(self, name):
                self.removed.append(name)
            def add_tool(self, fn, name):
                self.added.append(name)

        class Holder:
            pass

        base = Holder()
        core = Holder()
        core.mcp = FakeMCP()
        with patch.dict(os.environ, {"POSTMASTER_VERSION": "latest", "POSTMASTER_REQUESTED_VERSION": "v9.5.2"}, clear=False):
            build_status = install_runtime_v953(base, core, lambda: {"version": "9.5.3", "build": "v9.5.3"})
            status = build_status()
        assert status["requested_version"] == "v9.5.2"
        assert core.mcp.removed == ["build_status"]
        assert core.mcp.added == ["build_status"]
        assert status["new_mail_mcp_commands"] == 0

    def _make_system_app(self, tmp: str):
        base = FakeBase()
        core = FakeCore()
        restarted = []

        async def root(_request):
            return HTMLResponse("<html><style></style><main></main></html>")

        fallback = Starlette(routes=[])
        app = Starlette(routes=[Route("/", root, methods=["GET"]), Mount("/", app=fallback)])
        install_webgui_v953(app, base, core, root, restart_callback=lambda: restarted.append(True))
        return app, base, restarted

    def test_system_mutations_are_post_csrf_route_before_mount_and_restart_current_is_one_shot(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"POSTMASTER_RUNTIME_CONTROL_PATH": str(Path(tmp) / "control.json")}, clear=False):
            app, base, restarted = self._make_system_app(tmp)
            routes = app.router.routes
            mount_index = next(i for i, route in enumerate(routes) if isinstance(route, Mount))
            runtime_index = next(i for i, route in enumerate(routes) if isinstance(route, Route) and route.path == "/dashboard/system/runtime")
            assert runtime_index < mount_index
            with TestClient(app) as client:
                get_response = client.get("/dashboard/system/runtime", follow_redirects=False)
                assert get_response.status_code == 405
                bad = client.post("/dashboard/system/runtime", data={"csrf": "wrong", "action": "restart-current"}, follow_redirects=False)
                assert bad.status_code == 403
                assert restarted == []
                good = client.post("/dashboard/system/runtime", data={"csrf": "csrf-test", "action": "restart-current"}, follow_redirects=False)
                assert good.status_code == 303
            assert restarted == [True]
            state = runtime_control.read_control(Path(tmp) / "control.json")
            assert state["selector"] == "latest"
            assert state["restart_ref_once"] == "v9.5.3"
            assert base.verified_calls == 2

    def test_latest_update_sets_persistent_latest_and_one_shot_update_check(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"POSTMASTER_RUNTIME_CONTROL_PATH": str(Path(tmp) / "control.json")}, clear=False):
            app, _, restarted = self._make_system_app(tmp)
            with TestClient(app) as client:
                response = client.post("/dashboard/system/runtime", data={"csrf": "csrf-test", "action": "update-latest"}, follow_redirects=False)
            assert response.status_code == 303
            assert restarted == [True]
            assert runtime_control.read_control(Path(tmp) / "control.json") == {"selector": "latest", "check_updates_once": True}

    def test_explicit_downgrade_requires_confirmation_and_stable_release_membership(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"POSTMASTER_RUNTIME_CONTROL_PATH": str(Path(tmp) / "control.json")}, clear=False), patch(
            "postmaster.webgui_v953.runtime_control.stable_release_tags", return_value=(["v9.5.3", "v9.5.2"], "ok")
        ):
            app, _, restarted = self._make_system_app(tmp)
            with TestClient(app) as client:
                rejected = client.post(
                    "/dashboard/system/runtime",
                    data={"csrf": "csrf-test", "action": "switch-version", "version": "v9.5.2"},
                    follow_redirects=False,
                )
                assert "downgrade+requires+explicit+confirmation" in rejected.headers["location"]
                assert restarted == []
                accepted = client.post(
                    "/dashboard/system/runtime",
                    data={"csrf": "csrf-test", "action": "switch-version", "version": "v9.5.2", "confirm_downgrade": "yes"},
                    follow_redirects=False,
                )
            assert accepted.status_code == 303
            assert restarted == [True]
            assert runtime_control.read_control(Path(tmp) / "control.json") == {"selector": "v9.5.2"}

    def test_system_primary_view_is_concise_and_low_level_flags_are_advanced(self):
        base = FakeBase()
        with patch("postmaster.webgui_v953.runtime_control.stable_release_tags", return_value=(["v9.5.3", "v9.5.2"], "ok")):
            html = render_system(base, request_for("ui_view=system"))
        assert "Running version" in html
        assert "Concrete build/ref" in html
        assert "Requested selector" in html
        assert "Latest stable" in html
        assert "Advanced diagnostics" in html
        assert "Module/configuration controls" in html
        assert "Downgrades can be unsafe" in html
        assert 'method="post" action="/dashboard/system/runtime"' in html
        assert "Docker socket" not in html
        assert "Portainer credentials" in html

    def test_bootstrap_reuses_single_yaml_restart_policy_and_runtime_control_without_privileged_access(self):
        root = Path(__file__).resolve().parents[1]
        yaml = (root / "postmaster-mcp.yml").read_text(encoding="utf-8")
        start = (root / "scripts" / "start.sh").read_text(encoding="utf-8")
        assert "restart: unless-stopped" in yaml
        assert ".postmaster-runtime-control.json" in yaml
        assert "CONTROL_RESTART_REF" in yaml
        assert "CONTROL_CHECK_ONCE" in yaml
        assert "draft" in yaml and "prerelease" in yaml
        assert "context-model-v1" in yaml
        assert "docker.sock" not in yaml
        assert "PORTAINER" not in yaml
        assert "CLOUDFLARE" not in yaml
        assert 'exec "$VENV_DIR/bin/python" -m postmaster.runtime' in start

    def test_patch_keeps_mcp_surface_and_protocol_backends_out_of_webgui(self):
        root = Path(__file__).resolve().parents[1]
        webgui = (root / "src" / "postmaster" / "webgui_v953.py").read_text(encoding="utf-8").casefold()
        for forbidden in ("mcp.add_tool", "@mcp.tool", "imaplib", "smtplib", "sqlite3", "docker.sock"):
            assert forbidden not in webgui
        assert sum(len(items) for items in v951.MCP_COVERAGE.values()) == 90
