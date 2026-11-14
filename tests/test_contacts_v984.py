from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from postmaster.contacts_v984 import ContactBook, incoming_candidates


class ContactsV984Tests(TestCase):
    def test_save_does_not_grant_sending_permission(self):
        with TemporaryDirectory() as tmp:
            book = ContactBook(str(Path(tmp) / "contacts.db"))
            saved = book.save(
                email="Sample <sample@example.com>", name="Sample",
                owner_id="alpha", project_id="project-a",
                source_account_id="mail-1",
            )
            self.assertTrue(saved["ok"])
            self.assertFalse(saved["recipient_authorized"])
            contacts = book.list()
            self.assertEqual(contacts["count"], 1)
            self.assertEqual(contacts["contacts"][0]["email"], "sample@example.com")
            self.assertEqual(contacts["contacts"][0]["owner_id"], "alpha")
            self.assertEqual(book.list(owner_id="other")["count"], 0)
            # Filtering on owner_id is organizational, not an ACL.
            self.assertEqual(book.list()["count"], 1)

    def test_ignore_restore_and_readd(self):
        with TemporaryDirectory() as tmp:
            book = ContactBook(str(Path(tmp) / "contacts.db"))
            ignored = book.set_ignored(email="someone@example.net", ignored=True)
            self.assertEqual(ignored["status"], "ignored")
            self.assertEqual(book.list(status="ignored")["count"], 1)
            self.assertFalse(book.set_ignored(email="someone@example.net", ignored=False)["recipient_authorized"])
            self.assertEqual(book.list()["count"], 0)
            book.save(email="someone@example.net")
            book.set_ignored(email="someone@example.net", ignored=True)
            self.assertEqual(book.list(status="saved")["count"], 0)
            book.save(email="someone@example.net")
            self.assertEqual(book.list(status="saved")["count"], 1)

    def test_reject_invalid_addresses(self):
        with TemporaryDirectory() as tmp:
            book = ContactBook(str(Path(tmp) / "contacts.db"))
            for bad in ("", "not-an-email", "x@example.com\r\nBcc: bad@example.org"):
                with self.assertRaises(ValueError):
                    book.save(email=bad)

    def test_inbox_candidates_are_review_only_and_account_filtered(self):
        class Mail:
            def search_emails(self, *, mailbox, since_days, limit):
                assert mailbox == "INBOX"
                return [
                    {"from": "Jane <jane@example.com>", "date": "2026-11-13T18:00:00+00:00"},
                    {"from": "Jane <jane@example.com>", "date": "2026-11-12T18:00:00+00:00"},
                ]
        class Accounts:
            def list_accounts(self):
                return [{"id": "mail-1"}, {"id": "mail-2"}]
        class Base:
            def account_store(self):
                return Accounts()
            def mail_client(self, aid):
                return Mail()
        with TemporaryDirectory() as tmp:
            book = ContactBook(str(Path(tmp) / "contacts.db"))
            rows = incoming_candidates(Base(), book, account_id="mail-2", limit=10)
            self.assertTrue(rows["ok"])
            self.assertEqual(rows["count"], 1)
            self.assertEqual(rows["contacts"][0]["account_id"], "mail-2")
            self.assertEqual(rows["authorization_effect"], "none")
            self.assertEqual(book.list()["count"], 0)
