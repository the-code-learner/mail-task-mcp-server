from __future__ import annotations

import asyncio
import inspect
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from postmaster import update_status
from postmaster.runtime_v946 import install_runtime_v946


class UpdateStatusCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        update_status.reset_update_cache()

    def tearDown(self) -> None:
        update_status.reset_update_cache()

    def test_first_request_refreshes_second_within_60_seconds_uses_cache(self) -> None:
        fetch = Mock(return_value="9.4.6")
        with patch.object(update_status, "_fetch_latest_stable_version", fetch), patch.object(
            update_status.time, "monotonic", side_effect=[100.0, 159.999]
        ):
            first = update_status.latest_version_status("9.4.6")
            second = update_status.latest_version_status("9.4.6")
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(first["update_available"])
        self.assertFalse(second["update_available"])
        self.assertEqual(second["latest_version"], "9.4.6")
        self.assertEqual(second["update_cache_ttl_seconds"], 60)

    def test_first_request_at_or_after_ttl_refreshes_without_restart(self) -> None:
        fetch = Mock(side_effect=["9.4.6", "9.4.7"])
        with patch.object(update_status, "_fetch_latest_stable_version", fetch), patch.object(
            update_status.time, "monotonic", side_effect=[100.0, 160.0]
        ):
            current = update_status.latest_version_status("9.4.6")
            newer = update_status.latest_version_status("9.4.6")
        self.assertEqual(fetch.call_count, 2)
        self.assertFalse(current["update_available"])
        self.assertTrue(newer["update_available"])
        self.assertEqual(newer["latest_version"], "9.4.7")

    def test_remote_failure_preserves_stale_latest_version_and_does_not_false_negative(self) -> None:
        fetch = Mock(side_effect=["9.4.7", OSError("network down")])
        with patch.object(update_status, "_fetch_latest_stable_version", fetch), patch.object(
            update_status.time, "monotonic", side_effect=[100.0, 161.0]
        ):
            good = update_status.latest_version_status("9.4.6")
            failed = update_status.latest_version_status("9.4.6")
        self.assertEqual(good["update_check_status"], "ok")
        self.assertEqual(failed["update_check_status"], "error")
        self.assertEqual(failed["latest_version"], "9.4.7")
        self.assertTrue(failed["update_available"])
        self.assertEqual(failed["update_checked_at"], good["update_checked_at"])

    def test_remote_failure_without_previous_value_is_unknown_not_false(self) -> None:
        with patch.object(
            update_status, "_fetch_latest_stable_version", side_effect=OSError("offline")
        ), patch.object(update_status.time, "monotonic", return_value=100.0):
            failed = update_status.latest_version_status("9.4.6")
        self.assertEqual(failed["update_check_status"], "error")
        self.assertIsNone(failed["latest_version"])
        self.assertIsNone(failed["update_available"])

    def test_pinned_deployment_still_reports_newer_stable_release(self) -> None:
        with patch.dict(os.environ, {"POSTMASTER_VERSION": "9.4.6"}), patch.object(
            update_status, "_fetch_latest_stable_version", return_value="9.4.7"
        ), patch.object(update_status.time, "monotonic", return_value=100.0):
            status = update_status.latest_version_status("9.4.6")
        self.assertEqual(status["latest_version"], "9.4.7")
        self.assertTrue(status["update_available"])


class _FakeMCP:
    def __init__(self) -> None:
        self.removed: list[str] = []
        self.added: list[str] = []

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)

    def add_tool(self, fn, *, name: str) -> None:
        self.added.append(name)


class RuntimeUpdateIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        update_status.reset_update_cache()

    def tearDown(self) -> None:
        update_status.reset_update_cache()

    def test_build_status_and_webgui_footer_share_one_lazy_cache(self) -> None:
        async def legacy_dashboard(request):
            return HTMLResponse("<html><body><main><h1>Postmaster</h1></main></body></html>")

        async def old_tracking(request):
            return HTMLResponse("old")

        app = Starlette(
            routes=[
                Route("/", legacy_dashboard, methods=["GET"]),
                Route("/t/c/{token}", old_tracking, methods=["GET"]),
            ]
        )
        fake_mcp = _FakeMCP()
        core = SimpleNamespace(
            mcp=fake_mcp,
            build_status=lambda: {
                "ok": True,
                "version": "9.4.6",
                "build": "v9.4.6",
                "requested_version": "v9.4.6",
            },
            mail_client=lambda account_id=None: None,
            link_store=lambda: None,
        )
        base = SimpleNamespace(
            account_store=lambda: None,
            file_store=lambda: None,
            _require_knowledge_scope=lambda owner_id, project_id=None: None,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None),
            build_status=core.build_status,
            mail_client=core.mail_client,
        )
        fetch = Mock(return_value="9.4.6")
        with patch.object(update_status, "_fetch_latest_stable_version", fetch), patch.object(
            update_status.time, "monotonic", side_effect=[100.0, 120.0]
        ):
            dashboard, build_status, _ = install_runtime_v946(app, base, core, legacy_dashboard)
            status = build_status()
            request = Request(
                {
                    "type": "http",
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "https",
                    "path": "/",
                    "raw_path": b"/",
                    "query_string": b"",
                    "headers": [],
                    "client": ("127.0.0.1", 1234),
                    "server": ("example.test", 443),
                }
            )
            response = asyncio.run(dashboard(request))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(status["latest_version"], "9.4.6")
        self.assertFalse(status["update_available"])
        self.assertEqual(status["update_check_status"], "ok")
        self.assertIn("Postmaster v9.4.6 · Up to date", response.body.decode("utf-8"))
        self.assertEqual(fake_mcp.removed, ["build_status"])
        self.assertEqual(fake_mcp.added, ["build_status"])
        self.assertEqual(len(inspect.signature(build_status).parameters), 0)

    def test_footer_does_not_claim_up_to_date_after_failed_refresh(self) -> None:
        from postmaster.runtime_v946 import _footer_html

        footer = _footer_html(
            {
                "version": "9.4.6",
                "latest_version": "9.4.6",
                "update_available": False,
                "update_check_status": "error",
            }
        )
        self.assertIn("Update check unavailable", footer)
        self.assertNotIn("Up to date", footer)


if __name__ == "__main__":
    unittest.main()
