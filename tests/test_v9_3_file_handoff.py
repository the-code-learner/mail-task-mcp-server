from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from mcp import Client
from mcp.types import BlobResourceContents, ResourceLink, TextContent
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from postmaster.file_handoff import build_signed_file_url


class V93FileHandoffTests(unittest.TestCase):
    KEYS = (
        "SCHEDULER_DB_PATH", "CONTEXT_DB_PATH", "CONTEXT_SEMANTIC_ENABLED",
        "CONTEXT_MODEL_AUTO_DOWNLOAD", "CONTEXT_MODEL_AUTO_PREPARE",
        "FILE_STORE_DB_PATH", "FILE_STORE_ROOT", "FILE_STORE_MAX_BYTES",
        "FILE_STORE_MAX_TOTAL_BYTES", "FILE_STORE_MAX_FILES", "FILE_STORE_TEXT_MAX_CHARS",
        "FILE_STORE_PUBLIC_BASE_URL", "FILE_STORE_DOWNLOAD_SECRET",
        "FILE_STORE_DOWNLOAD_URL_TTL_SECONDS", "PUBLIC_MCP_HOST", "MAIL_ACCOUNTS_DB_PATH",
        "MAIL_ACCOUNTS_KEY_PATH", "EMAIL_ANALYTICS_DB_PATH", "EMAIL_ANALYTICS_KEY_PATH",
        "RECIPIENT_POLICY_DB_PATH", "POSTMASTER_REF", "POSTMASTER_VERSION",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
        self.old = {key: os.environ.get(key) for key in self.KEYS}
        os.environ.update({
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
            "FILE_STORE_PUBLIC_BASE_URL": "https://files.example.test",
            "FILE_STORE_DOWNLOAD_SECRET": "ci-v9.3-file-download-secret-0123456789abcdef",
            "FILE_STORE_DOWNLOAD_URL_TTL_SECONDS": "900",
            "PUBLIC_MCP_HOST": "",
            "MAIL_ACCOUNTS_DB_PATH": str(root / "accounts.db"),
            "MAIL_ACCOUNTS_KEY_PATH": str(root / "accounts.key"),
            "EMAIL_ANALYTICS_DB_PATH": str(root / "analytics.db"),
            "EMAIL_ANALYTICS_KEY_PATH": str(root / "analytics.key"),
            "RECIPIENT_POLICY_DB_PATH": str(root / "policy.db"),
            "POSTMASTER_REF": f"v{version}-test",
            "POSTMASTER_VERSION": "latest",
        })
        # Keep this historical handoff test deterministic: its contract is resource
        # handoff, not analytics-key generation. A random 32-byte key can begin or
        # end with whitespace bytes, while the legacy loader strips existing keys.
        (root / "analytics.key").write_bytes(b"A" * 32)
        import postmaster.runtime as runtime
        self.s = runtime
        self.s.analytics_store.cache_clear()
        self.s.link_store.cache_clear()
        for cached in (
            runtime.scheduler,
            runtime.context_engine,
            runtime.file_store,
            runtime.account_store,
            runtime.policy_client,
        ):
            cached.cache_clear()
        self.payload = b"\x00postmaster-v9.3-original-bytes\xff\x10"
        self.saved = runtime.file_store().save_bytes(
            owner_id="owner", project_id="project", filename="original asset.bin",
            data=self.payload, media_type="application/x-postmaster-test",
            description="v9.3 handoff test", tags=["v9.3"],
        )

    def tearDown(self) -> None:
        self.s.analytics_store.cache_clear()
        self.s.link_store.cache_clear()
        for cached in (
            self.s.scheduler,
            self.s.context_engine,
            self.s.file_store,
            self.s.account_store,
            self.s.policy_client,
        ):
            cached.cache_clear()
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    async def _tool(self, transport: str):
        async with Client(self.s.mcp, raise_exceptions=True) as client:
            return await client.call_tool("get_stored_file_resource", {
                "file_id": self.saved["id"], "transport": transport,
            })

    def _route(self, path_template: str) -> Route:
        routes = [
            route
            for route in self.s.app.router.routes
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
            "client": ("203.0.113.9", 49153),
            "server": ("files.example.test", 443),
            "path_params": path_params or {},
        }
        response = await self._route(path_template).endpoint(Request(scope))
        if isinstance(response, StreamingResponse):
            body = b"".join([chunk async for chunk in response.body_iterator])
        else:
            body = response.body
        return response, body

    def _invoke(self, *args, **kwargs):
        return asyncio.run(self._invoke_async(*args, **kwargs))

    def _click(self, token: str, *, method: str = "GET"):
        return self._invoke(
            "/t/c/{token}",
            f"/t/c/{token}",
            method=method,
            path_params={"token": token},
        )

    def _legacy_file_request(
        self,
        file_id: str,
        query: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ):
        return self._invoke(
            "/files/{file_id}",
            f"/files/{file_id}",
            method=method,
            path_params={"file_id": file_id},
            query=query,
            headers=headers,
        )

    def test_native_resource_link_and_resources_read(self) -> None:
        store = self.s.file_store()
        with patch.object(
            store, "raw_bytes", side_effect=AssertionError("link construction must not read blob")
        ):
            result = asyncio.run(self._tool("mcp"))
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.content), 1)
        link = result.content[0]
        self.assertIsInstance(link, ResourceLink)
        self.assertNotIsInstance(link, TextContent)
        self.assertEqual(str(link.uri), f"postmaster://files/{self.saved['id']}")
        self.assertEqual(link.name, "original asset.bin")
        self.assertEqual(link.mime_type, "application/x-postmaster-test")
        self.assertEqual(link.size, len(self.payload))
        self.assertEqual(link.description, "v9.3 handoff test")

        async def read_resource():
            async with Client(self.s.mcp, raise_exceptions=True) as client:
                templates = await client.list_resource_templates()
                self.assertTrue(any(
                    str(item.uri_template) == "postmaster://files/{file_id}"
                    for item in templates.resource_templates
                ))
                return await client.read_resource(f"postmaster://files/{self.saved['id']}")

        read = asyncio.run(read_resource())
        self.assertEqual(len(read.contents), 1)
        blob = read.contents[0]
        self.assertIsInstance(blob, BlobResourceContents)
        self.assertEqual(base64.b64decode(blob.blob), self.payload)

        fallback = self.s.get_file_base64(self.saved["id"])
        self.assertEqual(base64.b64decode(fallback["content_base64"]), self.payload)
        self.assertTrue(self.s.build_status()["native_file_resource_handoff"])

    def test_public_resource_is_terminal_t_c_and_legacy_files_capabilities_remain_supported(self) -> None:
        auto = asyncio.run(self._tool("auto")).content[0]
        http = asyncio.run(self._tool("http")).content[0]
        self.assertNotEqual(str(auto.uri), str(http.uri))
        for link in (auto, http):
            self.assertIsInstance(link, ResourceLink)
            parsed = urlsplit(str(link.uri))
            self.assertEqual((parsed.scheme, parsed.netloc), ("https", "files.example.test"))
            self.assertTrue(parsed.path.startswith("/t/c/sfc1_"))
            self.assertNotIn(self.saved["id"], parsed.path)
            self.assertNotIn("/files/", parsed.path)
            self.assertEqual(parsed.query, "")

        parsed = urlsplit(str(http.uri))
        token = parsed.path.rsplit("/", 1)[-1]
        full, full_body = self._click(token)
        self.assertEqual((full.status_code, full_body), (200, self.payload))
        self.assertNotIn("location", full.headers)
        self.assertEqual(full.headers["content-type"], "application/x-postmaster-test")
        self.assertEqual(full.headers["content-length"], str(len(self.payload)))
        self.assertEqual(full.headers["x-content-type-options"], "nosniff")
        self.assertIn("attachment", full.headers["content-disposition"])

        head, head_body = self._click(token, method="HEAD")
        self.assertEqual((head.status_code, head_body), (200, b""))
        self.assertEqual(head.headers["content-length"], str(len(self.payload)))

        # Historical /files capability behavior remains available for compatibility.
        legacy = urlsplit(build_signed_file_url(self.s.file_store(), self.saved["id"]))
        legacy_full, legacy_body = self._legacy_file_request(
            self.saved["id"], legacy.query
        )
        self.assertEqual((legacy_full.status_code, legacy_body), (200, self.payload))
        self.assertEqual(legacy_full.headers["accept-ranges"], "bytes")

        partial, partial_body = self._legacy_file_request(
            self.saved["id"], legacy.query, headers={"Range": "bytes=3-11"}
        )
        self.assertEqual((partial.status_code, partial_body), (206, self.payload[3:12]))
        self.assertEqual(partial.headers["content-range"], f"bytes 3-11/{len(self.payload)}")

        invalid, _ = self._legacy_file_request(
            self.saved["id"], legacy.query, headers={"Range": "bytes=9999-10000"}
        )
        self.assertEqual(invalid.status_code, 416)
        self.assertEqual(invalid.headers["content-range"], f"bytes */{len(self.payload)}")

        query = parse_qs(legacy.query)
        sig = query["sig"][0]
        bad_sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        bad, _ = self._legacy_file_request(
            self.saved["id"], f"expires={query['expires'][0]}&sig={bad_sig}"
        )
        self.assertEqual(bad.status_code, 403)

        from postmaster.file_handoff import _download_signature
        expired = int(time.time()) - 10
        expired_sig = _download_signature(self.saved["id"], expired)
        expired_response, _ = self._legacy_file_request(
            self.saved["id"], f"expires={expired}&sig={expired_sig}"
        )
        self.assertEqual(expired_response.status_code, 403)

        missing_id = "00000000-0000-0000-0000-000000000000"
        future = int(time.time()) + 60
        missing_sig = _download_signature(missing_id, future)
        missing, _ = self._legacy_file_request(
            missing_id, f"expires={future}&sig={missing_sig}"
        )
        self.assertEqual(missing.status_code, 404)

    def test_transport_configuration_fallbacks(self) -> None:
        os.environ["FILE_STORE_PUBLIC_BASE_URL"] = ""
        os.environ["PUBLIC_MCP_HOST"] = "mcp.example.test"
        via_existing_host = asyncio.run(self._tool("auto"))
        self.assertFalse(via_existing_host.is_error)
        uri = str(via_existing_host.content[0].uri)
        self.assertTrue(uri.startswith("https://mcp.example.test/t/c/sfc1_"))
        self.assertNotIn(self.saved["id"], uri)

        os.environ["PUBLIC_MCP_HOST"] = ""
        mcp_fallback = asyncio.run(self._tool("auto"))
        self.assertEqual(str(mcp_fallback.content[0].uri), f"postmaster://files/{self.saved['id']}")
        http_error = asyncio.run(self._tool("http"))
        self.assertTrue(http_error.is_error)
        self.assertIn("PUBLIC_MCP_HOST", http_error.content[0].text)


if __name__ == "__main__":
    unittest.main()
