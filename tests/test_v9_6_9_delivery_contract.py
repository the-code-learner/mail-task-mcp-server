from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.link_tracking import LinkTrackingStore
from postmaster.outbound_operations_v969 import OutboundOperationStore


class DeliveryContractV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.analytics = EmailAnalyticsStore(
            str(root / "analytics.db"),
            str(root / "analytics.key"),
        )
        self.links = LinkTrackingStore(self.analytics)
        self.logical = OutboundOperationStore(root / "outbound.db")

    def test_individualized_public_delivery_ids_are_real_tracking_rows(self):
        campaign = self.analytics.create_campaign(
            account_id="acct",
            sender="sender@example.test",
            subject="Tracked",
            track_opens=True,
            amp_used=False,
        )
        real_rows = []
        for index, recipient in enumerate(
            ("a@example.test", "b@example.test"), start=1
        ):
            delivery = self.analytics.create_delivery(
                campaign_id=campaign["id"],
                account_id="acct",
                recipient=recipient,
                recipient_role="to",
            )
            message_id = f"<m{index}@example.test>"
            self.analytics.mark_sent(delivery["id"], message_id)
            real_rows.append(
                {
                    "delivery_id": delivery["id"],
                    "message_id": message_id,
                    "recipient": recipient,
                    "role": "to",
                }
            )

        self.logical.record_operation(
            operation_id="out_individualized",
            account_id="acct",
            canonical_message_id="<m1@example.test>",
            canonical_sent_archived=True,
            to=["a@example.test", "b@example.test"],
            cc=[],
            bcc=[],
            deliveries=real_rows,
        )

        public_deliveries = self.analytics.list_deliveries(
            campaign_id=campaign["id"], account_id="acct"
        )
        self.assertEqual(len(public_deliveries), 2)
        public_ids = {str(row["id"]) for row in public_deliveries}
        self.assertEqual(public_ids, {row["delivery_id"] for row in real_rows})
        for delivery_id in public_ids:
            self.assertEqual(self.analytics.get_delivery(delivery_id)["id"], delivery_id)
            self.assertEqual(
                self.analytics.list_open_events(delivery_id=delivery_id), []
            )
            summary = self.links.summary(delivery_id=delivery_id)
            self.assertEqual(summary["delivery_id"], delivery_id)
            self.assertEqual(
                self.links.unified_events(delivery_id=delivery_id), []
            )
            self.assertEqual(
                self.links.list_links(delivery_id=delivery_id), []
            )

        stored = self.logical.get_operation("out_individualized")
        self.assertEqual(
            {row["delivery_id"] for row in stored["deliveries"]}, public_ids
        )
        self.assertEqual(stored["recipient_mappings"], [])

    def test_normal_group_logical_mappings_never_enter_tracking_delivery_surface(self):
        shared = "<group@example.test>"
        self.logical.record_operation(
            operation_id="out_group",
            account_id="acct",
            canonical_message_id=shared,
            canonical_sent_archived=True,
            to=["a@example.test"],
            cc=["b@example.test"],
            bcc=["c@example.test"],
            deliveries=[],
            recipient_mappings=[
                {"message_id": shared, "recipient": "a@example.test", "role": "to"},
                {"message_id": shared, "recipient": "b@example.test", "role": "cc"},
                {"message_id": shared, "recipient": "c@example.test", "role": "bcc"},
            ],
        )
        stored = self.logical.get_operation("out_group")
        self.assertEqual(stored["deliveries"], [])
        self.assertEqual(len(stored["recipient_mappings"]), 3)
        self.assertEqual(self.analytics.list_deliveries(account_id="acct"), [])
        self.assertEqual(self.analytics.list_open_events(account_id="acct"), [])
        self.assertEqual(self.links.unified_events(account_id="acct"), [])
        self.assertEqual(self.links.list_links(account_id="acct"), [])
        self.assertNotIn("delivery_", repr(stored["recipient_mappings"]))


if __name__ == "__main__":
    unittest.main()
