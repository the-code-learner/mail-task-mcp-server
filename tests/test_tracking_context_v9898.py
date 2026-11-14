"""Tracking event context links and safe display contracts for beta.8."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from postmaster.tracking_presentation_v9898 import attach_sent_email_links
from postmaster.webgui_tracking_v984 import _event_html


class Store:
    def __init__(self, path):
        self.path=path
    def _connect(self):
        conn=sqlite3.connect(self.path)
        conn.row_factory=sqlite3.Row
        return conn


class Base:
    def __init__(self, path):
        self.store=Store(path)
    def mailbox_cache_store(self):
        return self.store


class TrackingContextV9898(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="tracking-context-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base=Base(str(Path(self.tmp.name)/"mail.db"))
        with self.base.store._connect() as conn:
            conn.executescript("""
              CREATE TABLE mailbox_cache_mailboxes (account_id TEXT,name TEXT,role TEXT);
              CREATE TABLE mailbox_cache_messages (
                account_id TEXT,mailbox TEXT,uid TEXT,uid_int INTEGER,
                message_id TEXT,date_value TEXT
              );
              INSERT INTO mailbox_cache_mailboxes VALUES
                ('a','Sent','sent'),('b','Sent Items','sent'),('a','INBOX','received');
              INSERT INTO mailbox_cache_messages VALUES
                ('a','Sent','27',27,'<same@invalid>','2026-11-11'),
                ('b','Sent Items','88',88,'<same@invalid>','2026-11-12'),
                ('a','INBOX','51',51,'<inbox-only@invalid>','2026-11-13');
            """)

    def test_deep_link_finds_exact_source_sent_uid_and_mailbox(self):
        events=[
            {"event_kind":"email_open","account_id":"a","message_id":"<same@invalid>"},
            {"event_kind":"email_click","account_id":"b","message_id":"<same@invalid>"},
        ]
        attach_sent_email_links(self.base,events)
        self.assertIn("account_id=a",events[0]["email_href"])
        self.assertIn("message_uid=27",events[0]["email_href"])
        self.assertIn("account_id=b",events[1]["email_href"])
        self.assertIn("mailbox=Sent+Items",events[1]["email_href"])
        self.assertIn("message_uid=88",events[1]["email_href"])
        self.assertNotEqual(events[0]["email_href"],events[1]["email_href"])

    def test_missing_or_inbox_only_message_never_gets_false_sent_link(self):
        events=[
            {"event_kind":"email_open","account_id":"a","message_id":"<inbox-only@invalid>"},
            {"event_kind":"email_open","account_id":"c","message_id":"<same@invalid>"},
            {"event_kind":"email_open","account_id":"a","message_id":"missing"},
        ]
        attach_sent_email_links(self.base,events)
        self.assertTrue(all("email_href" not in row for row in events))

    def test_file_event_shows_filename_without_technical_id_as_primary_label(self):
        html=_event_html({"event_kind":"tracked_file_download",
                          "download_filename":"Quarterly report.pdf",
                          "tracked_link_id":"link-opaque-id",
                          "geo_country_code":"IT"})
        self.assertIn("File: Quarterly report.pdf",html)
        self.assertIn("Identificativi tecnici",html)
        self.assertIn("link-opaque-id",html)
        self.assertNotIn("Linked email / tracked link",html)

    def test_link_event_shows_escaped_url_and_prevents_unsafe_schemes(self):
        html=_event_html({"event_kind":"tracked_url_click",
                          "original_url":"https://example.org/files?a=1&b=2"})
        self.assertIn("https://example.org/files?a=1&amp;b=2",html)
        self.assertIn('rel="noopener noreferrer"',html)
        unsafe=_event_html({"event_kind":"tracked_url_click",
                            "original_url":"javascript:alert(1)"})
        self.assertNotIn('href="javascript:',unsafe)
        malformed=_event_html({"event_kind":"tracked_url_click",
                               "original_url":"http://[invalid"})
        self.assertIn("http://[invalid",malformed)

    def test_email_context_subject_recipient_and_safe_mail_link(self):
        html=_event_html({
            "event_kind":"email_click","email_subject":"Project & proposal",
            "recipient":"person@example.invalid","original_url":"https://example.org/proposal",
            "email_href":"/?ui_view=inbox&mailbox=Sent&account_id=a&message_uid=27#inbox",
        })
        self.assertIn("Project &amp; proposal",html)
        self.assertIn("Destinatario: person@example.invalid",html)
        self.assertIn("Destinazione del link",html)
        self.assertIn("Apri email in Sent",html)
        self.assertIn('data-v960-fragment="inbox"',html)
        no_link=_event_html({"event_kind":"email_open","email_subject":"A"})
        self.assertIn("non disponibile nella cache Sent",no_link)
        self.assertNotIn("Apri email in Sent",no_link)


if __name__=="__main__":
    unittest.main()
