from __future__ import annotations

import unittest
from datetime import UTC,datetime,timedelta
from unittest.mock import patch

from starlette.requests import Request
from postmaster.webgui_tracking_v985 import scoped_events,render_tracking_v985


def request(query=b""):
    return Request({
        "type":"http","method":"GET","scheme":"http",
        "path":"/","query_string":query,"headers":[]
    })


class TrackingV985Tests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,11,7,tzinfo=UTC)
        self.end=datetime(2026,11,14,tzinfo=UTC)

    def test_email_account_and_generic_owner_project_are_independent_filters(self):
        calls=[]
        def fake_events(base,account,owner,project,start,finish,*,filters):
            calls.append((filters["event_kind"],account,owner,project))
            return ([{
                "event_kind":filters["event_kind"],
                "event_id":len(calls),"observed_at":"2026-11-13T10:00:00+00:00"
            }],False)
        with patch("postmaster.webgui_tracking_v985._events",side_effect=fake_events):
            rows,truncated=scoped_events(
                object(),account_id="mail-primary",owner_id="alice",project_id="proj-a",
                start=self.start,finish=self.end,filters={"sort":"desc"}
            )
        self.assertFalse(truncated)
        self.assertEqual(len(rows),4)
        self.assertEqual(calls,[
            ("email_open","mail-primary","",""),
            ("email_click","mail-primary","",""),
            ("tracked_url_click","","alice","proj-a"),
            ("tracked_file_download","","alice","proj-a"),
        ])

    def test_renders_approved_overview_using_live_events_and_all_three_heatmaps(self):
        data=[
            {"event_kind":"email_open","event_id":1,"observed_at":"2026-11-12T09:00:00+00:00",
             "delivery_id":"d1","recipient":"person@invalid.test"},
            {"event_kind":"email_click","event_id":2,"observed_at":"2026-11-12T09:10:00+00:00",
             "delivery_id":"d1","recipient":"person@invalid.test"},
            {"event_kind":"tracked_file_download","event_id":3,
             "observed_at":"2026-11-12T10:00:00+00:00","tracked_link_id":"glnk_1"},
        ]
        with patch("postmaster.webgui_tracking_v985._period",
                   return_value=(self.start,self.end,"7d")), \
             patch("postmaster.webgui_tracking_v985.scoped_events",
                   return_value=(data,False)), \
             patch("postmaster.webgui_tracking_v985._sent",
                   return_value=(["2026-11-08","2026-11-09"],False)), \
             patch("postmaster.webgui_tracking_v985._received",
                   return_value=(["2026-11-10"],"Recent mailbox sample")), \
             patch("postmaster.webgui_tracking_v985._heatmap",
                   side_effect=lambda ts,title,**kw: "<section>"+title+"</section>"), \
             patch("postmaster.webgui_tracking_v985._activity_trend",
                   return_value="<section>Real activity trend</section>"):
            html=render_tracking_v985(object(),object(),request(b"account_id=mail-primary"))
        for part in (
            "Recent activity","My performance","When emails are sent",
            "When emails are received","When tracking events occur",
            "Real activity trend","All events","Email","URL links","File links",
            "24 hours","7 days","28 days","90 days","Custom",
            "Observed open signals","Observed email click signals",
            "person@invalid.test","Statistics period",
        ):
            self.assertIn(part,html)
        self.assertIn('data-panel="tracking"',html)
        self.assertNotIn("Demo",html)
        self.assertIn("not proof of human reading",html)

    def test_url_and_file_tabs_filter_existing_tracked_link_manager(self):
        for tab,kind in (("links","url"),("files","stored_file")):
            with self.subTest(tab=tab):
                with patch("postmaster.webgui_tracking_v985._period",
                           return_value=(self.start,self.end,"7d")), \
                     patch("postmaster.webgui_tracking_v985.scoped_events",
                           return_value=([],False)), \
                     patch("postmaster.webgui_tracking_v985._sent",
                           return_value=([],False)), \
                     patch("postmaster.webgui_tracking_v985._received",
                           return_value=([],"Select a mailbox")), \
                     patch("postmaster.webgui_tracking_v985.render_tracked_links_v984",
                           return_value="<section>Actual tracked links</section>") as manager:
                    html=render_tracking_v985(
                        object(),object(),request(("tracking_tab="+tab).encode())
                    )
                self.assertIn("Actual tracked links",html)
                self.assertEqual(manager.call_args.kwargs["target_filter"],kind)


if __name__=="__main__":
    unittest.main()
