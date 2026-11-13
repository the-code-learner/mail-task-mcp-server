from __future__ import annotations

import base64
import unittest

from starlette.requests import Request

from postmaster.webgui_files_v984 import preview_file


class FakeStore:
    def __init__(self, media_type, payload):
        self.media_type = media_type
        self.payload = payload

    def read_base64(self, file_id):
        if file_id != "f1":
            raise ValueError("unknown")
        return {
            "ok": True,
            "file": {"filename": "untrusted.html", "media_type": self.media_type},
            "content_base64": base64.b64encode(self.payload).decode("ascii"),
        }


class FakeBase:
    def __init__(self, media_type, payload):
        self.store = FakeStore(media_type, payload)

    def file_store(self):
        return self.store


def request(file_id="f1"):
    return Request({
        "type": "http", "method": "GET", "scheme": "https",
        "path": f"/dashboard/files/{file_id}/preview",
        "query_string": b"", "headers": [],
        "path_params": {"file_id": file_id},
    })


class StoredFilePreviewTests(unittest.TestCase):
    def test_html_is_inert_text(self):
        result = preview_file(FakeBase("text/html", b"<script>alert(1)</script>"), request())
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.media_type.startswith("text/plain"))
        self.assertIn(b"<script>", result.body)
        self.assertIn("sandbox", result.headers["content-security-policy"])
        self.assertEqual(result.headers["x-content-type-options"], "nosniff")

    def test_json_is_inert_text(self):
        result = preview_file(FakeBase("application/json", b'{"ok":true}'), request())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.media_type, "text/plain")

    def test_pdf_is_inline(self):
        result = preview_file(FakeBase("application/pdf", b"%PDF-1.5"), request())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.media_type, "application/pdf")
        self.assertTrue(result.headers["content-disposition"].startswith("inline;"))

    def test_unsupported_binary_is_not_inline(self):
        result = preview_file(FakeBase("application/zip", b"PK"), request())
        self.assertEqual(result.status_code, 415)

    def test_unknown_id_does_not_leak_store_error(self):
        result = preview_file(FakeBase("application/pdf", b"x"), request("missing"))
        self.assertEqual(result.status_code, 404)


if __name__ == "__main__":
    unittest.main()
