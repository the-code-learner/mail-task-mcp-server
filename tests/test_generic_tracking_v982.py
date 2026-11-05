from __future__ import annotations

import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import urlsplit
import unittest

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.generic_tracking_v982 import (
    GENERIC_TRACKED_LINK_TOKEN_PREFIX,
    GenericTrackedLinkStore,
)
from postmaster.runtime_v982 import (
    MCP_COMMAND_COUNT_V982,
    MCP_GENERIC_TRACKED_LINK_COMMANDS_V982,
    install_runtime_v982,
)


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def add_tool(self, fn, name=None, annotations=None):
        self.tools[name or fn.__name__] = (fn, annotations)

    def remove_tool(self, name):
        self.tools.pop(name, None)


class GenericTrackedLinkStoreV982Tests(unittest.TestCase):
    def setUp(self):
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
        self.store = GenericTrackedLinkStore(self.analytics)

    def tearDown(self):
        for key, value in self.old_public.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_create_list_get_click_and_revoke(self):
        created = self.store.create(
            owner_id="davide",
            project_id="postmaster-mcp",
            destination_url="https://example.com/page?a=1#section",
            label="Docs CTA",
            idempotency_key="docs-cta-1",
        )
        self.assertTrue(created["ok"])
        self.assertFalse(created["idempotent_replay"])
        self.assertEqual(created["label"], "Docs CTA")
        self.assertEqual(created["project_id"], "postmaster-mcp")
        self.assertEqual(
            created["destination_url"],
            "https://example.com/page?a=1#section",
        )
        token = urlsplit(created["tracked_url"]).path.rsplit("/", 1)[-1]
        self.assertTrue(token.startswith(GENERIC_TRACKED_LINK_TOKEN_PREFIX))
        self.assertEqual(
            created["tracked_url"],
            f"https://postmaster.example.test/t/c/{token}",
        )

        replay = self.store.create(
            owner_id="davide",
            project_id="postmaster-mcp",
            destination_url="https://example.com/page?a=1#section",
            label="Docs CTA",
            idempotency_key="docs-cta-1",
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["id"], created["id"])
        self.assertEqual(replay["tracked_url"], created["tracked_url"])

        link = self.store.get_by_token(token)
        first = self.store.record_click(
            link,
            user_agent="Mozilla/5.0 Chrome/150.0.0.0",
            client_ip="203.0.113.10",
            country_code="IT",
        )
        second = self.store.record_click(
            link,
            user_agent="Mozilla/5.0 Chrome/150.0.0.0",
            client_ip="203.0.113.10",
            country_code="IT",
        )
        self.assertEqual(first["client_fingerprint"], second["client_fingerprint"])
        self.assertFalse(first["raw_ip_stored"])

        listed = self.store.list_links(
            owner_id="davide",
            project_id="postmaster-mcp",
            label="docs cta",
        )
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["links"][0]["total_clicks"], 2)
        self.assertEqual(listed["links"][0]["unique_clicks"], 1)
        self.assertEqual(listed["links"][0]["tracked_url"], created["tracked_url"])

        fetched = self.store.get(
            owner_id="davide",
            project_id="postmaster-mcp",
            tracked_link_id=created["id"],
        )
        self.assertEqual(fetched["unique_clicks"], 1)
        self.assertEqual(
            fetched["unique_click_definition"],
            "tracked_link_id + client_fingerprint",
        )

        events = self.store.list_events(
            owner_id="davide",
            project_id="postmaster-mcp",
            tracked_link_id=created["id"],
        )
        self.assertEqual(events["count"], 2)
        self.assertEqual(events["events"][0]["label"], "Docs CTA")
        self.assertNotIn("client_ip", events["events"][0])

        refused = None
        try:
            self.store.revoke(
                owner_id="davide",
                project_id="postmaster-mcp",
                tracked_link_id=created["id"],
            )
        except Exception as exc:
            refused = str(exc)
        self.assertIn("confirm=true", refused or "")

        revoked = self.store.revoke(
            owner_id="davide",
            project_id="postmaster-mcp",
            tracked_link_id=created["id"],
            confirm=True,
        )
        self.assertTrue(revoked["revoked"])
        with self.assertRaises(Exception):
            self.store.get_by_token(token)

        history = self.store.get(
            owner_id="davide",
            project_id="postmaster-mcp",
            tracked_link_id=created["id"],
        )
        self.assertEqual(history["status"], "revoked")
        self.assertEqual(history["total_clicks"], 2)

    def test_scope_and_destination_validation(self):
        created = self.store.create(
            owner_id="davide",
            project_id="one",
            destination_url="https://example.com/a",
            label="A",
        )
        with self.assertRaises(Exception):
            self.store.get(
                owner_id="other",
                tracked_link_id=created["id"],
            )
        with self.assertRaises(Exception):
            self.store.get(
                owner_id="davide",
                project_id="two",
                tracked_link_id=created["id"],
            )
        with self.assertRaises(Exception):
            self.store.create(
                owner_id="davide",
                destination_url="mailto:test@example.com",
                label="Mail",
            )
        with self.assertRaises(Exception):
            self.store.create(
                owner_id="davide",
                destination_url=created["tracked_url"],
                label="Double wrapped",
            )


class RuntimeV982Tests(unittest.IsolatedAsyncioTestCase):
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
        self.store = GenericTrackedLinkStore(self.analytics)

    async def asyncTearDown(self):
        for key, value in self.old_public.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    async def test_runtime_adds_five_tools_and_preserves_legacy_route(self):
        async def legacy_tracking(request):
            return PlainTextResponse(
                "legacy:" + str(request.path_params.get("token") or ""),
                status_code=209,
            )

        app = Starlette(
            routes=[
                Route("/t/c/{token}", legacy_tracking, methods=["GET"]),
            ]
        )
        mcp = FakeMCP()
        scopes = []

        def require_scope(owner_id, project_id):
            scopes.append((owner_id, project_id))
            if owner_id != "davide":
                raise ValueError("scope denied")

        base = SimpleNamespace(
            analytics_store=lambda: self.analytics,
            _require_knowledge_scope=require_scope,
            logger=logging.getLogger("test-v982"),
        )
        core = SimpleNamespace(mcp=mcp)

        result = install_runtime_v982(
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
        self.assertEqual(
            result["mcp_command_count_expected"],
            MCP_COMMAND_COUNT_V982,
        )
        self.assertEqual(MCP_GENERIC_TRACKED_LINK_COMMANDS_V982, 5)
        self.assertEqual(
            set(mcp.tools),
            {
                "create_tracked_link",
                "list_tracked_links",
                "get_tracked_link",
                "list_tracked_link_events",
                "revoke_tracked_link",
                "runtime_status",
            },
        )

        created = core.create_tracked_link(
            "davide",
            "https://example.com/landing",
            "Landing page",
            "postmaster-mcp",
            "landing-v1",
        )
        self.assertTrue(created["ok"])
        self.assertEqual(scopes[-1], ("davide", "postmaster-mcp"))
        self.assertTrue(created["tracked_url"].startswith("https://postmaster.example.test/t/c/glnk_"))

        status = core.runtime_status()
        self.assertEqual(status["version_capability"], "9.8.2")
        self.assertEqual(status["mcp_command_count_expected"], 123)
        self.assertTrue(status["generic_tracked_links"]["enabled"])
        self.assertTrue(status["generic_tracked_links"]["labels_required"])
        self.assertTrue(
            status["generic_tracked_links"]["email_tracking_semantics_unchanged"]
        )

        endpoint = next(
            route.endpoint
            for route in app.router.routes
            if isinstance(route, Route) and route.path == "/t/c/{token}"
        )
        token = urlsplit(created["tracked_url"]).path.rsplit("/", 1)[-1]
        generic_request = SimpleNamespace(
            path_params={"token": token},
            headers={
                "user-agent": "Mozilla/5.0 Chrome/150",
                "x-forwarded-for": "203.0.113.20",
                "cf-ipcountry": "IT",
            },
            client=None,
        )
        response = await endpoint(generic_request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "https://example.com/landing")

        events = core.list_tracked_link_events(
            "davide",
            "postmaster-mcp",
            created["id"],
        )
        self.assertEqual(events["count"], 1)

        legacy_request = SimpleNamespace(
            path_params={"token": "legacy-token"},
            headers={},
            client=None,
        )
        legacy_response = await endpoint(legacy_request)
        self.assertEqual(legacy_response.status_code, 209)
        self.assertEqual(legacy_response.body, b"legacy:legacy-token")

        no_confirm = core.revoke_tracked_link(
            "davide",
            created["id"],
            "postmaster-mcp",
            False,
        )
        self.assertFalse(no_confirm["ok"])
        revoked = core.revoke_tracked_link(
            "davide",
            created["id"],
            "postmaster-mcp",
            True,
        )
        self.assertTrue(revoked["ok"])
        response = await endpoint(generic_request)
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
