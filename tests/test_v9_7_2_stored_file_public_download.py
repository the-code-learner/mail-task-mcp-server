from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from starlette.routing import Route
from starlette.testclient import TestClient

from postmaster.file_handoff import build_signed_file_url


class V972StoredFilePublicDownloadTests(unittest.TestCase):
    KEYS = (
        "SCHEDULER_DB_PATH",
        "CONTEXT_DB_PATH",
        "CONTEXT_SEMANTIC_ENABLED",
        "CONTEXT_MODEL_AUTO_DOWNLOAD",
        "CONTEXT_MODEL_AUTO_PREPARE",
        "FILE_STORE_DB_PATH",
        "FILE_STORE_ROOT",
        "FILE_STORE_MAX_BYTES",
        "FILE_STORE_MAX_TOTAL_BYTES",
        "FILE_STORE_MAX_FILES",
        "FILE_STORE_TEXT_MAX_CHARS",
        "FILE_STORE_PUBLIC_BASE_URL",
        "FILE_STORE_DOWNLOAD_SECRET",
        "FILE_STORE_DOWNLOAD_URL_TTL_SECONDS",
        "PUBLIC_EMAIL_BASE_URL",
        "PUBLIC_MCP_HOST",
        "MAIL_ACCOUNTS_DB_PATH",
        "MAIL_ACCOUNTS_KEY_PATH",
        "EMAIL_ANALYTICS_DB_PATH",
        "EMAIL_ANALYTICS_KEY_PATH",
        "RECIPIENT_POLICY_DB_PATH",
        "POSTMASTER_REF",
        "POSTMASTER_VERSION",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = {key: os.environ.get(key) for key in self.KEYS}
        os.environ.update(
            {
                "SCHEDULER_DB_PATH": str(root / "scheduler.db"),
                "CONTEXT_DB_PATH": str(root / "knowledge.db"),
                "CONTEXT_SEMANTIC_ENABLED": "false",
                "CONTEXT_MODEL_AUTO_DOWNLOAD": "false",
                "CONTEXT_MODEL_AUTO_PREPARE": "false",
                "FILE_STORE_DB_PATH": str(root / "files.db"),
                "FILE_STORE_ROOT": str(root / "files"),
                "FILE_STORE_MAX_BYTES": "4096",
                "FILE_STORE_MAX_TOTAL_BYTES": "16384",
                "FILE_STORE_MAX_FILES": "20",
                "FILE_STORE_TEXT_MAX_CHARS": "1000",
                "FILE_STORE_PUBLIC_BASE_URL": "https://postmaster.example.test",
                "FILE_STORE_DOWNLOAD_SECRET": "ci-v9.7.2-public-file-secret-0123456789abcdef",
                "FILE_STORE_DOWNLOAD_URL_TTL_SECONDS": "900",
                "PUBLIC_EMAIL_BASE_URL": "https://postmaster.example.test",
                "PUBLIC_MCP_HOST": "",
                "MAIL_ACCOUNTS_DB_PATH": str(root / "accounts.db"),
                "MAIL_ACCOUNTS_KEY_PATH": str(root / "accounts.key"),
                "EMAIL_ANALYTICS_DB_PATH": str(root / "analytics.db"),
                "EMAIL_ANALYTICS_KEY_PATH": str(root / "analytics.key"),
                "RECIPIENT_POLICY_DB_PATH": str(root / "policy.db"),
                "POSTMASTER_REF": "v9.7.2-test",
                "POSTMASTER_VERSION": "latest",
            }
        )

        import postmaster.runtime as runtime

        self.runtime = runtime
        runtime.analytics_store.cache_clear()
        runtime.link_store.cache_clear()
        for cached in (
            runtime.scheduler,
            runtime.context_engine,
            runtime.file_store,
            runtime.account_store,
            runtime.policy_client,
        ):
            cached.cache_clear()

        self.store = runtime.file_store()
        self.file_id = "public-terminal-file"
        self.payload = b"%PDF-v9.7.2-terminal-bytes"
        with patch("postmaster.file_store._now", return_value="2026-08-31T10:00:00+00:00"):
            self.saved = self.store.save_bytes(
                owner_id="owner",
                project_id="project",
                filename="report 2026.pdf",
                media_type="application/pdf",
                data=self.payload,
                file_id=self.file_id,
            )
        self.links = runtime.link_store()
        self.analytics = runtime.analytics_store()

    def tearDown(self) -> None:
        self.runtime.analytics_store.cache_clear()
        self.runtime.link_store.cache_clear()
        for cached in (
            self.runtime.scheduler,
            self.runtime.context_engine,
            self.runtime.file_store,
            self.runtime.account_store,
            self.runtime.policy_client,
        ):
            cached.cache_clear()
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _delivery(self, *, subject: str = "Stored File"):
        campaign = self.analytics.create_campaign(
            account_id="acct",
            sender="sender@example.test",
            subject=subject,
            track_opens=True,
            amp_used=False,
        )
        delivery = self.analytics.create_delivery(
            campaign_id=campaign["id"],
            account_id="acct",
            recipient="reader@example.net",
            recipient_role="to",
        )
        return campaign, delivery

    def _resource_url(self) -> str:
        result = self.runtime.get_stored_file_resource(self.file_id, "http")
        self.assertFalse(result.is_error)
        return str(result.content[0].uri)

    @staticmethod
    def _tracked_token(rendered: str) -> str:
        match = re.search(r"/t/c/([A-Za-z0-9_-]+)", rendered)
        if not match:
            raise AssertionError(rendered)
        return str(match.group(1))

    def _verified_info(self, file_id: str):
        info, _ = self.store.resolve_blob(file_id)
        return info

    def test_public_resource_is_opaque_terminal_safe_and_zero_network(self) -> None:
        url = self._resource_url()
        parsed = urlsplit(url)
        self.assertEqual((parsed.scheme, parsed.netloc), ("https", "postmaster.example.test"))
        self.assertTrue(parsed.path.startswith("/t/c/sfp1_"))
        self.assertEqual(parsed.query, "")
        self.assertNotIn("/files/", url)
        self.assertNotIn(self.file_id, url)

        routes = [
            route
            for route in self.runtime.app.router.routes
            if isinstance(route, Route) and route.path == "/t/c/{token}"
        ]
        self.assertEqual(len(routes), 1)

        with TestClient(self.runtime.app) as client, patch(
            "socket.getaddrinfo", side_effect=AssertionError("DNS must not be used")
        ), patch(
            "urllib.request.urlopen", side_effect=AssertionError("HTTP must not be used")
        ):
            response = client.get(parsed.path, follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.payload)
        self.assertNotIn("location", response.headers)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("report%202026.pdf", response.headers["content-disposition"])

    def test_resource_link_is_reclassified_as_stored_file_and_click_is_recorded(self) -> None:
        url = self._resource_url()
        html = f'<html><body><a href="{url}">Download report</a></body></html>'
        self.assertEqual(self.links.normalize_postmaster_html(html), html)

        campaign, delivery = self._delivery()
        rendered, meta, _ = self.links.instrument_html_with_shares(
            body_html=html,
            delivery=delivery,
            track_web_links=True,
            stored_file_resolver=self._verified_info,
        )
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]["target_type"], "stored_file")
        self.assertNotIn(self.file_id, rendered)
        self.assertNotIn("/files/", rendered)

        token = self._tracked_token(rendered)
        with self.links._connect() as conn:
            row = dict(
                conn.execute(
                    "SELECT * FROM tracking_links WHERE tracking_token=?",
                    (token,),
                ).fetchone()
            )
        self.assertEqual(row["target_type"], "stored_file")
        self.assertEqual(row["stored_file_id"], self.file_id)
        self.assertEqual(row["stored_file_sha256"], self.saved["sha256"])
        self.assertEqual(row["stored_file_created_at"], self.saved["created_at"])

        with TestClient(self.runtime.app) as client:
            response = client.get(f"/t/c/{token}", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.payload)
        self.assertNotIn("location", response.headers)

        events = self.links.list_click_events(delivery_id=delivery["id"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["campaign_id"], campaign["id"])
        self.assertEqual(events[0]["target_type"], "stored_file")
        self.assertEqual(events[0]["download_filename"], "report 2026.pdf")

    def test_normal_tracked_web_url_still_redirects(self) -> None:
        _, delivery = self._delivery(subject="Web URL")
        destination = "https://destination.example/path?q=1"
        rendered, meta, _ = self.links.instrument_html_with_shares(
            body_html=f'<a href="{destination}">Website</a>',
            delivery=delivery,
            track_web_links=True,
            stored_file_resolver=self._verified_info,
        )
        self.assertEqual(meta[0]["target_type"], "url")
        token = self._tracked_token(rendered)
        with TestClient(self.runtime.app) as client:
            response = client.get(f"/t/c/{token}", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], destination)

    def test_delete_and_recreate_never_resurrects_public_or_tracked_capability(self) -> None:
        public_url = self._resource_url()
        public_path = urlsplit(public_url).path
        _, delivery = self._delivery(subject="Incarnation")
        rendered, _, _ = self.links.instrument_html_with_shares(
            body_html=f'<a href="{public_url}">Download</a>',
            delivery=delivery,
            track_web_links=True,
            stored_file_resolver=self._verified_info,
        )
        tracked_token = self._tracked_token(rendered)

        self.store.delete(self.file_id)
        with TestClient(self.runtime.app) as client:
            self.assertEqual(client.get(public_path, follow_redirects=False).status_code, 404)
            self.assertEqual(
                client.get(f"/t/c/{tracked_token}", follow_redirects=False).status_code,
                404,
            )

        with patch("postmaster.file_store._now", return_value="2026-09-01T10:00:00+00:00"):
            replacement = self.store.save_bytes(
                owner_id="owner",
                project_id="project",
                filename="report 2026.pdf",
                media_type="application/pdf",
                data=self.payload,
                file_id=self.file_id,
            )
        self.assertNotEqual(replacement["created_at"], self.saved["created_at"])
        new_url = self._resource_url()
        self.assertNotEqual(new_url, public_url)

        with TestClient(self.runtime.app) as client:
            self.assertEqual(client.get(public_path, follow_redirects=False).status_code, 404)
            self.assertEqual(
                client.get(f"/t/c/{tracked_token}", follow_redirects=False).status_code,
                404,
            )
            self.assertEqual(
                client.get(urlsplit(new_url).path, follow_redirects=False).content,
                self.payload,
            )

    def test_v971_tracked_files_capability_is_terminal_and_expiring_legacy_semantics_hold(self) -> None:
        durable = build_signed_file_url(self.store, self.file_id)
        self.assertIn("/files/", durable)
        _, delivery = self._delivery(subject="v9.7.1 durable")
        rendered, meta, _ = self.links.instrument_html_with_shares(
            body_html=f'<a href="{durable}">Old resource link</a>',
            delivery=delivery,
            track_web_links=True,
            stored_file_resolver=self._verified_info,
        )
        self.assertEqual(meta[0]["target_type"], "url")
        token = self._tracked_token(rendered)

        with TestClient(self.runtime.app) as client, patch(
            "socket.getaddrinfo", side_effect=AssertionError("DNS must not be used")
        ), patch(
            "urllib.request.urlopen", side_effect=AssertionError("HTTP must not be used")
        ):
            response = client.get(f"/t/c/{token}", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.payload)
        self.assertNotIn("location", response.headers)

        now = 1_800_000_000
        expiring = build_signed_file_url(
            self.store,
            self.file_id,
            now=now,
            expires=now + 120,
        )
        _, delivery2 = self._delivery(subject="legacy expiring")
        rendered2, _, _ = self.links.instrument_html_with_shares(
            body_html=f'<a href="{expiring}">Expiring resource</a>',
            delivery=delivery2,
            track_web_links=True,
            stored_file_resolver=self._verified_info,
        )
        token2 = self._tracked_token(rendered2)
        with TestClient(self.runtime.app) as client:
            with patch("postmaster.file_handoff.time.time", return_value=now + 60):
                valid = client.get(f"/t/c/{token2}", follow_redirects=False)
            with patch("postmaster.file_handoff.time.time", return_value=now + 121):
                expired = client.get(f"/t/c/{token2}", follow_redirects=False)
        self.assertEqual((valid.status_code, valid.content), (200, self.payload))
        self.assertEqual(expired.status_code, 404)

    def test_mcp_command_surface_remains_97_and_public_contract_is_consistent(self) -> None:
        tools = self.runtime.mcp._tool_manager.list_tools()
        self.assertEqual(len(tools), 97)
        names = {tool.name for tool in tools}
        self.assertIn("get_stored_file_resource", names)

        status = self.runtime.tracking_status()
        self.assertTrue(status["link_tracking"]["stored_file_public_downloads"])
        self.assertEqual(status["link_tracking"]["public_file_path"], "/t/c/*")
        self.assertFalse(status["link_tracking"]["stored_file_id_exposed_in_public_url"])
        self.assertEqual(self.runtime.build_status()["stored_file_public_download_path"], "/t/c/*")


if __name__ == "__main__":
    unittest.main()
