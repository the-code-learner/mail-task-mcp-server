from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from postmaster.file_handoff import build_signed_file_url, stored_file_http_response
from postmaster.file_store import FileStore


class V971DurableFileLinkTests(unittest.TestCase):
    ENV_KEYS = (
        "FILE_STORE_PUBLIC_BASE_URL",
        "FILE_STORE_DOWNLOAD_SECRET",
        "FILE_STORE_DOWNLOAD_URL_TTL_SECONDS",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_env = {key: os.environ.get(key) for key in self.ENV_KEYS}
        os.environ.update(
            {
                "FILE_STORE_PUBLIC_BASE_URL": "https://files.example.test",
                "FILE_STORE_DOWNLOAD_SECRET": "ci-v9.7.1-durable-file-secret-0123456789abcdef",
                # Deliberately retain the historical 15-minute value. New links must
                # no longer inherit this short lifetime.
                "FILE_STORE_DOWNLOAD_URL_TTL_SECONDS": "900",
            }
        )
        self.store = FileStore(
            db_path=str(root / "files.db"),
            root=str(root / "files"),
            max_bytes=4096,
            max_total_bytes=16384,
            max_files=20,
        )
        self.file_id = "durable-file-id"
        with patch("postmaster.file_store._now", return_value="2026-08-31T10:00:00+00:00"):
            self.saved = self.store.save_bytes(
                owner_id="owner",
                project_id="project",
                filename="cv.pdf",
                data=b"durable-postmaster-file",
                media_type="application/pdf",
                file_id=self.file_id,
            )

        def file_route(request: Request):
            return stored_file_http_response(request, self.store)

        self.app = Starlette(
            routes=[Route("/files/{file_id}", file_route, methods=["GET", "HEAD"])]
        )

    def tearDown(self) -> None:
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _signed_path(self, url: str) -> tuple[str, dict[str, list[str]]]:
        parsed = urlsplit(url)
        return parsed.path + "?" + parsed.query, parse_qs(parsed.query)

    def test_default_public_link_is_durable_beyond_thirty_days(self) -> None:
        url = build_signed_file_url(self.store, self.file_id)
        signed_path, query = self._signed_path(url)
        self.assertEqual(query["expires"], ["0"])
        self.assertEqual(len(query["sig"][0]), 64)

        baseline = int(time.time())
        with TestClient(self.app) as client:
            self.assertEqual(client.get(signed_path).content, b"durable-postmaster-file")
            with patch("postmaster.file_handoff.time.time", return_value=baseline + 31 * 86400):
                after_31_days = client.get(signed_path)
            self.assertEqual(after_31_days.status_code, 200)
            self.assertEqual(after_31_days.content, b"durable-postmaster-file")

    def test_delete_invalidates_durable_link(self) -> None:
        signed_path, _ = self._signed_path(build_signed_file_url(self.store, self.file_id))
        self.store.delete(self.file_id)
        with TestClient(self.app) as client:
            response = client.get(signed_path)
        self.assertEqual(response.status_code, 404)

    def test_recreating_same_id_does_not_resurrect_old_capability(self) -> None:
        signed_path, _ = self._signed_path(build_signed_file_url(self.store, self.file_id))
        self.store.delete(self.file_id)
        with patch("postmaster.file_store._now", return_value="2026-09-01T10:00:00+00:00"):
            replacement = self.store.save_bytes(
                owner_id="owner",
                project_id="project",
                filename="cv.pdf",
                data=b"durable-postmaster-file",
                media_type="application/pdf",
                file_id=self.file_id,
            )
        self.assertNotEqual(replacement["created_at"], self.saved["created_at"])

        with TestClient(self.app) as client:
            old_capability = client.get(signed_path)
            new_path, _ = self._signed_path(build_signed_file_url(self.store, self.file_id))
            new_capability = client.get(new_path)
        self.assertEqual(old_capability.status_code, 403)
        self.assertEqual(new_capability.status_code, 200)

    def test_explicit_legacy_expiring_links_remain_supported(self) -> None:
        now = 1_800_000_000
        url = build_signed_file_url(
            self.store,
            self.file_id,
            now=now,
            expires=now + 120,
        )
        signed_path, query = self._signed_path(url)
        self.assertEqual(query["expires"], [str(now + 120)])

        with TestClient(self.app) as client:
            with patch("postmaster.file_handoff.time.time", return_value=now + 60):
                valid = client.get(signed_path)
            with patch("postmaster.file_handoff.time.time", return_value=now + 121):
                expired = client.get(signed_path)
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(expired.status_code, 403)


if __name__ == "__main__":
    unittest.main()
