from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from postmaster.email_search_v990 import HybridEmailIndex, combine_attachment_text, extract_attachment_text


def fake_embed(text: str):
    low = text.lower()
    # Deliberately tiny deterministic semantic space for unit tests.
    return np.array([
        low.count("invoice") + low.count("fattura"),
        low.count("meeting") + low.count("riunione"),
        low.count("printer") + low.count("stampante"),
    ], dtype=np.float32)


class EmailSearchV990Tests(unittest.TestCase):
    def test_hybrid_search_body_and_attachment_text(self):
        with TemporaryDirectory() as td:
            index = HybridEmailIndex(Path(td) / "index.db", embed_one=fake_embed, model_id="test")
            index.upsert({
                "account_id": "a", "mailbox": "INBOX", "uid": "1", "subject": "Q3 document",
                "body_text": "Here is the finance document", "attachments_text": "invoice total 1200 EUR",
                "date_utc": "2026-09-20T10:00:00+00:00",
            })
            index.upsert({
                "account_id": "a", "mailbox": "INBOX", "uid": "2", "subject": "Team meeting",
                "body_text": "Weekly meeting", "date_utc": "2026-09-20T11:00:00+00:00",
            })
            result = index.search("fattura", account_id="a", since_days=None)
            self.assertEqual(result["results"][0]["uid"], "1")
            self.assertTrue(result["semantic_active"])
            self.assertTrue(result["results"][0]["attachment_text_indexed"])

    def test_local_attachment_extraction_never_fetches_network(self):
        msg = EmailMessage()
        msg["Subject"] = "Files"
        msg.set_content("body")
        msg.add_attachment(b"hello attachment", maintype="text", subtype="plain", filename="notes.txt")
        extracted = extract_attachment_text(msg.as_bytes())
        self.assertEqual(extracted[0]["text"], "hello attachment")
        self.assertFalse(extracted[0]["network_access"])
        combined, metadata = combine_attachment_text(extracted)
        self.assertIn("hello attachment", combined)
        self.assertEqual(metadata[0]["filename"], "notes.txt")

    def test_scope_is_account_bound(self):
        with TemporaryDirectory() as td:
            index = HybridEmailIndex(Path(td) / "index.db", embed_one=fake_embed, model_id="test")
            index.upsert({"account_id": "a", "mailbox": "INBOX", "uid": "1", "subject": "invoice"})
            index.upsert({"account_id": "b", "mailbox": "INBOX", "uid": "1", "subject": "invoice secret"})
            result = index.search("invoice", account_id="a", since_days=None)
            self.assertEqual({row["account_id"] for row in result["results"]}, {"a"})


if __name__ == "__main__":
    unittest.main()
