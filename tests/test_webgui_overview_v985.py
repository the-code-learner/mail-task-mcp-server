from __future__ import annotations

import unittest
from datetime import UTC,datetime
from unittest.mock import patch

from starlette.requests import Request

from postmaster.webgui_overview_v985 import render_overview_v985


def request(q=b""):
    return Request({
        "type":"http","method":"GET","scheme":"https","path":"/",
        "query_string":q,"headers":[]
    })


class _Files:
    def list_files(self,**kwargs):
        return [{"id":"f1"},{"id":"f2"}]


class _Mail:
    def search_emails(self,**kwargs):
        assert kwargs["unread_only"] is True
        return [{"uid":"1"}]


class _Base:
    def build_status(self):
        return {"version":"9.8.5"}
    def file_store(self):
        return _Files()
    def mail_client(self,account):
        assert account=="primary"
        return _Mail()
    def list_tracked_links(self,**kwargs):
        return {"ok":True,"links":[{"id":"l1"}]}
    def analytics_store(self):
        return object()


class ApprovedOverviewV985Tests(unittest.TestCase):
    def setUp(self):
        self.s=datetime(2026,11,1,tzinfo=UTC)
        self.e=datetime(2026,11,8,tzinfo=UTC)
        patches=[
            patch("postmaster.webgui_overview_v985._period",
                  return_value=(self.s,self.e,"7d")),
            patch("postmaster.webgui_overview_v985.latest_version_status",
                  return_value={"latest_version":"9.8.5","update_available":False}),
            patch("postmaster.webgui_overview_v985.global_tracking_network_events",
                  return_value={"events":[{"id":1},{"id":2},{"id":3}]}),
            patch("postmaster.webgui_overview_v985.webgui_v962_perf.owners",
                  return_value=[{"id":"alice"}]),
        ]
        for p in patches:
            p.start();self.addCleanup(p.stop)

    def test_approved_v5_sections_are_backed_by_live_methods(self):
        html=render_overview_v985(_Base(),request(b"account_id=primary"))
        for part in ("Installed version","Latest available stable","v9.8.5",
                     "Unread","Tracking events","Stored files","Tracked links",
                     "Quick access","Activities","Workspace","Contacts","Version settings"):
            self.assertIn(part,html)
        self.assertIn("Recent unread messages",html)
        self.assertIn("3</strong>",html)
        self.assertIn("2</strong>",html)
        self.assertIn('data-panel="overview"',html)
        self.assertNotIn("UI demo",html)
        self.assertNotIn("team-demo",html)

    def test_period_selector_includes_custom_and_all_user_ranges(self):
        html=render_overview_v985(_Base(),request())
        for title in ("24 hours","7 days","28 days","90 days","Custom"):
            self.assertIn(title,html)
        self.assertIn('name="from"',html)
        self.assertIn('name="to"',html)
        self.assertIn('name="period" value="custom"',html)
        self.assertIn("Choose an email account",html)

    def test_does_not_expose_raw_network_identifiers_in_overview(self):
        html=render_overview_v985(_Base(),request())
        self.assertNotIn("source_ip",html)
        self.assertIn("Tracking events are observed network requests",html)


if __name__=="__main__":
    unittest.main()
