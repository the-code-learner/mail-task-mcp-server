"""Files manager preview-first UI contract and safe download regressions."""
from __future__ import annotations

import base64
import re
import unittest
from unittest.mock import Mock
from urllib.parse import urlencode

from starlette.requests import Request

from postmaster.webgui_files_v984 import preview_supported, preview_file
from postmaster.webgui_projects import files_fragment
from postmaster.webgui_projects_ux_v971 import _make_file_rows_clickable
from postmaster.webgui_files_manager_v98104 import STYLE, SCRIPT


def request(*, project: str = "") -> Request:
    return Request({
        "type": "http", "method": "GET", "scheme": "https",
        "path": "/", "query_string": urlencode({"project": project}).encode()
        if project else b"", "headers": [],
    })


class FakeBase:
    def __init__(self):
        self.projects = [
            {"id": "outreach", "name": "Direct Outreach", "owner_id": "davide"},
            {"id": "creator", "name": "Creator Partnerships", "owner_id": "davide"},
        ]
        self.files = [
            {"id": "pdf-1", "filename": "CV_Exhibition_Care_2026.pdf",
             "project_id": "outreach", "owner_id": "davide",
             "media_type": "application/pdf", "size_bytes": 200063,
             "created_at": "2026-11-13T07:00:00Z",
             "description": "Approved outreach CV", "tags": ["outreach", "approved"]},
            {"id": "image-2", "filename": "Moodboard.png",
             "project_id": "creator", "owner_id": "davide",
             "media_type": "image/png", "size_bytes": 1204,
             "created_at": "2026-11-12T07:00:00Z",
             "description": "Visual references", "tags": ["branding"]},
            {"id": "zip-3", "filename": "Backup.zip",
             "project_id": None, "owner_id": "davide",
             "media_type": "application/zip", "size_bytes": 1004,
             "created_at": "2026-11-10T07:00:00Z",
             "description": "", "tags": []},
        ]
        self.list_calls = []
        self._scheduler = Mock()
        self._scheduler.list_projects.return_value = self.projects
        self._scheduler.list_owners.return_value = [
            {"id": "davide", "display_name": "Davide"},
        ]
        self._store = Mock()
        self._store.status.return_value = {
            "files": 418, "logical_bytes": 120902799,
            "max_bytes_per_file": 1048576,
        }
        self._store.list_files.side_effect = self.list_files

    def list_files(self, **kwargs):
        self.list_calls.append(kwargs)
        if "project_id" in kwargs:
            return [row for row in self.files
                    if row.get("project_id") == kwargs["project_id"]]
        return self.files

    def scheduler(self):
        return self._scheduler

    def file_store(self):
        return self._store

    def _csrf_value(self):
        return "test-csrf"

    def _safe_call(self, func, *args, **kwargs):
        return func(*args, **kwargs)


class FileManagerV98104Tests(unittest.TestCase):
    def setUp(self):
        self.base = FakeBase()

    def test_preview_click_is_not_a_download(self):
        html = files_fragment(self.base, request())
        self.assertIn('href="/dashboard/files/pdf-1/preview"', html)
        self.assertIn('data-pm-preview-link', html)
        self.assertIn('data-pm-preview-supported="1"', html)
        self.assertIn('href="/dashboard/files/pdf-1/download"', html)
        self.assertIn('data-pm-preview-pane', html)
        self.assertIn('data-pm-preview-frame', html)
        self.assertNotIn('title="PDF preview" sandbox', html)
        self.assertIn('data-pm-preview-text', html)
        self.assertIn('data-pm-upload-pane', html)
        self.assertIn('data-pm-open-tab', html)
        self.assertIn("Preview opens here without downloading", html)
        self.assertNotIn('data-v971-file-href', html)
        self.assertNotIn('onclick="location.assign(', html)

    def test_legacy_row_download_rewriter_does_not_mutate_preview_manager(self):
        html = files_fragment(self.base, request())
        transformed = _make_file_rows_clickable(html)
        self.assertEqual(transformed, html)
        self.assertIn('data-pm-preview-link', transformed)

    def test_upload_overlay_is_standalone_and_not_wrapped_by_legacy_section(self):
        html = files_fragment(self.base, request())
        self.assertEqual(html.count('data-pm-open-upload'), 1)
        self.assertEqual(html.count('data-pm-upload-pane'), 1)
        self.assertIn('class="pm-drive-upload-pane" data-pm-upload-pane', html)
        self.assertIn('data-pm-upload-shade aria-hidden="true" hidden', html)
        self.assertNotIn('class="pm-drive-upload"', html)
        self.assertLess(html.index('class="pm-drive-head"'), html.index('data-pm-open-upload'))
        self.assertLess(html.index('data-pm-inventory-head') if
                        'data-pm-inventory-head' in html else html.index('pm-drive-inventory-head'),
                        html.index('data-pm-upload-pane'))
        from unittest.mock import patch
        from postmaster import webgui_v962_views
        with patch.object(webgui_v962_views, "files_fragment", return_value=html):
            rendered = webgui_v962_views.render_view(self.base, None, request(), "files")
        self.assertEqual(rendered, html)
        self.assertNotIn('data-v962-state-key="files-upload"', rendered)

    def test_upload_and_preview_dialog_js_is_keyboard_usable(self):
        for token in (
            "data-pm-open-upload", "data-pm-upload-pane",
            "data-pm-close-upload", "setUpload(root,true)",
            "setUpload(root,false)", "event.key==='Escape'",
            "data-pm-preview-text", "response.text()",
            "source.textContent=", "AbortController",
            "data-pm-preview-image", "data-pm-preview-video",
            "media==='application/pdf'",
        ):
            self.assertIn(token, SCRIPT, token)

    def test_file_upload_keeps_owner_project_tags_and_safe_crud(self):
        html = files_fragment(self.base, request())
        self.assertIn('action="/dashboard/files/upload"', html)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn('name="file" required', html)
        self.assertIn('name="owner_id" required', html)
        self.assertIn('name="project_id"', html)
        self.assertIn('name="description"', html)
        self.assertIn('name="tags"', html)
        self.assertIn('name="csrf" value="test-csrf"', html)
        self.assertEqual(html.count('action="/dashboard/files/delete"'), 3)
        self.assertIn("Delete this stored file?", html)
        self.assertIn('data-pm-copy-id="pdf-1"', html)

    def test_server_side_project_scope_is_preserved(self):
        html = files_fragment(self.base, request(project="outreach"))
        self.assertIn("CV_Exhibition_Care_2026.pdf", html)
        self.assertNotIn("Moodboard.png", html)
        self.assertNotIn("Backup.zip", html)
        self.assertEqual(
            self.base.list_calls,
            [{"project_id": "outreach", "include_global": False, "limit": 500}],
        )
        self.assertIn("Project: Direct Outreach", html)

    def test_unsafe_filename_description_and_tags_are_html_escaped(self):
        self.base.files = [
            {**self.base.files[0],
             "filename": '</a><script>alert("bad")</script>.pdf',
             "description": 'Danger" onmouseover="x',
             "tags": ["<img src=x onerror=alert(1)>"]},
        ]
        html = files_fragment(self.base, request())
        self.assertNotIn("<script>alert", html)
        self.assertNotIn('onmouseover="x', html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('data-pm-search=', html)

    def test_mime_allowlist_matches_preview_endpoint_and_download_is_separate(self):
        for media in ("application/pdf", "image/png", "text/plain",
                      "application/json", "application/xml", "audio/mpeg"):
            self.assertTrue(preview_supported(media), media)
        for media in ("application/zip", "application/x-msdownload",
                      "application/octet-stream"):
            self.assertFalse(preview_supported(media), media)
        self.assertIn('data-pm-preview-supported="0"', files_fragment(self.base, request()))

        class PreviewBase:
            def file_store(self):
                return self
            def read_base64(self, file_id):
                return {"ok": True,
                        "file": {"filename": "report.pdf", "media_type": "application/pdf"},
                        "content_base64": base64.b64encode(b"%PDF-demo").decode("ascii")}
        req = Request({
            "type": "http", "method": "GET", "path": "/dashboard/files/pdf-1/preview",
            "path_params": {"file_id": "pdf-1"}, "headers": [],
        })
        response = preview_file(PreviewBase(), req)
        self.assertEqual(response.status_code, 200)
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertNotIn("sandbox", response.headers["content-security-policy"])
        self.assertIn("frame-ancestors", response.headers["content-security-policy"])

    def test_search_sort_grid_and_drawer_are_wired_for_lazy_fragments(self):
        self.assertIn("pm-drive-v98104-script", SCRIPT)
        self.assertIn("MutationObserver", SCRIPT)
        self.assertIn("data-pm-preview-link", SCRIPT)
        self.assertIn("dataset.pmDownload", SCRIPT)
        self.assertIn("data-pm-sort", SCRIPT)
        self.assertIn("data-pm-layout-toggle", SCRIPT)
        self.assertIn(".pm-drive-grid-view", STYLE)
        self.assertIn("pm-drive-preview-frame", STYLE)
        self.assertIn("pm-drive-upload-pane", STYLE)
        self.assertIn("pm-drive-root", STYLE)

    def test_installed_runtime_has_authenticated_preview_route(self):
        from postmaster import runtime, webgui_v962
        self.assertIn("pm-drive-v98104-script", webgui_v962.SCRIPT)
        self.assertIn("v9.8.10 file manager", webgui_v962.BASE_STYLE)
        self.assertIn(
            "/dashboard/files/{file_id}/preview",
            {getattr(route, "path", "") for route in runtime.app.router.routes},
        )


if __name__ == "__main__":
    unittest.main()
