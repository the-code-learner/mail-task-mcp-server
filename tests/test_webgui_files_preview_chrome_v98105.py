"""Chrome-safe PDF, inert text/Word previews and file manager upload regressions."""
from __future__ import annotations

import base64
import io
import unittest
import zipfile

from starlette.requests import Request

from postmaster.webgui_files_v984 import preview_file, preview_supported


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx(body: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", body)
    return output.getvalue()


class Store:
    def __init__(self, media: str, raw: bytes):
        self.media, self.raw = media, raw

    def read_base64(self, _file_id):
        return {
            "ok": True,
            "file": {"filename": "preview.docx", "media_type": self.media},
            "content_base64": base64.b64encode(self.raw).decode("ascii"),
        }


class Base:
    def __init__(self, media: str, raw: bytes):
        self.store = Store(media, raw)

    def file_store(self):
        return self.store


def request():
    return Request({
        "type": "http", "method": "GET",
        "path": "/dashboard/files/file-1/preview",
        "path_params": {"file_id": "file-1"}, "headers": [],
    })


class ChromeAndWordPreviewTests(unittest.TestCase):
    def test_chrome_pdf_uses_inline_native_browser_viewer_not_sandbox(self):
        response = preview_file(Base("application/pdf", b"%PDF-1.4"), request())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "application/pdf")
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertIn("frame-ancestors 'self'", response.headers["content-security-policy"])
        self.assertNotIn("sandbox", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_html_and_json_still_have_inert_text_and_csp_sandbox(self):
        for media, payload in (
            ("text/html", b"<script>alert(1)</script>"),
            ("application/json", b'{"key":"<img src=x onerror=evil()>"}'),
        ):
            response = preview_file(Base(media, payload), request())
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.media_type.startswith("text/plain"))
            self.assertIn("sandbox", response.headers["content-security-policy"])
            self.assertEqual(response.body, payload)
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_docx_text_can_be_previewed_without_downloading_or_executing(self):
        xml = (
            '<w:document xmlns:w="' + NS + '"><w:body>'
            '<w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Second</w:t><w:tab/><w:t>line</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
        self.assertTrue(preview_supported(DOCX))
        response = preview_file(Base(DOCX, docx(xml)), request())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.media_type.startswith("text/plain"))
        self.assertIn("sandbox", response.headers["content-security-policy"])
        self.assertIn(b"First paragraph", response.body)
        self.assertIn(b"Second", response.body)
        self.assertIn(b"line", response.body)
        self.assertIn(b"inline", response.headers["content-disposition"].encode())

    def test_invalid_word_or_zip_bomb_does_not_leak_content(self):
        for payload in (
            b"not-a-valid-zip",
            docx("A" * 3_100_000),
        ):
            response = preview_file(Base(DOCX, payload), request())
            self.assertEqual(response.status_code, 415)
            self.assertNotIn("A" * 100, response.body.decode("utf-8"))
            self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_nonpreviewable_archive_stays_download_only(self):
        self.assertFalse(preview_supported("application/zip"))
        result = preview_file(Base("application/zip", b"PK random"), request())
        self.assertEqual(result.status_code, 415)


if __name__ == "__main__":
    unittest.main()
