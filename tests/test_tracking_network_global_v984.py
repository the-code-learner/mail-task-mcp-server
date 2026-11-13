from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from postmaster.tracking_geoip_v984 import (
    ensure_email_geo_columns, global_tracking_network_events, persist_generic_network,
)
from postmaster.tracking_telemetry_v971 import _ensure_schema


class FakeAnalytics:
    def __init__(self, db):
        self.db = str(db)
    def _connect(self):
        db = sqlite3.connect(self.db, timeout=5)
        db.row_factory = sqlite3.Row
        return db


class TrackingMCPGlobalV984Tests(unittest.TestCase):
    def test_mixed_email_and_generic_events_are_globally_visible(self):
        with TemporaryDirectory() as td:
            analytics = FakeAnalytics(Path(td) / "tracking.sqlite")
            with analytics._connect() as conn:
                conn.executescript("""
                CREATE TABLE tracking_opens (
                    id INTEGER PRIMARY KEY, account_id TEXT,recipient TEXT,
                    delivery_id TEXT, user_agent TEXT,browser TEXT,os TEXT,
                    client_source TEXT,metadata_confidence TEXT
                );
                CREATE TABLE tracking_clicks (
                    id INTEGER PRIMARY KEY,account_id TEXT, recipient TEXT,
                    delivery_id TEXT, link_id TEXT, user_agent TEXT,
                    browser TEXT,os TEXT,client_source TEXT,metadata_confidence TEXT
                );
                CREATE TABLE generic_tracking_links (
                    id TEXT PRIMARY KEY, label TEXT, target_type TEXT
                );
                CREATE TABLE generic_tracking_clicks (
                    id INTEGER PRIMARY KEY, tracked_link_id TEXT,
                    user_agent TEXT, browser TEXT, os TEXT,
                    client_source TEXT,metadata_confidence TEXT
                );
                INSERT INTO tracking_opens VALUES
                    (1,'mail-1','recipient@example.com','del-1','ExampleClient','Browser','OS','pixel','low');
                INSERT INTO tracking_clicks VALUES
                    (2,'mail-2','other@example.net','del-2','click-1','ClientClick','Browser','OS','link','medium');
                INSERT INTO generic_tracking_links VALUES
                    ('link-1','File URL','stored_file');
                INSERT INTO generic_tracking_clicks VALUES
                    (3,'link-1','Downloader','Browser','OS','link','medium');
                """)
            _ensure_schema(analytics)
            ensure_email_geo_columns(analytics)
            with analytics._connect() as conn:
                conn.executemany(
                    """INSERT INTO tracking_sensitive_telemetry(
                       event_kind,event_id,account_id,delivery_id,observed_at,captured_at,
                       source_ip,geo_country_code
                    ) VALUES (?,?,?,?,?,?,?,?)""",
                    [
                        ("open",1,"mail-1","del-1","2026-11-13T20:00:00+00:00","2026-11-13","8.8.8.8","US"),
                        ("click",2,"mail-2","del-2","2026-11-13T21:00:00+00:00","2026-11-13","1.1.1.1","AU"),
                    ],
                )
            persist_generic_network(
                analytics, event_id=3,
                link={"id":"link-1","owner_id":"alice","project_id":"project-1"},
                client_ip="2001:4860:4860::8888",country_code="US",
                observed_at="2026-11-13T22:00:00+00:00",
            )
            global_result = global_tracking_network_events(analytics)
            self.assertTrue(global_result["ok"])
            self.assertEqual(len(global_result["events"]),3)
            self.assertEqual(
                {x["event_kind"] for x in global_result["events"]},
                {"email_open","email_click","tracked_file_download"},
            )
            self.assertEqual(global_result["events"][0]["source_ip"],"2001:4860:4860::8888")
            self.assertEqual(global_result["access_scope"],"MCP_instance_global")
            self.assertEqual(
                [x["event_kind"] for x in global_tracking_network_events(analytics,account_id="mail-2")["events"]],
                ["email_click"],
            )
            self.assertEqual(
                [x["event_kind"] for x in global_tracking_network_events(analytics,owner_id="alice")["events"]],
                ["tracked_file_download"],
            )
            self.assertEqual(global_tracking_network_events(analytics,owner_id="nobody")["events"],[])
            self.assertEqual(len(global_tracking_network_events(analytics,limit=1,offset=1)["events"]),1)

if __name__ == "__main__":
    unittest.main()
