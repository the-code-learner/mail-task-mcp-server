from __future__ import annotations

import base64
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from postmaster.file_store import FileStore
from postmaster.mail_bridge import MailBridgeError, Settings
from postmaster.stored_file_delivery import PostmasterV946MailClient, StoredFileMailError


def source_message(*, outbound: bool) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "sender@example.test" if outbound else "external@example.net"
    msg["To"] = "external@example.net" if outbound else "sender@example.test"
    msg["Subject"] = "Stored file test"
    msg["Message-ID"] = "<source@example.test>"
    msg["References"] = "<root@example.test>"
    return msg


class CaptureClient(PostmasterV946MailClient):
    def __init__(self, settings: Settings, *, store: FileStore, source: EmailMessage, authorizer=None):
        super().__init__(settings, file_store=store, file_authorizer=authorizer)
        self.source = source
        self.sent_messages: list[EmailMessage] = []
        self.draft_messages: list[EmailMessage] = []
        self.source_attachment_payload = b"source-mail-payload"

    def _thread_source_message(self, mailbox: str, uid: str):
        return self.source

    def _validate_recipients(self, recipients):
        cleaned = [str(value).strip() for value in recipients if str(value).strip()]
        if not cleaned:
            raise MailBridgeError("At least one recipient is required")
        return cleaned

    def _send_message(self, msg, recipients):
        self.sent_messages.append(msg)
        return {
            "sent": True,
            "from": self.settings.email_address,
            "to": list(recipients),
            "subject": str(msg.get("Subject", "")),
            "message_id": "<sent@example.test>",
            "sent_copy_saved": True,
            "sent_copy_error": None,
        }

    def _save_draft(self, msg):
        self.draft_messages.append(msg)
        return {
            "draft_saved": True,
            "mailbox": "Drafts",
            "from": self.settings.email_address,
            "to": [],
            "cc": [],
            "bcc": [],
            "subject": str(msg.get("Subject", "")),
            "message_id": "<draft@example.test>",
        }

    def recipient_authorization_status(self, recipients):
        return {
            "ok": True,
            "results": [
                {"address": value, "authorized_for_automated_send": True}
                for value in recipients
            ],
        }

    def get_email_attachment(self, mailbox, uid, filename=None, index=None, include_base64=True):
        return {
            "ok": True,
            "filename": filename or "source.txt",
            "content_type": "text/plain",
            "size": len(self.source_attachment_payload),
        }

    def _find_attachment(self, mailbox, uid, filename=None, index=None):
        return None, 0, object(), self.source_attachment_payload


def attachment_parts(msg: EmailMessage):
    return [part for part in msg.walk() if part.get_content_disposition() == "attachment"]


class StoredFileAttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = FileStore(
            db_path=str(root / "files.db"),
            root=str(root / "files"),
            max_bytes=3 * 1024 * 1024,
            max_total_bytes=20 * 1024 * 1024,
        )
        self.settings = Settings(
            email_address="sender@example.test",
            email_password="pw",
            enable_send=True,
            save_sent_copy=False,
            allow_previous_sent_recipients=False,
            account_id="acct",
        )
        self.info = self.store.save_bytes(
            owner_id="owner",
            project_id="project",
            filename="image.jpg",
            media_type="image/jpeg",
            data=b"\xff\xd8stored-image\xff\xd9",
        )
        self.client = CaptureClient(
            self.settings,
            store=self.store,
            source=source_message(outbound=False),
            authorizer=lambda info: True,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_legacy_attachment_modes_and_file_id_can_be_mixed(self) -> None:
        inline_payload = b"inline-base64"
        decoded = self.client._decode_attachment_specs(
            [
                {
                    "filename": "inline.bin",
                    "content_type": "application/octet-stream",
                    "content_base64": base64.b64encode(inline_payload).decode("ascii"),
                },
                {
                    "source_mailbox": "INBOX",
                    "source_uid": "123",
                    "filename": "source.txt",
                },
                {"file_id": self.info["id"]},
            ]
        )
        self.assertEqual([item["blob"] for item in decoded], [inline_payload, b"source-mail-payload", b"\xff\xd8stored-image\xff\xd9"])
        self.assertEqual([item["filename"] for item in decoded], ["inline.bin", "source.txt", "image.jpg"])
        self.assertEqual(decoded[2]["content_type"], "image/jpeg")

    def test_file_metadata_defaults_and_mime_overrides_do_not_mutate_store(self) -> None:
        default = self.client._decode_attachment_specs([{"file_id": self.info["id"]}])[0]
        self.assertEqual(default["filename"], "image.jpg")
        self.assertEqual(default["content_type"], "image/jpeg")

        overridden = self.client._decode_attachment_specs(
            [
                {
                    "file_id": self.info["id"],
                    "filename": "renamed.jpeg",
                    "media_type": "application/octet-stream",
                }
            ]
        )[0]
        self.assertEqual(overridden["filename"], "renamed.jpeg")
        self.assertEqual(overridden["content_type"], "application/octet-stream")
        persisted = self.store.get_info(self.info["id"])
        self.assertEqual(persisted["filename"], "image.jpg")
        self.assertEqual(persisted["media_type"], "image/jpeg")

    def test_stored_file_errors_are_explicit_and_safe(self) -> None:
        with self.assertRaisesRegex(StoredFileMailError, "stored_file_not_found"):
            self.client._decode_attachment_specs([{"file_id": "missing"}])

        denied = CaptureClient(
            self.settings,
            store=self.store,
            source=source_message(outbound=False),
            authorizer=lambda info: False,
        )
        with self.assertRaisesRegex(StoredFileMailError, "stored_file_not_authorized"):
            denied._decode_attachment_specs([{"file_id": self.info["id"]}])

        with self.assertRaisesRegex(StoredFileMailError, "invalid_attachment_filename"):
            self.client._decode_attachment_specs(
                [{"file_id": self.info["id"], "filename": "../escape.jpg"}]
            )
        with self.assertRaisesRegex(StoredFileMailError, "invalid_attachment_media_type"):
            self.client._decode_attachment_specs(
                [{"file_id": self.info["id"], "media_type": "not-a-mime"}]
            )
        with self.assertRaisesRegex(StoredFileMailError, "attachment_source_conflict"):
            self.client._decode_attachment_specs(
                [
                    {
                        "file_id": self.info["id"],
                        "content_base64": base64.b64encode(b"x").decode("ascii"),
                    }
                ]
            )

    def test_missing_blob_is_distinct_from_missing_record(self) -> None:
        _, blob_path = self.store.resolve_blob(self.info["id"])
        blob_path.unlink()
        with self.assertRaisesRegex(StoredFileMailError, "stored_file_blob_missing"):
            self.client._decode_attachment_specs([{"file_id": self.info["id"]}])

    def test_individual_and_aggregate_size_limits(self) -> None:
        first = self.store.save_bytes(
            owner_id="owner",
            project_id="project",
            filename="first.bin",
            media_type="application/octet-stream",
            data=b"a" * 700_000,
        )
        second = self.store.save_bytes(
            owner_id="owner",
            project_id="project",
            filename="second.bin",
            media_type="application/octet-stream",
            data=b"b" * 700_000,
        )
        too_large = self.store.save_bytes(
            owner_id="owner",
            project_id="project",
            filename="too-large.bin",
            media_type="application/octet-stream",
            data=b"c" * 1_100_000,
        )
        with patch.dict(os.environ, {"MAX_ATTACHMENT_BYTES": "1048576"}):
            with self.assertRaisesRegex(StoredFileMailError, "attachment_size_limit_exceeded"):
                self.client._decode_attachment_specs([{"file_id": too_large["id"]}])
            with self.assertRaisesRegex(StoredFileMailError, "attachment_size_limit_exceeded"):
                self.client._decode_attachment_specs(
                    [{"file_id": first["id"]}, {"file_id": second["id"]}]
                )

    def test_reply_and_follow_up_drafts_contain_true_mime_attachment(self) -> None:
        result = self.client.create_reply_draft(
            mailbox="INBOX",
            uid="1",
            body="Draft reply",
            attachments=[{"file_id": self.info["id"]}],
        )
        self.assertTrue(result["draft_saved"])
        part = attachment_parts(self.client.draft_messages[-1])[0]
        self.assertEqual(part.get_filename(), "image.jpg")
        self.assertEqual(part.get_content_type(), "image/jpeg")
        self.assertEqual(part.get_payload(decode=True), b"\xff\xd8stored-image\xff\xd9")

        self.client.source = source_message(outbound=True)
        result = self.client.create_follow_up_draft(
            mailbox="Sent",
            uid="2",
            body="Draft follow-up",
            attachments=[{"file_id": self.info["id"]}],
        )
        self.assertTrue(result["draft_saved"])
        part = attachment_parts(self.client.draft_messages[-1])[0]
        self.assertEqual(part.get_filename(), "image.jpg")
        self.assertEqual(part.get_payload(decode=True), b"\xff\xd8stored-image\xff\xd9")

    def test_send_reply_follow_up_all_use_same_file_id_mime_builder(self) -> None:
        self.client.send_email(
            to=["external@example.net"],
            subject="Send",
            body="Body",
            attachments=[{"file_id": self.info["id"]}],
            track_opens=False,
        )
        self.assertEqual(attachment_parts(self.client.sent_messages[-1])[0].get_payload(decode=True), b"\xff\xd8stored-image\xff\xd9")

        self.client.source = source_message(outbound=False)
        self.client.reply_email(
            mailbox="INBOX",
            uid="3",
            body="Reply",
            attachments=[{"file_id": self.info["id"]}],
            track_opens=False,
        )
        self.assertEqual(attachment_parts(self.client.sent_messages[-1])[0].get_filename(), "image.jpg")

        self.client.source = source_message(outbound=True)
        self.client.follow_up_email(
            mailbox="Sent",
            uid="4",
            body="Follow-up",
            attachments=[{"file_id": self.info["id"]}],
            track_opens=False,
        )
        self.assertEqual(attachment_parts(self.client.sent_messages[-1])[0].get_content_type(), "image/jpeg")

    def test_file_store_to_reply_draft_and_send_never_uses_base64_readback(self) -> None:
        with patch.object(self.store, "read_base64", side_effect=AssertionError("base64 readback forbidden")):
            self.client.create_reply_draft(
                mailbox="INBOX",
                uid="5",
                body="Review me",
                attachments=[{"file_id": self.info["id"]}],
            )
            draft_part = attachment_parts(self.client.draft_messages[-1])[0]
            self.assertEqual(draft_part.get_filename(), "image.jpg")
            self.client.reply_email(
                mailbox="INBOX",
                uid="5",
                body="Send reviewed reply",
                attachments=[{"file_id": self.info["id"]}],
                track_opens=False,
            )
            sent_part = attachment_parts(self.client.sent_messages[-1])[0]
            self.assertEqual(sent_part.get_payload(decode=True), b"\xff\xd8stored-image\xff\xd9")


if __name__ == "__main__":
    unittest.main()
