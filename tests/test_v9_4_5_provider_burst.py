from __future__ import annotations

import unittest

from postmaster.provider_classification import classify_click_events, summarize_click_classification


class V945MultiLinkBurstClassificationTests(unittest.TestCase):
    @staticmethod
    def _event(
        event_id: int,
        *,
        observed_at: str,
        link_id: str,
        fingerprint: str,
        delivery_id: str,
        recipient: str = "reader@example.test",
    ) -> dict:
        return {
            "id": event_id,
            "delivery_id": delivery_id,
            "link_id": link_id,
            "recipient": recipient,
            "client_fingerprint": fingerprint,
            "country_code": "IT",
            "browser": "Chrome 151.0.0.0",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/151.0.0.0 Safari/537.36",
            "client_source": "direct_or_unknown",
            "observed_at": observed_at,
        }

    def test_sub_100ms_amd_and_qnap_style_bursts_are_provider_evidence(self) -> None:
        events = [
            self._event(1, observed_at="2026-08-21T00:00:00.000000+00:00", link_id="amd-header", fingerprint="fp-amd", delivery_id="delivery-amd"),
            self._event(2, observed_at="2026-08-21T00:00:00.007000+00:00", link_id="amd-cta", fingerprint="fp-amd", delivery_id="delivery-amd"),
            self._event(3, observed_at="2026-08-21T00:01:00.000000+00:00", link_id="qnap-header", fingerprint="fp-qnap", delivery_id="delivery-qnap"),
            self._event(4, observed_at="2026-08-21T00:01:00.035000+00:00", link_id="qnap-footer", fingerprint="fp-qnap", delivery_id="delivery-qnap"),
        ]
        classified = {row["id"]: row for row in classify_click_events(events)}
        for event_id in (1, 2, 3, 4):
            self.assertEqual(classified[event_id]["provider_classification"], "likely_email_provider")
            self.assertGreaterEqual(classified[event_id]["provider_likelihood"], 95)
            self.assertTrue(any(reason.startswith("multi-link burst:") for reason in classified[event_id]["classification_reasons"]))
            self.assertFalse(any(reason.startswith("fingerprint observed consistently across") for reason in classified[event_id]["classification_reasons"]))
        summary = summarize_click_classification(events)
        self.assertEqual(summary["unique_clicks_classified"], 4)
        self.assertEqual(summary["likely_provider_unique_clicks"], 4)
        self.assertEqual(summary["potential_provider_share"], {"numerator": 4, "denominator": 4, "percent": 100.0})

    def test_one_to_two_second_cross_link_burst_outweighs_multi_link_mitigation(self) -> None:
        events = [
            self._event(1, observed_at="2026-08-21T00:02:00.000000+00:00", link_id="burst-a", fingerprint="fp-burst", delivery_id="delivery-burst"),
            self._event(2, observed_at="2026-08-21T00:02:01.500000+00:00", link_id="burst-b", fingerprint="fp-burst", delivery_id="delivery-burst"),
        ]
        classified = classify_click_events(events)
        self.assertTrue(all(row["provider_classification"] == "likely_email_provider" for row in classified))
        self.assertTrue(all(row["provider_likelihood"] >= 70 for row in classified))

    def test_manual_libero_style_multi_link_clicks_remain_human_when_separated(self) -> None:
        events = [
            self._event(1, observed_at="2026-08-21T00:03:00+00:00", link_id="libero-a", fingerprint="fp-libero", delivery_id="delivery-libero", recipient="reader@libero.it"),
            self._event(2, observed_at="2026-08-21T00:03:08+00:00", link_id="libero-b", fingerprint="fp-libero", delivery_id="delivery-libero", recipient="reader@libero.it"),
        ]
        classified = classify_click_events(events)
        self.assertTrue(all(row["provider_classification"] == "likely_human" for row in classified))
        self.assertTrue(all(row["provider_likelihood"] <= 5 for row in classified))
        self.assertTrue(all(any(reason.startswith("fingerprint observed consistently across 2 links") for reason in row["classification_reasons"]) for row in classified))

    def test_burst_signal_is_delivery_scoped_and_does_not_change_unique_key(self) -> None:
        events = [
            self._event(1, observed_at="2026-08-21T00:04:00+00:00", link_id="a", fingerprint="same-fp", delivery_id="delivery-one"),
            self._event(2, observed_at="2026-08-21T00:04:00.010000+00:00", link_id="b", fingerprint="same-fp", delivery_id="delivery-two"),
        ]
        classified = classify_click_events(events)
        self.assertTrue(all(row["provider_classification"] == "likely_human" for row in classified))
        summary = summarize_click_classification(events)
        self.assertEqual(summary["unique_clicks_classified"], 2)
        self.assertEqual(summary["likely_provider_unique_clicks"], 0)
        self.assertEqual(summary["classification_model"], "heuristic-v1-query-time")


if __name__ == "__main__":
    unittest.main()
