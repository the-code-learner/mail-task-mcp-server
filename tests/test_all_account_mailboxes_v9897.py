"""All accounts is a genuine aggregate mailbox, never a default-account alias."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from starlette.requests import Request

from postmaster.mailbox_cache_v963 import MailboxCacheStore
from postmaster import webgui_v963
from postmaster.webgui_mail_v986 import modern_inbox_markup


class _AccountStore:
    def get_account(self, account_id):
        return {"id": account_id, "inbox_mailbox": "INBOX"}


class _Base:
    def __init__(self, store):
        self.store=store

    def _safe_call(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def list_email_accounts(self):
        return [
            {"id":"a","label":"Account A","email_address":"a@example.invalid",
             "enabled":True,"is_default":True},
            {"id":"b","label":"Account B","email_address":"b@example.invalid",
             "enabled":True},
        ]

    def account_store(self):
        return _AccountStore()

    def mailbox_cache_store(self):
        return self.store

    def _csrf_value(self):
        return "test-only-csrf"


def _request(query):
    return Request({"type":"http","method":"GET","path":"/dashboard/view/inbox",
                    "query_string":query.encode("utf-8")})


class AllAccountMailboxesV9897(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="mailbox-unified-test-")
        self.store=MailboxCacheStore(str(Path(self.tmp.name)/"messages.db"))
        for a, folders in (("a",("INBOX","Sent","Drafts","Spam")),
                           ("b",("INBOX","Sent Items","Drafts","Junk"))):
            roles=("received","sent","drafts","spam")
            self.store.replace_mailboxes(a, [
                {"name":m,"role":role} for m,role in zip(folders,roles)
            ],synced_at="2026-11-14T00:00:00+00:00")
        self.base=_Base(self.store)
        self.add("a","INBOX","100","2026-11-13T11:00:00+00:00","From A")
        self.add("b","INBOX","100","2026-11-14T12:00:00+00:00","From B")
        self.add("a","Sent","7","2026-11-13T11:00:00+00:00","Sent A")
        self.add("b","Sent Items","7","2026-11-14T12:00:00+00:00","Sent B")
        self.add("a","Drafts","8","2026-11-13T11:00:00+00:00","Draft A")
        self.add("b","Drafts","8","2026-11-14T12:00:00+00:00","Draft B")
        self.add("a","Spam","9","2026-11-13T11:00:00+00:00","Spam A")
        self.add("b","Junk","9","2026-11-14T12:00:00+00:00","Spam B")
        self.patcher=patch.object(webgui_v963,"_proxy_card",return_value="")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def add(self, account, mailbox, uid, stamp, subject):
        with self.store._connect() as conn:
            conn.execute(
                "INSERT INTO mailbox_cache_messages "
                "(account_id,mailbox,uid,uid_int,date_value,subject,from_text,"
                "to_text,snippet,cached_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (account,mailbox,uid,int(uid),stamp,subject,
                 "Sender "+account+" <s@example.invalid>",
                 "recipient@example.invalid","Snippet "+subject,stamp)
            )
            conn.commit()

    def render(self, query):
        return webgui_v963.render_inbox_v963(self.base,_request(query))

    # Exact-commit CI release gate for the live multi-account renderer and account-scoped UIDs.
    def test_all_accounts_inbox_combines_accounts_not_default_only(self):
        html=self.render("ui_view=inbox&mailbox=INBOX")
        self.assertIn("From A",html)
        self.assertIn("From B",html)
        self.assertLess(html.index("From B"),html.index("From A"))
        self.assertIn("message_account_id=a",html)
        self.assertIn("message_account_id=b",html)
        self.assertIn('value="" selected',html)
        self.assertIn("All accounts",html)
        self.assertNotIn('action="/dashboard/inbox/refresh"',html)
        self.assertIn("Select an email account above",html)
        transformed=modern_inbox_markup(html,query_string="ui_view=inbox&mailbox=INBOX")
        self.assertIn('class="v989-account-origin"',transformed)
        self.assertIn("Account A",transformed)
        self.assertIn("Account B",transformed)
        self.assertIn('class="v989-reading-pane"',transformed)

    def test_specific_account_filter_shows_only_selected_account(self):
        html=self.render("ui_view=inbox&mailbox=INBOX&account_id=b")
        self.assertIn("From B",html)
        self.assertNotIn("From A",html)
        self.assertIn('action="/dashboard/inbox/refresh"',html)

    def test_all_important_folders_are_role_mapped_across_providers(self):
        for requested,one,two in (
            ("Sent","Sent A","Sent B"),
            ("Drafts","Draft A","Draft B"),
            ("Spam","Spam A","Spam B"),
        ):
            with self.subTest(folder=requested):
                html=self.render("ui_view=inbox&mailbox="+requested)
                self.assertIn(one,html)
                self.assertIn(two,html)

    def test_equal_uids_are_distinguished_by_source_account(self):
        html=self.render("ui_view=inbox&mailbox=INBOX&message_uid=100"
                         "&message_account_id=b&message_mailbox=INBOX")
        self.assertIn("From A",html)
        self.assertIn("From B",html)
        with patch.object(webgui_v963,"_detail",return_value='<div class="v963-detail">SAFE B</div>') as detail:
            result=self.render("ui_view=inbox&mailbox=INBOX&message_uid=100"
                               "&message_account_id=b&message_mailbox=INBOX")
        self.assertEqual(detail.call_count,1)
        self.assertEqual(detail.call_args.args[2],"b")
        self.assertEqual(detail.call_args.args[3],"INBOX")
        self.assertIn("SAFE B",result)

    def test_aggregate_cache_query_is_paginated_across_accounts(self):
        q=self.store.query_messages_across_accounts(
            account_mailboxes=[("a","INBOX"),("b","INBOX")],page=1,page_size=1
        )
        self.assertEqual(q["total"],2)
        self.assertEqual(q["messages"][0]["account_id"],"b")
        second=self.store.query_messages_across_accounts(
            account_mailboxes=[("a","INBOX"),("b","INBOX")],page=2,page_size=1
        )
        self.assertEqual(second["messages"][0]["account_id"],"a")

    def test_aggregate_rejects_unlisted_account_sources(self):
        q=self.store.query_messages_across_accounts(
            account_mailboxes=[("a","INBOX")],page=1
        )
        self.assertEqual(q["total"],1)
        self.assertEqual(q["messages"][0]["account_id"],"a")


if __name__=="__main__":
    unittest.main()
