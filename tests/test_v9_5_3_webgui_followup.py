from __future__ import annotations

import unittest

from starlette.requests import Request

from postmaster.webgui_v953 import render_tracking_summary
from test_v9_5_3_webgui_admin_ux import FakeBase, FakeCore


def request_for(query: str) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": query.encode("utf-8"),
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("localhost", 8000),
    })


class V953TrackingSelectorFollowupTests(unittest.TestCase):
    def test_tracking_aggregate_view_explicitly_selects_all_accounts(self):
        html = render_tracking_summary(FakeBase(), FakeCore(), request_for("ui_view=tracking"))
        assert '<option value="" selected>All accounts</option>' in html
        assert '<option value="alpha">' in html
        assert '<option value="beta">' in html


if __name__ == "__main__":
    unittest.main()
