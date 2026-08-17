from __future__ import annotations

import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from nomadcompass.hostinger_mail import _message_to_dict


class MimeBodyRegressionTests(unittest.TestCase):
    def row(self, msg: EmailMessage) -> dict:
        parsed = BytesParser(policy=policy.default).parsebytes(msg.as_bytes(policy=policy.default))
        return _message_to_dict(parsed, uid="1", mailbox="INBOX", include_body=True, truncated=False)

    def test_short_plain_forward_note_does_not_hide_rich_html(self) -> None:
        msg = EmailMessage()
        msg["From"] = "forwarder@example.test"
        msg["To"] = "reader@example.test"
        msg["Subject"] = "Fwd: jobs"
        msg.set_content("--\nInviato da Web Mail")
        msg.add_alternative(
            """<html><body><h1>Messaggio inoltrato</h1>
            <p>DA: Newsletter &lt;news@example.test&gt;</p>
            <p>OGGETTO: 61 new roles</p>
            <p>Program Manager and several engineering roles are available.</p>
            <a href="https://example.test/jobs/61">View the roles</a>
            </body></html>""",
            subtype="html",
        )
        row = self.row(msg)
        self.assertEqual(row["body_source"], "html-rich")
        self.assertIn("61 new roles", row["body"])
        self.assertIn("https://example.test/jobs/61", row["body"])
        self.assertIn("https://example.test/jobs/61", row["body_html"])
        self.assertGreater(row["body_html_significant_chars"], row["body_plain_significant_chars"])
        self.assertFalse(row["content_truncated"])

    def test_normal_rich_plain_body_remains_preferred(self) -> None:
        msg = EmailMessage()
        msg["Subject"] = "Normal alternative"
        plain = "This is the complete plain text message with enough useful information for the reader."
        msg.set_content(plain)
        msg.add_alternative(f"<p>{plain}</p>", subtype="html")
        row = self.row(msg)
        self.assertEqual(row["body_source"], "plain")
        self.assertEqual(row["body"], plain)
        self.assertTrue(row["body_html"])

    def test_html_only_body_preserves_links_in_text_and_html(self) -> None:
        msg = EmailMessage()
        msg["Subject"] = "HTML only"
        msg.set_content('<p>Hello <a href="https://example.test/a">world</a></p>', subtype="html")
        row = self.row(msg)
        self.assertEqual(row["body_source"], "html")
        self.assertIn("Hello world", row["body"])
        self.assertIn("https://example.test/a", row["body"])
        self.assertIn("href=\"https://example.test/a\"", row["body_html"])

    def test_message_rfc822_is_traversed_and_headers_are_visible(self) -> None:
        nested = EmailMessage()
        nested["From"] = "alerts@example.test"
        nested["To"] = "reader@example.test"
        nested["Subject"] = "Original alert"
        nested.set_content("Original nested body")
        nested.add_alternative(
            '<p>Original nested body with <a href="https://example.test/alert">alert link</a>.</p>',
            subtype="html",
        )

        outer = EmailMessage()
        outer["From"] = "forwarder@example.test"
        outer["To"] = "reader@example.test"
        outer["Subject"] = "Fwd: Original alert"
        outer.set_content("FYI")
        outer.add_attachment(nested)

        row = self.row(outer)
        self.assertEqual(row["forwarded_message_count"], 1)
        self.assertIn("Subject: Original alert", row["body"])
        self.assertIn("Original nested body", row["body"])
        self.assertIn("https://example.test/alert", row["body_html"])

    def test_text_attachment_is_not_treated_as_body(self) -> None:
        msg = EmailMessage()
        msg.set_content("Actual message body")
        msg.add_attachment(b"secret attachment text", maintype="text", subtype="plain", filename="note.txt")
        row = self.row(msg)
        self.assertIn("Actual message body", row["body"])
        self.assertNotIn("secret attachment text", row["body"])
        self.assertIn("note.txt", row["attachments"])


if __name__ == "__main__":
    unittest.main()
