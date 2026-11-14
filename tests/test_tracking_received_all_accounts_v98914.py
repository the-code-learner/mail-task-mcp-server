"""Regression tests: All accounts received-mail Tracking heatmap is not blank."""
from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from postmaster.mailbox_cache_v963 import MailboxCacheStore
from postmaster.tracking_received_cache_v98914 import cached_received_dates
from postmaster import webgui_tracking_v985
from starlette.requests import Request


class _Base:
    def __init__(self, db):
        self.store=MailboxCacheStore(db)

    def _safe_call(self, fn, *a, **k):
        return fn(*a, **k)

    def list_email_accounts(self):
        return [
            {"id":"alpha","enabled":True,"label":"Alpha"},
            {"id":"beta","enabled":True,"label":"Beta"},
            {"id":"disabled","enabled":False,"label":"Disabled"},
        ]

    def mailbox_cache_store(self):
        return self.store


class ReceivedHeatmapAllAccountsV98914(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=_Base(str(Path(self.temp.name)/"mailcache.db"))
        with self.base.store._connect() as conn:
            conn.executemany(
                "INSERT INTO mailbox_cache_mailboxes(account_id,name,role) VALUES (?,?,?)",
                [("alpha","INBOX","received"),("beta","Posta in arrivo","received"),
                 ("alpha","Sent","sent"),("disabled","INBOX","received")]
            )
            conn.executemany(
                "INSERT INTO mailbox_cache_messages("
                "account_id,mailbox,uid,uid_int,date_value,cached_at) "
                "VALUES (?,?,?,?,?,?)",
                [
                    ("alpha","INBOX","100",100,"2026-11-13T08:00:00+00:00","x"),
                    ("beta","Posta in arrivo","100",100,"2026-11-13T09:00:00+00:00","x"),
                    ("alpha","Sent","200",200,"2026-11-13T10:00:00+00:00","x"),
                    ("disabled","INBOX","300",300,"2026-11-13T11:00:00+00:00","x"),
                    ("alpha","INBOX","101",101,"2026-10-01T07:00:00+00:00","x"),
                ]
            )
            conn.commit()
        self.since=datetime(2026,11,7,tzinfo=UTC)
        self.until=datetime(2026,11,15,tzinfo=UTC)

    def test_all_accounts_aggregates_two_enabled_inboxes_and_not_sent_or_disabled(self):
        dates,note=cached_received_dates(self.base,"",self.since,self.until)
        self.assertEqual(len(dates),2)
        self.assertIn("all 2 enabled accounts",note)
        self.assertIn("already synchronized",note)
        self.assertTrue(all("2026-11-13" in s for s in dates))

    def test_explicit_account_is_restricted_and_invalid_account_never_leaks(self):
        alpha,_=cached_received_dates(self.base,"alpha",self.since,self.until)
        self.assertEqual(len(alpha),1)
        disabled,_=cached_received_dates(self.base,"disabled",self.since,self.until)
        self.assertEqual(disabled,[])

    def test_date_window_changes_cached_received_counts(self):
        dates,_=cached_received_dates(
            self.base,"",datetime(2026,11,13,8,30,tzinfo=UTC),
            datetime(2026,11,14,tzinfo=UTC)
        )
        self.assertEqual(len(dates),1)

    def test_tracking_all_accounts_renders_nonempty_heatmap_and_received_stat(self):
        req=Request({"type":"http","method":"GET","path":"/",
                     "query_string":b"ui_view=tracking&period=7d","headers":[]})
        with patch.object(webgui_tracking_v985,"_period",return_value=(self.since,self.until,"7d")), \
             patch.object(webgui_tracking_v985,"scoped_events",return_value=([],False)), \
             patch.object(webgui_tracking_v985,"_sent",return_value=([],False)), \
             patch.object(webgui_tracking_v985,"_activity_trend",return_value=""), \
             patch.object(webgui_tracking_v985,"geoip_status",return_value={
                 "city_database_available":True,"asn_database_available":True,
                 "isp_database_available":False,
             }):
            html=webgui_tracking_v985.render_tracking_v985(self.base,object(),req)
        self.assertIn("When emails are received",html)
        self.assertIn("Cached received mail across enabled accounts",html)
        self.assertIn("2 enabled accounts",html)
        self.assertIn("Fri 08:00: 1 event(s)",html)
        self.assertIn("Fri 09:00: 1 event(s)",html)
        self.assertNotIn("Select one email account",html)
        self.assertNotIn("Choose an email account",html)


if __name__=="__main__":
    unittest.main()
