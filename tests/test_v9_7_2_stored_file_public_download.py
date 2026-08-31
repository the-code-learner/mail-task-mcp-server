from __future__ import annotations

import asyncio
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

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

    def _delivery(self, recipient: str, *, subject: str = "Stored File"):
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
            recipient=recipient,
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

    def _route(self, path_template: str) -> Route:
        routes = [
            route
            for route in self.runtime.app.router.routes
            if isinstance(route, Route) and route.path == path_template
        ]
        self.assertEqual(len(routes), 1)
        return routes[0]

    async def _invoke_async(
        self,
        path_template: str,
        path: str,
        *,
        method: str = "GET",
        path_params: dict[str, str] | None = None,
        query: str = "",
        headers: dict[str, str] | None = None,
    ):
        encoded_headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in (headers or {}).items()
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query.encode("ascii"),
            "headers": encoded_headers,
            "client": ("203.0.113.8", 49152),
            "server": ("postmaster.example.test", 443),
            "path_params": path_params or {},
        }
        request = Request(scope)
        response = await self._route(path_template).endpoint(request)
        if isinstance(response, StreamingResponse):
            body = b"".join([chunk async for chunk in response.body_iterator])
        else:
            body = response.body
        return response, body

    def _invoke(self, *args, **kwargs):
        return asyncio.run(self._invoke_async(*args, **kwargs))

    def _click(self, token: str, **kwargs):
        return self._invoke(
            "/t/c/{token}",
            f"/t/c/{token}",
            path_params={"token": token},
            **kwargs,
        )

    def test_public_resource_is_random_db_backed_terminal_and_zero_network(self) -> None:
        first_url = self._resource_url()
        second_url = self._resource_url()
        self.assertNotEqual(first_url, second_url)

        parsed = urlsplit(first_url)
        self.assertEqual((parsed.scheme, parsed.netloc), ("https", "postmaster.example.test"))
        self.assertTrue(parsed.path.startswith("/t/c/sfc1_"))
        self.assertEqual(parsed.query, "")
        self.assertNotIn("/files/", first_url)
        self.assertNotIn(self.file_id, first_url)
        token = parsed.path.rsplit("/", 1)[-1]
        self.assertNotIn(self.saved["sha256"], token)
        self.assertNotIn("reader", token)

        capability = self.links.get_public_capability_by_token(token)
        self.assertEqual(capability["file_id"], self.file_id)
        self.assertEqual(capability["file_sha256"], self.saved["sha256"])
        self.assertEqual(capability["file_created_at"], self.saved["created_at"])
        with self.links._connect() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(stored_file_capabilities)").fetchall()
            }
            row = conn.execute(
                "SELECT token_hash FROM stored_file_capabilities WHERE id=?",
                (capability["id"],),
            ).fetchone()
        self.assertIn("token_hash", columns)
        self.assertNotIn("public_token", columns)
        self.assertNotEqual(str(row["token_hash"]), token)
        self.assertNotIn(token, str(row["token_hash"]))

        with patch.object(
            self.store, "list_files", side_effect=AssertionError("File Store scan is forbidden")
        ), patch(
            "socket.getaddrinfo", side_effect=AssertionError("DNS must not be used")
        ), patch(
            "urllib.request.urlopen", side_effect=AssertionError("HTTP must not be used")
        ):
            response, body = self._click(token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, self.payload)
        self.assertNotIn("location", response.headers)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("report%202026.pdf", response.headers["content-disposition"])
        self.assertIn("no-store", response.headers["cache-control"])

    def test_unknown_tokens_fail_closed_without_any_file_store_access(self) -> None:
        unknown_public = "sfc1_" + "A" * 43
        unknown_tracking = "totally-unknown-tracking-token"
        forbidden = AssertionError("unknown token must not open or scan File Store")
        with patch.object(self.store, "list_files", side_effect=forbidden), patch.object(
            self.store, "get_info", side_effect=forbidden
        ), patch.object(self.store, "raw_bytes", side_effect=forbidden), patch.object(
            self.store, "resolve_blob", side_effect=forbidden
        ):
            public_response, public_body = self._click(unknown_public)
            tracking_response, tracking_body = self._click(unknown_tracking)
        self.assertEqual((public_response.status_code, public_body), (404, b"Not found"))
        self.assertEqual((tracking_response.status_code, tracking_body), (404, b"Not found"))

    def test_occurrences_are_delivery_recipient_specific_and_map_through_capability(self) -> None:
        resource_url = self._resource_url()
        normalized = self.links.normalize_postmaster_html(
            f'<a href="{resource_url}">Download report</a>'
        )
        self.assertIn(f"postmaster-file:{self.file_id}", normalized)
        self.assertNotIn("/t/c/", normalized)

        occurrences: list[tuple[dict, dict, str]] = []
        for recipient, subject in (
            ("reader-a@example.net", "A first"),
            ("reader-b@example.net", "B first"),
            ("reader-a@example.net", "A second"),
        ):
            campaign, delivery = self._delivery(recipient, subject=subject)
            rendered, meta, _ = self.links.instrument_html_with_shares(
                body_html=f'<a href="{resource_url}">Download report</a>',
                delivery=delivery,
                track_web_links=True,
                stored_file_resolver=self._verified_info,
            )
            self.assertEqual(len(meta), 1)
            self.assertEqual(meta[0]["target_type"], "stored_file")
            self.assertNotIn("stored_file_capability_id", meta[0])
            self.assertNotIn(self.file_id, rendered)
            self.assertNotIn("/files/", rendered)
            occurrences.append((campaign, delivery, self._tracked_token(rendered)))

        tokens = {token for _, _, token in occurrences}
        self.assertEqual(len(tokens), 3)

        for campaign, delivery, token in occurrences:
            row = self.links.get_by_token(token)
            self.assertEqual(row["campaign_id"], campaign["id"])
            self.assertEqual(row["delivery_id"], delivery["id"])
            self.assertEqual(row["recipient"], delivery["recipient"])
            self.assertEqual(row["target_type"], "stored_file")
            self.assertTrue(row["stored_file_capability_id"])
            capability = self.links.get_stored_file_capability(row["stored_file_capability_id"])
            self.assertEqual(capability["file_id"], self.file_id)
            self.assertEqual(capability["file_sha256"], self.saved["sha256"])
            self.assertEqual(capability["file_created_at"], self.saved["created_at"])

        first_campaign, first_delivery, first_token = occurrences[0]
        response, body = self._click(first_token)
        self.assertEqual((response.status_code, body), (200, self.payload))
        self.assertNotIn("location", response.headers)
        events = self.links.list_click_events(delivery_id=first_delivery["id"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["campaign_id"], first_campaign["id"])
        self.assertEqual(events[0]["delivery_id"], first_delivery["id"])
        self.assertEqual(events[0]["recipient"], first_delivery["recipient"])
        self.assertEqual(events[0]["target_type"], "stored_file")

    def test_normal_web_tracking_never_opens_file_store_and_still_redirects(self) -> None:
        campaign, delivery = self._delivery("reader@example.net", subject="Web URL")
        destination = "https://destination.example/path?q=1"
        second_destination = "https://another.example/resource"
        forbidden = AssertionError("ordinary web instrumentation must not open File Store")
        with patch.object(self.store, "list_files", side_effect=forbidden), patch.object(
            self.store, "get_info", side_effect=forbidden
        ), patch.object(self.store, "raw_bytes", side_effect=forbidden), patch.object(
            self.store, "resolve_blob", side_effect=forbidden
        ):
            rendered, meta, _ = self.links.instrument_html_with_shares(
                body_html=(
                    f'<a href="{destination}">Website</a>'
                    f'<a href="{second_destination}">Other</a>'
                ),
                delivery=delivery,
                track_web_links=True,
                stored_file_resolver=self._verified_info,
            )
        self.assertEqual([item["target_type"] for item in meta], ["url", "url"])
        token = self._tracked_token(rendered)
        response, body = self._click(token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(body, b"")
        self.assertEqual(response.headers["location"], destination)
        events = self.links.list_click_events(delivery_id=delivery["id"])
        self.assertEqual(events[0]["campaign_id"], campaign["id"])

    def test_delete_and_recreate_never_resurrects_public_or_tracked_capability(self) -> None:
        public_url = self._resource_url()
        public_token = urlsplit(public_url).path.rsplit("/", 1)[-1]
        _, delivery = self._delivery("reader@example.net", subject="Incarnation")
        rendered, _, _ = self.links.instrument_html_with_shares(
            body_html=f'<a href="{public_url}">Download</a>',
            delivery=delivery,
            track_web_links=True,
            stored_file_resolver=self._verified_info,
        )
        tracked_token = self._tracked_token(rendered)

        self.store.delete(self.file_id)
        self.assertEqual(self._click(public_token)[0].status_code, 404)
        self.assertEqual(self._click(tracked_token)[0].status_code, 404)

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
        new_token = urlsplit(new_url).path.rsplit("/", 1)[-1]
        self.assertNotEqual(new_token, public_token)

        self.assertEqual(self._click(public_token)[0].status_code, 404)
        self.assertEqual(self._click(tracked_token)[0].status_code, 404)
        new_response, new_body = self._click(new_token)
        self.assertEqual((new_response.status_code, new_body), (200, self.payload))

    def test_v971_tracked_files_capability_is_terminal_and_expiring_semantics_hold(self) -> None:
        durable = build_signed_file_url(self.store, self.file_id)
        self.assertIn("/files/", durable)
        _, delivery = self._delivery("reader@example.net", subject="v9.7.1 durable")
        rendered, meta, _ = self.links.instrument_html_with_shares(
            body_html=f'<a href="{durable}">Old resource link</a>',
            delivery=delivery,
            track_web_links=True,
            stored_file_resolver=self._verified_info,
        )
        self.assertEqual(meta[0]["target_type"], "url")
        token = self._tracked_token(rendered)

        with patch(
            "socket.getaddrinfo", side_effect=AssertionError("DNS must not be used")
        ), patch(
            "urllib.request.urlopen", side_effect=AssertionError("HTTP must not be used")
        ):
            response, body = self._click(token)
        self.assertEqual((response.status_code, body), (200, self.payload))
        self.assertNotIn("location", response.headers)

        now = 1_800_000_000
        expiring = build_signed_file_url(
            self.store,
            self.file_id,
            now=now,
            expires=now + 120,
        )
        _, delivery2 = self._delivery("reader@example.net", subject="legacy expiring")
        rendered2, _, _ = self.links.instrument_html_with_shares(
            body_html=f'<a href="{expiring}">Expiring resource</a>',
            delivery=delivery2,
            track_web_links=True,
            stored_file_resolver=self._verified_info,
        )
        token2 = self._tracked_token(rendered2)
        with patch("postmaster.file_handoff.time.time", return_value=now + 60):
            valid_response, valid_body = self._click(token2)
        with patch("postmaster.file_handoff.time.time", return_value=now + 121):
            expired_response, _ = self._click(token2)
        self.assertEqual((valid_response.status_code, valid_body), (200, self.payload))
        self.assertEqual(expired_response.status_code, 404)

    def test_additive_schema_final_runtime_and_public_mcp_surface(self) -> None:
        with self.links._connect() as conn:
            capability_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(stored_file_capabilities)").fetchall()
            }
            tracking_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(tracking_links)").fetchall()
            }
        self.assertEqual(
            capability_columns,
            {
                "id",
                "token_hash",
                "file_id",
                "file_sha256",
                "file_created_at",
                "status",
                "created_at",
                "expires_at",
                "revoked_at",
            },
        )
        self.assertTrue(
            {"stored_file_capability_id", "stored_file_sha256", "stored_file_created_at"}
            <= tracking_columns
        )
        self._route("/t/c/{token}")

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
