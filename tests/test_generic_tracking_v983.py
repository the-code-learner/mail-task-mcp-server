from __future__ import annotations

import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import unittest

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.file_store import FileStore
from postmaster.generic_tracking_v983 import GenericTrackedLinkStoreV983
from postmaster.runtime_v982 import install_runtime_v982
from postmaster.runtime_v983 import (
    MCP_COMMAND_COUNT_V983,
    MCP_TRACKED_FILE_COMMANDS_V983,
    install_runtime_v983,
)


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def add_tool(self, fn, name=None, annotations=None):
        self.tools[name or fn.__name__] = (fn, annotations)

    def remove_tool(self, name):
        self.tools.pop(name, None)


class TrackedFileLinksV983Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_public = {
            key: os.environ.get(key)
            for key in ("PUBLIC_EMAIL_BASE_URL", "PUBLIC_MCP_HOST")
        }
        os.environ["PUBLIC_EMAIL_BASE_URL"] = "https://postmaster.example.test"
        os.environ["PUBLIC_MCP_HOST"] = ""

        self.analytics = EmailAnalyticsStore(
            db_path=str(root / "analytics.db"),
            key_path=str(root / "analytics.key"),
        )
        self.files = FileStore(
            db_path=str(root / "files.db"),
            root=str(root / "files"),
            max_bytes=1024 * 1024,
            max_total_bytes=10 * 1024 * 1024,
            max_files=100,
        )
        self.store = GenericTrackedLinkStoreV983(
            self.analytics,
            file_store_provider=lambda: self.files,
        )

    async def asyncTearDown(self):
        for key, value in self.old_public.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _save(
        self,
        *,
        owner="davide",
        project="postmaster-mcp",
        file_id="file-1",
        data=b"tracked file payload",
        filename="report.txt",
    ):
        return self.files.save_bytes(
            owner_id=owner,
            project_id=project,
            file_id=file_id,
            filename=filename,
            data=data,
            media_type="text/plain",
        )

    async def _runtime(self):
        async def legacy_tracking(request):
            return PlainTextResponse(
                "legacy:" + str(request.path_params.get("token") or ""),
                status_code=209,
            )

        app = Starlette(
            routes=[Route("/t/c/{token}", legacy_tracking, methods=["GET"])]
        )
        mcp = FakeMCP()
        scopes = []

        def require_scope(owner_id, project_id):
            scopes.append((owner_id, project_id))
            if owner_id != "davide":
                raise ValueError("scope denied")

        base = SimpleNamespace(
            analytics_store=lambda: self.analytics,
            file_store=lambda: self.files,
            _require_knowledge_scope=require_scope,
            logger=logging.getLogger("test-v983"),
        )
        core = SimpleNamespace(mcp=mcp)
        v982 = install_runtime_v982(
            app,
            base,
            core,
            lambda: {
                "ok": True,
                "version_capability": "9.8.1",
                "mcp_command_count_expected": 118,
            },
            store=self.store,
        )
        v983 = install_runtime_v983(
            app,
            base,
            core,
            v982["runtime_status"],
            store=self.store,
        )
        return app, base, core, scopes, v983

    async def test_create_file_link_returns_same_generic_surface_with_file_target(self):
        info = self._save()
        created = self.store.create_file(
            owner_id="davide",
            project_id="postmaster-mcp",
            file_id=info["id"],
            label="Quarterly report",
            idempotency_key="report-v1",
        )
        self.assertTrue(created["ok"])
        self.assertFalse(created["idempotent_replay"])
        self.assertEqual(created["target_type"], "stored_file")
        self.assertEqual(created["file_id"], "file-1")
        self.assertEqual(created["filename"], "report.txt")
        self.assertEqual(created["media_type"], "text/plain")
        self.assertEqual(created["size_bytes"], len(b"tracked file payload"))
        self.assertNotIn("destination_url", created)
        self.assertTrue(
            created["tracked_url"].startswith(
                "https://postmaster.example.test/t/c/glnk_"
            )
        )

        replay = self.store.create_file(
            owner_id="davide",
            project_id="postmaster-mcp",
            file_id=info["id"],
            label="Quarterly report",
            idempotency_key="report-v1",
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["id"], created["id"])
        self.assertEqual(replay["tracked_url"], created["tracked_url"])

        listed = self.store.list_links(
            owner_id="davide",
            project_id="postmaster-mcp",
        )
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["links"][0]["target_type"], "stored_file")
        self.assertEqual(listed["links"][0]["file_id"], "file-1")

        fetched = self.store.get(
            owner_id="davide",
            project_id="postmaster-mcp",
            tracked_link_id=created["id"],
        )
        self.assertEqual(fetched["target_type"], "stored_file")
        self.assertEqual(fetched["filename"], "report.txt")

    async def test_runtime_tool_serves_file_directly_records_click_and_revokes(self):
        info = self._save()
        app, base, core, scopes, v983 = await self._runtime()

        self.assertEqual(MCP_TRACKED_FILE_COMMANDS_V983, 1)
        self.assertEqual(MCP_COMMAND_COUNT_V983, 124)
        self.assertEqual(v983["mcp_command_count_expected"], 124)
        self.assertIn("create_tracked_file_link", core.mcp.tools)

        created = core.create_tracked_file_link(
            "davide",
            info["id"],
            "Download report",
            "postmaster-mcp",
            "download-report-v1",
        )
        self.assertTrue(created["ok"])
        self.assertEqual(scopes[-1], ("davide", "postmaster-mcp"))
        self.assertEqual(created["target_type"], "stored_file")

        endpoint = next(
            route.endpoint
            for route in app.router.routes
            if isinstance(route, Route) and route.path == "/t/c/{token}"
        )
        token = urlsplit(created["tracked_url"]).path.rsplit("/", 1)[-1]
        request = SimpleNamespace(
            method="GET",
            path_params={"token": token},
            headers={
                "user-agent": "Mozilla/5.0 Chrome/150",
                "x-forwarded-for": "203.0.113.55",
                "cf-ipcountry": "IT",
            },
            client=None,
        )
        response = await endpoint(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"tracked file payload")
        self.assertEqual(response.media_type, "text/plain")
        self.assertIn("report.txt", response.headers["content-disposition"])
        self.assertEqual(
            response.headers["cache-control"],
            "private, no-store, no-cache, max-age=0",
        )

        events = core.list_tracked_link_events(
            "davide",
            "postmaster-mcp",
            created["id"],
        )
        self.assertEqual(events["count"], 1)
        self.assertEqual(events["events"][0]["target_type"], "stored_file")
        self.assertEqual(events["events"][0]["file_id"], "file-1")
        self.assertEqual(events["events"][0]["filename"], "report.txt")
        self.assertNotIn("client_ip", events["events"][0])

        status = core.runtime_status()
        self.assertEqual(status["version_capability"], "9.8.3")
        self.assertEqual(status["mcp_command_count_expected"], 124)
        self.assertTrue(
            status["generic_tracked_links"]["stored_file_targets_supported"]
        )
        self.assertTrue(
            status["generic_tracked_links"][
                "stored_file_exact_incarnation_enforced"
            ]
        )

        revoked = core.revoke_tracked_link(
            "davide",
            created["id"],
            "postmaster-mcp",
            True,
        )
        self.assertTrue(revoked["ok"])
        after = await endpoint(request)
        self.assertEqual(after.status_code, 404)

    async def test_same_file_id_recreated_does_not_resurrect_old_link(self):
        info = self._save(file_id="stable-id", data=b"first incarnation")
        app, _, core, _, _ = await self._runtime()
        created = core.create_tracked_file_link(
            "davide",
            info["id"],
            "Stable id file",
            "postmaster-mcp",
        )
        endpoint = next(
            route.endpoint
            for route in app.router.routes
            if isinstance(route, Route) and route.path == "/t/c/{token}"
        )
        token = urlsplit(created["tracked_url"]).path.rsplit("/", 1)[-1]
        request = SimpleNamespace(
            method="GET",
            path_params={"token": token},
            headers={},
            client=None,
        )
        before = await endpoint(request)
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.body, b"first incarnation")

        self.files.delete("stable-id")
        time.sleep(0.002)
        self._save(file_id="stable-id", data=b"second incarnation")
        after = await endpoint(request)
        self.assertEqual(after.status_code, 404)

        # The click attempt is still telemetry, matching the existing tracked-email file path:
        # the server observed the old capability request even though delivery failed closed.
        events = core.list_tracked_link_events(
            "davide",
            "postmaster-mcp",
            created["id"],
        )
        self.assertEqual(events["count"], 2)

    async def test_file_scope_must_match_owner_and_not_cross_project(self):
        self._save(owner="davide", project="project-a", file_id="a")
        with self.assertRaises(Exception):
            self.store.create_file(
                owner_id="other",
                project_id="project-a",
                file_id="a",
                label="Wrong owner",
            )
        with self.assertRaises(Exception):
            self.store.create_file(
                owner_id="davide",
                project_id="project-b",
                file_id="a",
                label="Wrong project",
            )

        global_info = self._save(
            owner="davide",
            project=None,
            file_id="global-file",
            data=b"global",
        )
        created = self.store.create_file(
            owner_id="davide",
            project_id="project-a",
            file_id=global_info["id"],
            label="Global file in project context",
        )
        self.assertTrue(created["ok"])

    async def test_existing_url_and_legacy_tokens_delegate_unchanged(self):
        self._save()
        app, _, core, _, _ = await self._runtime()
        url_link = core.create_tracked_link(
            "davide",
            "https://example.com/landing",
            "Landing",
            "postmaster-mcp",
            "landing-v1",
        )
        endpoint = next(
            route.endpoint
            for route in app.router.routes
            if isinstance(route, Route) and route.path == "/t/c/{token}"
        )

        url_token = urlsplit(url_link["tracked_url"]).path.rsplit("/", 1)[-1]
        url_request = SimpleNamespace(
            method="GET",
            path_params={"token": url_token},
            headers={},
            client=None,
        )
        url_response = await endpoint(url_request)
        self.assertEqual(url_response.status_code, 302)
        self.assertEqual(
            url_response.headers["location"],
            "https://example.com/landing",
        )

        legacy_request = SimpleNamespace(
            method="GET",
            path_params={"token": "legacy-token"},
            headers={},
            client=None,
        )
        legacy_response = await endpoint(legacy_request)
        self.assertEqual(legacy_response.status_code, 209)
        self.assertEqual(legacy_response.body, b"legacy:legacy-token")


if __name__ == "__main__":
    unittest.main()
