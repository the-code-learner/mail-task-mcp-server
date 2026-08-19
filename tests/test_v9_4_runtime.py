from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from starlette.routing import Mount, Route
from starlette.testclient import TestClient

class RuntimeRouteTests(unittest.TestCase):
    KEYS = (
        "SCHEDULER_DB_PATH", "CONTEXT_DB_PATH", "CONTEXT_SEMANTIC_ENABLED",
        "CONTEXT_MODEL_AUTO_DOWNLOAD", "CONTEXT_MODEL_AUTO_PREPARE",
        "FILE_STORE_DB_PATH", "FILE_STORE_ROOT", "MAIL_ACCOUNTS_DB_PATH",
        "MAIL_ACCOUNTS_KEY_PATH", "EMAIL_ANALYTICS_DB_PATH", "EMAIL_ANALYTICS_KEY_PATH",
        "RECIPIENT_POLICY_DB_PATH", "PUBLIC_EMAIL_BASE_URL", "PUBLIC_MCP_HOST",
        "POSTMASTER_REF", "POSTMASTER_VERSION",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = {key: os.environ.get(key) for key in self.KEYS}
        version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
        os.environ.update({
            "SCHEDULER_DB_PATH": str(root / "scheduler.db"),
            "CONTEXT_DB_PATH": str(root / "knowledge.db"),
            "CONTEXT_SEMANTIC_ENABLED": "false",
            "CONTEXT_MODEL_AUTO_DOWNLOAD": "false",
            "CONTEXT_MODEL_AUTO_PREPARE": "false",
            "FILE_STORE_DB_PATH": str(root / "files.db"),
            "FILE_STORE_ROOT": str(root / "files"),
            "MAIL_ACCOUNTS_DB_PATH": str(root / "accounts.db"),
            "MAIL_ACCOUNTS_KEY_PATH": str(root / "accounts.key"),
            "EMAIL_ANALYTICS_DB_PATH": str(root / "analytics.db"),
            "EMAIL_ANALYTICS_KEY_PATH": str(root / "analytics.key"),
            "RECIPIENT_POLICY_DB_PATH": str(root / "policy.db"),
            "PUBLIC_EMAIL_BASE_URL": "https://postmaster.example.test",
            "PUBLIC_MCP_HOST": "",
            "POSTMASTER_REF": f"v{version}-test",
            "POSTMASTER_VERSION": "latest",
        })
        from postmaster.email_analytics import analytics_store
        from postmaster.link_tracking import link_store
        analytics_store.cache_clear()
        link_store.cache_clear()
        import postmaster.runtime as runtime
        self.runtime = runtime
        runtime.analytics_store.cache_clear()
        runtime.link_store.cache_clear()
        for cached in (runtime.scheduler, runtime.context_engine, runtime.file_store, runtime.account_store, runtime.policy_client):
            cached.cache_clear()

    def tearDown(self) -> None:
        self.runtime.analytics_store.cache_clear()
        self.runtime.link_store.cache_clear()
        for cached in (self.runtime.scheduler, self.runtime.context_engine, self.runtime.file_store, self.runtime.account_store, self.runtime.policy_client):
            cached.cache_clear()
        for key, value in self.old.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        self.tmp.cleanup()

    def test_click_route_redirects_only_to_server_record_and_records_event(self) -> None:
        analytics = self.runtime.analytics_store()
        links = self.runtime.link_store()
        campaign = analytics.create_campaign(account_id="acct", sender="sender@example.test", subject="route", track_opens=True, amp_used=False)
        delivery = analytics.create_delivery(campaign_id=campaign["id"], account_id="acct", recipient="reader@example.test", recipient_role="to")
        destination = "https://destination.example/path?a=1&b=2#section"
        _, meta = links.instrument_html(body_html=f'<a href="{destination.replace("&", "&amp;")}">Destination</a>', delivery=delivery)
        with links._connect() as conn:
            token = str(conn.execute("SELECT tracking_token FROM tracking_links WHERE id=?", (meta[0]["occurrence_id"],)).fetchone()[0])
        with TestClient(self.runtime.app) as client:
            response = client.get(f"/t/c/{token}", follow_redirects=False, headers={"User-Agent":"Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36","X-Forwarded-For":"203.0.113.77","CF-IPCountry":"IT"})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["location"], destination)
            invalid = client.get("/t/c/not-a-token?url=https://evil.example/", follow_redirects=False)
            self.assertEqual(invalid.status_code, 404)
            self.assertNotIn("location", invalid.headers)
        events = links.list_click_events(delivery_id=delivery["id"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "link")
        self.assertEqual(events[0]["country_code"], "IT")
        self.assertEqual(events[0]["original_url"], destination)

    def test_public_route_surface_build_status_dashboard_and_no_secret_exposure(self) -> None:
        routes = [route for route in self.runtime.app.router.routes if isinstance(route, Route)]
        paths = {route.path for route in routes}
        self.assertIn("/track/open/{token}.gif", paths)
        self.assertIn("/api/amp/status", paths)
        self.assertIn("/t/c/{token}", paths)
        self.assertIn("/files/{file_id}", paths)
        self.assertEqual([p for p in paths if p.startswith("/t/c/")], ["/t/c/{token}"])
        self.assertTrue(any(isinstance(route, Mount) for route in self.runtime.app.router.routes))
        status = self.runtime.build_status()
        expected_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
        self.assertEqual(status["version"], expected_version)
        self.assertTrue(status["native_chatgpt_file_upload"])
        self.assertTrue(status["native_file_resource_handoff"])
        self.assertTrue(status["link_tracking"])
        self.assertTrue(status["sent_copy_tracking_sanitized"])
        analytics = self.runtime.analytics_store()
        links = self.runtime.link_store()
        campaign = analytics.create_campaign(account_id="acct", sender="sender@example.test", subject="safe", track_opens=True, amp_used=False)
        delivery = analytics.create_delivery(campaign_id=campaign["id"], account_id="acct", recipient="reader@example.test", recipient_role="to")
        _, meta = links.instrument_html(body_html='<a href="https://example.com">Example</a>', delivery=delivery)
        with links._connect() as conn:
            secret = str(conn.execute("SELECT tracking_token FROM tracking_links WHERE id=?", (meta[0]["occurrence_id"],)).fetchone()[0])
        self.assertNotIn(secret, repr(links.list_links(campaign_id=campaign["id"])))
        fragment = self.runtime._tracking_dashboard_fragment("acct")
        self.assertIn("Top links", fragment)
        self.assertIn("Tracking events", fragment)
        self.assertNotIn(secret, fragment)

    def test_cloudflare_preflight_is_documented_not_implemented_in_yaml(self) -> None:
        root = Path(__file__).resolve().parents[1]
        doc = (root / "docs" / "LINK_TRACKING.md").read_text(encoding="utf-8")
        self.assertIn("/t/c/*", doc)
        self.assertIn("/track/open/*", doc)
        self.assertIn("/api/amp/*", doc)
        self.assertIn("/mcp", doc)
        self.assertIn("Cloudflare Access", doc)
        yaml = (root / "postmaster-mcp.yml").read_text(encoding="utf-8")
        self.assertIn("POSTMASTER_VERSION", yaml)
        self.assertIn("POSTMASTER_CHECK_UPDATES_ON_START", yaml)
        self.assertNotIn("/t/c/*", yaml)
