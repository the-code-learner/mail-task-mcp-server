from __future__ import annotations

import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from starlette.requests import Request

from postmaster.webgui_links_v984 import _scope, render_tracked_links_v984


class _Base:
    def _csrf_value(self):
        return "fixed-csrf-token"


class _Core:
    def __init__(self):
        self.calls = []

    def list_tracked_links(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["owner_id"] == "other":
            return {"ok": True, "links": []}
        return {
            "ok": True,
            "links": [
                {
                    "id": "gtl_url", "owner_id": "alpha", "project_id": "p1",
                    "label": "Visit website", "target_type": "url",
                    "destination_url": "https://example.org/",
                    "tracked_url": "https://postmaster.example/t/c/glnk_a",
                    "total_clicks": 3, "status": "active", "created_at": "2026-11-10",
                },
                {
                    "id": "gtl_file", "owner_id": "alpha", "project_id": "p1",
                    "label": "Download PDF", "target_type": "stored_file",
                    "filename": "document.pdf", "file_id": "f1",
                    "tracked_url": "https://postmaster.example/t/c/glnk_b",
                    "total_clicks": 1, "status": "revoked", "created_at": "2026-11-09",
                },
            ],
        }


def _request(params=None):
    query = urlencode(params or {}).encode()
    return Request({
        "type": "http", "method": "GET", "scheme": "https",
        "server": ("localhost", 80), "path": "/dashboard/view/tracking",
        "query_string": query, "headers": [],
    })


class WebGuiLinksV984Tests(unittest.TestCase):
    def setUp(self):
        self.p1 = patch("postmaster.webgui_links_v984.owners",
                        return_value=[{"id": "alpha", "display_name": "Alpha"},
                                      {"id": "other", "display_name": "Other"}])
        self.p2 = patch("postmaster.webgui_links_v984.active_projects",
                        return_value=[{"id": "p1", "owner_id": "alpha", "name": "Project One"}])
        self.p1.start()
        self.p2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)

    def test_renders_url_and_file_tools_without_duplicate_token_minting(self):
        core = _Core()
        html = render_tracked_links_v984(_Base(), core, _request())
        self.assertIn("Create tracked URL", html)
        self.assertIn("Create tracked file link", html)
        self.assertIn("https://postmaster.example/t/c/glnk_a", html)
        self.assertIn("document.pdf", html)
        self.assertIn("fixed-csrf-token", html)
        self.assertIn("Revoke", html)
        self.assertEqual(len(core.calls), 2)
        self.assertTrue(all(call["active_only"] is False for call in core.calls))

    def test_filter_uses_one_owner_without_becoming_a_permission_boundary(self):
        core = _Core()
        html = render_tracked_links_v984(_Base(), core,
                                         _request({"owner_id": "alpha", "project_id": "p1"}))
        self.assertIn("Visit website", html)
        self.assertEqual(len(core.calls), 1)
        self.assertEqual(core.calls[0]["project_id"], "p1")

    def test_write_scope_rejects_mismatched_owner_project(self):
        with self.assertRaises(ValueError):
            _scope({"owner_id": "other", "project_id": "p1"}, _Base())
        self.assertEqual(_scope({"owner_id": "alpha", "project_id": "p1"}, _Base()),
                         ("alpha", "p1"))
        self.assertEqual(_scope({"owner_id": "other", "project_id": ""}, _Base()),
                         ("other", None))


if __name__ == "__main__":
    unittest.main()
