from __future__ import annotations

import unittest
from datetime import UTC,datetime,timedelta
from unittest.mock import MagicMock, patch

from starlette.requests import Request
from postmaster.webgui_tracking_v985 import scoped_events,render_tracking_v985
from postmaster.webgui_tracking_v984 import _received


def request(query=b""):
    return Request({
        "type":"http","method":"GET","scheme":"http",
        "path":"/","query_string":query,"headers":[]
    })


class TrackingV985Tests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,11,7,tzinfo=UTC)
        self.end=datetime(2026,11,14,tzinfo=UTC)

    def test_all_accounts_aggregates_received_dates_with_bounded_imap_queries(self):
        base=MagicMock()
        base.account_store.return_value.list_accounts.return_value=[
            {"id":"a"}, {"id":"b"}, {"id":"a"}
        ]
        clients={
            "a":[{"date":"2026-11-10T09:00:00+00:00"},
                 {"date":"2026-11-06T09:00:00+00:00"}],
            "b":[{"date":"Fri, 13 Nov 2026 14:00:00 +0000"}],
        }
        base.mail_client.side_effect=lambda key: type("Client",(),{
            "search_emails":lambda self,**kwargs:clients[key]
        })()
        dates,note=_received(base,"",self.start,self.end)
        self.assertEqual(len(dates),2)
        self.assertIn("per account",note)
        self.assertEqual(base.mail_client.call_count,2)

    def test_all_accounts_partial_imap_failure_preserves_other_accounts(self):
        base=MagicMock()
        base.account_store.return_value.list_accounts.return_value=[{"id":"bad"},{"id":"good"}]
        def client(key):
            if key=="bad":
                raise ConnectionError("offline")
            obj=MagicMock()
            obj.search_emails.return_value=[{"date":"2026-11-12T10:00:00+00:00"}]
            return obj
        base.mail_client.side_effect=client
        dates,note=_received(base,"",self.start,self.end)
        self.assertEqual(len(dates),1)
        self.assertIn("1 account(s) unavailable",note)

    def test_all_accounts_overview_displays_received_statistics(self):
        with patch("postmaster.webgui_tracking_v985._period",
                   return_value=(self.start,self.end,"7d")), \
             patch("postmaster.webgui_tracking_v985.scoped_events",
                   return_value=([],False)), \
             patch("postmaster.webgui_tracking_v985._sent",
                   return_value=([],False)), \
             patch("postmaster.webgui_tracking_v985._received",
                   return_value=(["2026-11-12T10:00:00+00:00"],"Sample")), \
             patch("postmaster.webgui_tracking_v985._heatmap",
                   side_effect=lambda ts,title,**kw:title+":"+str(len(ts))), \
             patch("postmaster.webgui_tracking_v985._activity_trend",
                   return_value=""):
            html=render_tracking_v985(object(),object(),request())
        self.assertIn("When emails are received:1",html)
        self.assertIn("<small>Received emails</small><strong>1</strong>",html)
        self.assertNotIn("Received-mail heatmaps require a selected",html)

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
