from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from postmaster.tracking_policy_v990 import (
    TrackingApprovalError,
    TrackingApprovalStore,
    classify_open_event,
    effective_tracking,
    generated_subject_guidance,
)


class TrackingPolicyV990Tests(unittest.TestCase):
    def test_effective_tracking_respects_account_default(self):
        self.assertFalse(effective_tracking(None, account_tracking_default=False))
        self.assertTrue(effective_tracking(None, account_tracking_default=True))
        self.assertFalse(effective_tracking(False, account_tracking_default=True))
        self.assertTrue(effective_tracking(True, account_tracking_default=False))

    def test_approval_is_exact_and_one_use(self):
        with TemporaryDirectory() as td:
            store = TrackingApprovalStore(Path(td) / "approvals.db")
            intent = {"operation": "send_email", "to": ["a@example.com"], "subject": "Hello", "body_sha256": "abc"}
            preview = store.create(operation="send_email", intent=intent, summary={"to": ["a@example.com"]})
            consumed = store.consume(preview["preview_id"], operation="send_email", intent=intent)
            self.assertTrue(consumed["consumed"])
            with self.assertRaises(TrackingApprovalError):
                store.consume(preview["preview_id"], operation="send_email", intent=intent)

    def test_approval_rejects_mutated_intent(self):
        with TemporaryDirectory() as td:
            store = TrackingApprovalStore(Path(td) / "approvals.db")
            preview = store.create(operation="send_email", intent={"to": ["a@example.com"]})
            with self.assertRaises(TrackingApprovalError):
                store.consume(preview["preview_id"], operation="send_email", intent={"to": ["b@example.com"]})

    def test_google_proxy_after_one_minute_is_likely_human_but_metadata_low(self):
        sent = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
        result = classify_open_event(
            sent_at=sent,
            opened_at=sent + timedelta(seconds=61),
            user_agent="Mozilla/5.0 GoogleImageProxy",
            client_source="gmail_image_proxy",
        )
        self.assertEqual(result["classification"], "likely_human_via_proxy")
        self.assertEqual(result["metadata_reliability"], "low")
        self.assertEqual(result["threshold_seconds"], 60)

    def test_google_proxy_at_one_minute_is_not_promoted(self):
        sent = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
        result = classify_open_event(sent_at=sent, opened_at=sent + timedelta(seconds=60), user_agent="GoogleImageProxy")
        self.assertEqual(result["classification"], "likely_machine_or_proxy")

    def test_ascii_is_preference_not_rewrite(self):
        result = generated_subject_guidance("Ciao — prova", explicitly_user_supplied=True)
        self.assertEqual(result["subject"], "Ciao — prova")
        self.assertFalse(result["should_suggest_ascii"])
        self.assertTrue(result["preserve_user_supplied_unicode"])


if __name__ == "__main__":
    unittest.main()
