from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from postmaster.delivery_reliability import ReliabilityStore
from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.outbound_operations_v969 import OutboundOperationStore
from postmaster import outbound_archive_v969 as archive


class FanoutReplyMappingV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.analytics_db = root / "analytics.db"
        EmailAnalyticsStore(
            str(self.analytics_db),
            str(root / "analytics.key"),
        )
        self.reliability = ReliabilityStore(str(self.analytics_db))
        self.logical = OutboundOperationStore(root / "logical.db")
        self.operation_id = "out_fanout_reply_mapping"
        self.m1 = "<delivery-one@example.test>"
        self.m2 = "<delivery-two@example.test>"
        self.canonical = "<canonical-sent@example.test>"
        self.logical.record_operation(
            operation_id=self.operation_id,
            account_id="acct",
            canonical_message_id=self.canonical,
            canonical_sent_archived=True,
            to=["one@example.test", "two@example.test"],
            cc=[],
            bcc=[],
            deliveries=[
                {
                    "delivery_id": "d1",
                    "message_id": self.m1,
                    "recipient": "one@example.test",
                    "role": "to",
                },
                {
                    "delivery_id": "d2",
                    "message_id": self.m2,
                    "recipient": "two@example.test",
                    "role": "to",
                },
            ],
        )
        now = datetime.now(timezone.utc).isoformat()
        expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with sqlite3.connect(self.analytics_db) as conn:
            conn.execute(
                "INSERT INTO tracking_campaigns(id,account_id,sender,subject,created_at) VALUES(?,?,?,?,?)",
                ("campaign", "acct", "sender@example.test", "Fanout", now),
            )
            for did, recipient, mid in (
                ("d1", "one@example.test", self.m1),
                ("d2", "two@example.test", self.m2),
            ):
                conn.execute(
                    """
                    INSERT INTO tracking_deliveries(
                        id,campaign_id,account_id,recipient,recipient_role,
                        tracking_token,amp_token,amp_expires_at,message_id,sent_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        did,
                        "campaign",
                        "acct",
                        recipient,
                        "to",
                        "track-" + did,
                        "amp-" + did,
                        expiry,
                        mid,
                        now,
                    ),
                )

    @staticmethod
    def _reply(target: str) -> bytes:
        msg = EmailMessage()
        msg["Message-ID"] = "<reply@example.test>"
        msg["From"] = "recipient@example.test"
        msg["To"] = "sender@example.test"
        msg["Subject"] = "Re: Fanout"
        msg["In-Reply-To"] = target
        msg["References"] = "<unrelated@example.test> " + target
        msg.set_content("Reply")
        return msg.as_bytes()

    def _operation_count(self) -> int:
        with sqlite3.connect(self.logical.db_path) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM outbound_operations_v969"
                ).fetchone()[0]
            )

    def test_second_delivery_message_id_resolves_exact_delivery_and_same_logical_root(self):
        before = self._operation_count()
        with patch.object(
            archive, "outbound_operation_store", return_value=self.logical
        ):
            archive._install_outbound_archive_boundary()
            result = self.reliability.process_inbound(
                self._reply(self.m2), account_id="acct"
            )

        self.assertEqual(result["kind"], "replied")
        self.assertEqual(result["delivery_id"], "d2")
        self.assertEqual(result["logical_outbound_operation_id"], self.operation_id)
        correlation = result["logical_outbound_correlation"]
        self.assertEqual(correlation["matched_delivery_id"], "d2")
        self.assertEqual(correlation["matched_delivery_message_id"], self.m2)
        self.assertEqual(correlation["canonical_sent_message_id"], self.canonical)
        self.assertNotEqual(correlation["canonical_sent_message_id"], self.m2)
        self.assertFalse(result["logical_outbound_root_created"])
        self.assertEqual(self._operation_count(), before)

    def test_first_delivery_message_id_uses_same_root_too(self):
        before = self._operation_count()
        with patch.object(
            archive, "outbound_operation_store", return_value=self.logical
        ):
            archive._install_outbound_archive_boundary()
            result = self.reliability.process_inbound(
                self._reply(self.m1), account_id="acct"
            )
        self.assertEqual(result["delivery_id"], "d1")
        self.assertEqual(result["logical_outbound_operation_id"], self.operation_id)
        self.assertEqual(result["logical_outbound_correlation"]["matched_delivery_id"], "d1")
        self.assertEqual(self._operation_count(), before)


class SharedMessageIdReplyResolutionV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logical = OutboundOperationStore(Path(self.temp.name) / "logical.db")
        self.operation_id = "out_group"
        self.message_id = "<shared-group@example.test>"
        self.logical.record_operation(
            operation_id=self.operation_id,
            account_id="acct",
            canonical_message_id=self.message_id,
            canonical_sent_archived=True,
            to=["a@example.com"],
            cc=["b@example.com"],
            bcc=["c@example.com"],
            deliveries=[],
            recipient_mappings=[
                {
                    "message_id": self.message_id,
                    "recipient": "a@example.com",
                    "role": "to",
                },
                {
                    "message_id": self.message_id,
                    "recipient": "b@example.com",
                    "role": "cc",
                },
                {
                    "message_id": self.message_id,
                    "recipient": "c@example.com",
                    "role": "bcc",
                },
            ],
        )

    def _reply(self, sender: str) -> bytes:
        msg = EmailMessage()
        msg["Message-ID"] = "<reply-shared@example.test>"
        msg["From"] = sender
        msg["To"] = "sender@example.test"
        msg["Subject"] = "Re: Group"
        msg["In-Reply-To"] = self.message_id
        msg.set_content("Reply")
        return msg.as_bytes()

    def test_shared_message_id_from_to_recipient_resolves_logical_mapping_not_delivery(self):
        correlation = self.logical.resolve_reply("acct", self._reply("a@example.com"))
        self.assertEqual(correlation["logical_outbound_operation_id"], self.operation_id)
        self.assertEqual(correlation["matched_delivery_id"], "")
        self.assertEqual(correlation["recipient"], "a@example.com")
        self.assertEqual(correlation["recipient_role"], "to")

    def test_shared_message_id_from_cc_recipient_resolves_logical_mapping_not_delivery(self):
        correlation = self.logical.resolve_reply("acct", self._reply("b@example.com"))
        self.assertEqual(correlation["logical_outbound_operation_id"], self.operation_id)
        self.assertEqual(correlation["matched_delivery_id"], "")
        self.assertEqual(correlation["recipient"], "b@example.com")
        self.assertEqual(correlation["recipient_role"], "cc")

    def test_shared_message_id_from_bcc_recipient_is_private_match_and_public_role_is_hidden(self):
        correlation = self.logical.resolve_reply("acct", self._reply("c@example.com"))
        self.assertEqual(correlation["logical_outbound_operation_id"], self.operation_id)
        self.assertEqual(correlation["matched_delivery_id"], "")
        self.assertEqual(correlation["recipient"], "c@example.com")
        self.assertEqual(correlation["recipient_role"], "bcc")

        public = archive._public_reply_correlation(correlation)
        self.assertEqual(public["logical_outbound_operation_id"], self.operation_id)
        self.assertEqual(public["recipient"], "c@example.com")
        self.assertEqual(public["recipient_role"], "")
        self.assertNotIn("bcc", repr(public).casefold())

    def test_shared_message_id_unknown_sender_finds_operation_but_invents_no_exact_match(self):
        correlation = self.logical.resolve_reply(
            "acct", self._reply("unknown@example.com")
        )
        self.assertEqual(correlation["logical_outbound_operation_id"], self.operation_id)
        self.assertEqual(correlation["matched_delivery_id"], "")
        self.assertEqual(correlation["recipient"], "")
        self.assertEqual(correlation["recipient_role"], "")
        self.assertEqual(correlation["matched_delivery_message_id"], self.message_id)

    def test_shared_message_id_has_three_mappings_and_zero_persisted_deliveries(self):
        operation = self.logical.get_operation(self.operation_id)
        self.assertEqual(operation["deliveries"], [])
        self.assertEqual(len(operation["recipient_mappings"]), 3)
        self.assertIsNone(self.logical.delivery_by_message_id("acct", self.message_id))
        self.assertEqual(
            len(self.logical.recipient_mappings_by_message_id("acct", self.message_id)),
            3,
        )


class LegacyPseudoDeliveryMigrationV969Tests(unittest.TestCase):
    def test_unreleased_pseudo_delivery_rows_migrate_to_recipient_mappings(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "logical.db"
            store = OutboundOperationStore(path)
            store.record_operation(
                operation_id="out_legacy",
                account_id="acct",
                canonical_message_id="<legacy@example.test>",
                canonical_sent_archived=True,
                to=["a@example.com"],
                cc=[],
                bcc=[],
                deliveries=[],
            )
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """
                    INSERT INTO outbound_operation_deliveries_v969(
                        delivery_id,operation_id,message_id,recipient,recipient_role
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        "delivery_0123456789abcdef01234567",
                        "out_legacy",
                        "<legacy@example.test>",
                        "a@example.com",
                        "to",
                    ),
                )
            reopened = OutboundOperationStore(path)
            operation = reopened.get_operation("out_legacy")
            self.assertEqual(operation["deliveries"], [])
            self.assertEqual(
                operation["recipient_mappings"],
                [
                    {
                        "message_id": "<legacy@example.test>",
                        "recipient": "a@example.com",
                        "role": "to",
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
