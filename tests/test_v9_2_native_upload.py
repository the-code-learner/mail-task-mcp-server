from __future__ import annotations

import asyncio
import ipaddress
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from postmaster.remote_file import (
    DownloadedFile,
    RemoteFileError,
    download_openai_file,
    validate_public_https_url,
)


PUBLIC_IP = ipaddress.ip_address("93.184.216.34")
PRIVATE_IP = ipaddress.ip_address("127.0.0.1")


class RemoteFileDownloaderTests(unittest.TestCase):
    def test_https_download_stream_and_limits(self) -> None:
        payload = b"native-chatgpt-file"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(payload))},
                content=payload,
                request=request,
            )

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
        with patch("postmaster.remote_file._resolved_addresses", return_value={PUBLIC_IP}):
            result = download_openai_file(
                {"download_url": "https://files.example.test/object", "file_id": "file_test"},
                max_bytes=1024,
                client=client,
            )
        client.close()
        self.assertEqual(result.data, payload)
        self.assertEqual(result.response_media_type, "application/octet-stream")

    def test_rejects_non_https_private_redirect_and_oversize(self) -> None:
        with self.assertRaises(RemoteFileError):
            validate_public_https_url("http://example.com/file")
        with self.assertRaises(RemoteFileError):
            validate_public_https_url("https://127.0.0.1/file")

        def redirect_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"}, request=request)

        def resolver(hostname: str):
            return {PRIVATE_IP} if hostname == "127.0.0.1" else {PUBLIC_IP}

        client = httpx.Client(transport=httpx.MockTransport(redirect_handler), follow_redirects=False)
        with patch("postmaster.remote_file._resolved_addresses", side_effect=resolver):
            with self.assertRaises(RemoteFileError):
                download_openai_file(
                    {"download_url": "https://files.example.test/start", "file_id": "file_redirect"},
                    max_bytes=1024,
                    client=client,
                )
        client.close()

        def large_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"Content-Length": "5000"}, content=b"x", request=request)

        client = httpx.Client(transport=httpx.MockTransport(large_handler), follow_redirects=False)
        with patch("postmaster.remote_file._resolved_addresses", return_value={PUBLIC_IP}):
            with self.assertRaises(RemoteFileError):
                download_openai_file(
                    {"download_url": "https://files.example.test/large", "file_id": "file_large"},
                    max_bytes=100,
                    client=client,
                )
        client.close()


class V92ServerToolTests(unittest.TestCase):
    ENV_KEYS = (
        "SCHEDULER_DB_PATH",
        "DEFAULT_OWNER_ID",
        "DEFAULT_OWNER_NAME",
        "SEED_TINKERER_PROJECT",
        "SEED_TINKERER_PROFILE",
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
        "FILE_STORE_REMOTE_MAX_BATCH_FILES",
        "BRIDGE_BUILD",
        "POSTMASTER_REF",
        "POSTMASTER_VERSION",
        "POSTMASTER_REQUESTED_VERSION",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = {key: os.environ.get(key) for key in self.ENV_KEYS}
        os.environ.update(
            {
                "SCHEDULER_DB_PATH": str(root / "scheduler.db"),
                "DEFAULT_OWNER_ID": "ci-default",
                "DEFAULT_OWNER_NAME": "CI Default",
                "SEED_TINKERER_PROJECT": "false",
                "SEED_TINKERER_PROFILE": "false",
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
                "FILE_STORE_REMOTE_MAX_BATCH_FILES": "3",
                "POSTMASTER_VERSION": "latest",
                "POSTMASTER_REF": "v9.2.0",
            }
        )
        os.environ.pop("BRIDGE_BUILD", None)
        os.environ.pop("POSTMASTER_REQUESTED_VERSION", None)

        import postmaster.server as server

        self.s = server
        server.scheduler.cache_clear()
        server.context_engine.cache_clear()
        server.file_store.cache_clear()

    def tearDown(self) -> None:
        self.s.scheduler.cache_clear()
        self.s.context_engine.cache_clear()
        self.s.file_store.cache_clear()
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _descriptor(self, name: str) -> dict:
        tools = asyncio.run(self.s.mcp.list_tools())
        tool = next(item for item in tools if item.name == name)
        return tool.model_dump(by_alias=True, exclude_none=True)

    @staticmethod
    def _resolve_schema(schema: dict, node: dict) -> dict:
        ref = node.get("$ref")
        if not ref:
            return node
        prefix = "#/$defs/"
        if not ref.startswith(prefix):
            raise AssertionError(f"unexpected schema ref: {ref}")
        return schema["$defs"][ref[len(prefix):]]

    def test_chatgpt_file_param_descriptors_match_openai_contract(self) -> None:
        single = self._descriptor("save_uploaded_file")
        self.assertEqual(single["_meta"]["openai/fileParams"], ["file"])
        schema = single["inputSchema"]
        file_schema = self._resolve_schema(schema, schema["properties"]["file"])
        self.assertEqual(
            set(file_schema["properties"]),
            {"download_url", "file_id", "mime_type", "file_name"},
        )
        self.assertEqual(set(file_schema["required"]), {"download_url", "file_id"})
        self.assertFalse(file_schema.get("additionalProperties", True))

        batch = self._descriptor("save_uploaded_files")
        self.assertEqual(batch["_meta"]["openai/fileParams"], ["files"])
        batch_schema = batch["inputSchema"]
        item_schema = self._resolve_schema(batch_schema, batch_schema["properties"]["files"]["items"])
        self.assertEqual(set(item_schema["required"]), {"download_url", "file_id"})

    def test_native_file_round_trip_uses_existing_file_store(self) -> None:
        s = self.s
        expected_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(s.build_status()["version"], expected_version)
        self.assertEqual(s.build_status()["build"], "v9.2.0")
        self.assertEqual(s.build_status()["requested_version"], "latest")
        self.assertTrue(s.build_status()["native_chatgpt_file_upload"])
        self.assertTrue(s.create_owner("owner", "Owner")["ok"])
        self.assertTrue(s.create_project("owner", "project", "Project")["ok"])

        source = {
            "download_url": "https://files.example.test/native",
            "file_id": "file_native",
            "mime_type": "application/zip",
            "file_name": "canonical.zip",
        }
        with patch(
            "postmaster.server.download_openai_file",
            return_value=DownloadedFile(data=b"PK-native-file", response_media_type="application/zip"),
        ):
            saved = s.save_uploaded_file(
                owner_id="owner",
                project_id="project",
                file=source,
                tags=["native", "v9.2"],
            )
        self.assertEqual(saved["filename"], "canonical.zip")
        self.assertEqual(saved["media_type"], "application/zip")
        self.assertEqual(saved["size_bytes"], len(b"PK-native-file"))
        raw = s.file_store().raw_bytes(saved["id"])[1]
        self.assertEqual(raw, b"PK-native-file")
        self.assertTrue(s.delete_stored_file(saved["id"])["ok"])
        self.assertEqual(s.file_store_status()["files"], 0)

    def test_batch_limit_is_enforced_before_download(self) -> None:
        s = self.s
        files = [
            {"download_url": f"https://files.example.test/{i}", "file_id": f"file_{i}"}
            for i in range(4)
        ]
        result = s.save_uploaded_files(owner_id="owner", files=files)
        self.assertFalse(result["ok"])
        self.assertIn("FILE_STORE_REMOTE_MAX_BATCH_FILES", result["error"])


if __name__ == "__main__":
    unittest.main()
