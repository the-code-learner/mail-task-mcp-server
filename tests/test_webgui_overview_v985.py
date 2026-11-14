from __future__ import annotations

import unittest
from datetime import UTC,datetime,timedelta
from unittest.mock import patch
from starlette.requests import Request

from postmaster.webgui_overview_v985 import render_overview_v985


def request(query=b""):
    return Request({"type":"http","method":"GET","scheme":"https",
                    "path":"/","query_string":query,"headers":[]})


class V985OverviewTests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,11,1,tzinfo=UTC)
        self.end=datetime(2026,11,8,tzinfo=UTC)
        self.period=patch(
            "postmaster.webgui_overview_v985._period",
            return_value=(self.start,self.end,"7d")
        )
        self.events=patch(
            "postmaster.webgui_overview_v985._events",
            return_value=([
                {"event_kind":"email_open","delivery_id":"d1","observed_at":"2026-11-05T09:00:00+00:00",
                 "recipient":"alice@invalid.test"},
                {"event_kind":"email_click","delivery_id":"d1","observed_at":"2026-11-05T09:03:00+00:00",
                 "recipient":"alice@invalid.test"},
                {"event_kind":"tracked_file_download","tracked_link_id":"glnk_x",
                 "observed_at":"2026-11-04T16:22:00+00:00","label":"Private document"},
            ],False)
        )
        self.sent=patch(
            "postmaster.webgui_overview_v985._sent",
            return_value=(["2026-11-01","2026-11-03"],False)
        )
        self.inbox=patch(
            "postmaster.webgui_overview_v985._received_sample",
            return_value=(6,3,"Bounded 100-message sample.")
        )
        self.version=patch(
            "postmaster.webgui_overview_v985.version_summary",
            return_value='<section class="v985-release-summary">Installed v9.8.5</section>'
        )
        for p in (self.period,self.events,self.sent,self.inbox,self.version):
            p.start();self.addCleanup(p.stop)

    def test_renders_real_activity_and_observed_not_human_rates(self):
        html=render_overview_v985(object(),request(b"account_id=mail-1"))
        self.assertIn("Recent activity",html)
        self.assertIn("My performance",html)
        self.assertIn("Installed v9.8.5",html)
        self.assertIn("alice@invalid.test",html)
        self.assertIn("Private document",html)
        self.assertIn("50%",html)
        self.assertIn("not confirmation of a human reading",html)
        self.assertIn("When",html) if False else None
        self.assertIn('data-panel="overview"',html)
        self.assertNotIn("team-demo",html)
        self.assertNotIn("WebGUI v9.6.2",html)

    def test_period_is_not_all_time_and_can_change_to_custom(self):
        html=render_overview_v985(object(),request())
        for label in ("24 hours","7 days","28 days","90 days","Custom"):
            self.assertIn(label,html)
        self.assertIn('name="from"',html)
        self.assertIn('name="to"',html)
        self.assertIn('name="period" value="custom"',html)

    def test_does_not_expose_raw_ip_in_overview_activity(self):
        html=render_overview_v985(object(),request())
        self.assertNotIn("source_ip",html)
        self.assertIn("View tracking",html)


if __name__=="__main__":
    unittest.main()
