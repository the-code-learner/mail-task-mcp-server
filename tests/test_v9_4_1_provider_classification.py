from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.link_tracking import LinkTrackingStore
from postmaster.provider_classification import classify_click_events, summarize_click_classification


class ProviderClassificationTests(unittest.TestCase):
    @staticmethod
    def _event(
        event_id: int,
        *,
        observed_at: str,
        link_id: str,
        fingerprint: str,
        country: str,
        browser: str,
        user_agent: str,
        source: str = "direct_or_unknown",
        delivery_id: str = "del_ground_truth",
    ) -> dict:
        return {
            "id": event_id,
            "delivery_id": delivery_id,
            "link_id": link_id,
            "client_fingerprint": fingerprint,
            "country_code": country,
            "browser": browser,
            "user_agent": user_agent,
            "client_source": source,
            "observed_at": observed_at,
        }

    def test_ground_truth_combines_signals_without_changing_unique_definition(self) -> None:
        chrome151 = "Mozilla/5.0 (Windows NT 10.0) Chrome/151.0.0.0 Safari/537.36"
        chrome149 = "Mozilla/5.0 (Windows NT 10.0) Chrome/149.0.0.0 Safari/537.36"
        android151 = "Mozilla/5.0 (Linux; Android 15) Chrome/151.0.0.0 Mobile Safari/537.36"
        events = [
            # Gmail A: the user click we know was made manually.
            self._event(1, observed_at="2026-08-20T10:00:00+00:00", link_id="gmail-a", fingerprint="fp-human", country="IT", browser="Chrome 151.0.0.0", user_agent=chrome151),
            # Gmail A: additional request not made by the user, 3.637 seconds later.
            self._event(2, observed_at="2026-08-20T10:00:03.637000+00:00", link_id="gmail-a", fingerprint="fp-provider", country="US", browser="Chrome 149.0.0.0", user_agent=chrome149),
            # Same human fingerprint later on another link in the same delivery.
            self._event(3, observed_at="2026-08-20T10:00:30+00:00", link_id="gmail-b", fingerprint="fp-human", country="IT", browser="Chrome 151.0.0.0", user_agent=chrome151),
            # Libero-style pair: same fingerprint across two explicit human clicks.
            self._event(4, observed_at="2026-08-20T10:01:00+00:00", link_id="libero-a", fingerprint="fp-libero", country="IT", browser="Chrome 151.0.0.0", user_agent=android151),
            self._event(5, observed_at="2026-08-20T10:01:08+00:00", link_id="libero-b", fingerprint="fp-libero", country="IT", browser="Chrome 151.0.0.0", user_agent=android151),
            # Explicit known provider signature is deterministic and does not need timing heuristics.
            self._event(6, observed_at="2026-08-20T10:02:00+00:00", link_id="proxy", fingerprint="fp-google-proxy", country="US", browser="Google Image Proxy", user_agent="Mozilla/5.0 (via ggpht.com GoogleImageProxy)", source="gmail_image_proxy"),
        ]

        classified = {row["id"]: row for row in classify_click_events(events)}

        self.assertLessEqual(classified[1]["provider_likelihood"], 10)
        self.assertEqual(classified[1]["provider_classification"], "likely_human")
        self.assertGreaterEqual(classified[2]["provider_likelihood"], 85)
        self.assertLessEqual(classified[2]["provider_likelihood"], 100)
        self.assertEqual(classified[2]["provider_classification"], "likely_email_provider")
        self.assertIn("second request on same delivery/link after 3.64s", classified[2]["classification_reasons"])
        self.assertIn("fingerprint changed", classified[2]["classification_reasons"])
        self.assertIn("country changed IT → US", classified[2]["classification_reasons"])
        self.assertIn("browser changed Chrome 151.0.0.0 → Chrome 149.0.0.0", classified[2]["classification_reasons"])
        self.assertLessEqual(classified[4]["provider_likelihood"], 5)
        self.assertLessEqual(classified[5]["provider_likelihood"], 5)
        self.assertEqual(classified[6]["provider_likelihood"], 100)
        self.assertEqual(classified[6]["provider_classification"], "known_email_proxy")
        self.assertEqual(classified[6]["provider_guess"], "google")

        summary = summarize_click_classification(events)
        self.assertEqual(summary["unique_clicks_classified"], 6)
        self.assertEqual(summary["likely_provider_unique_clicks"], 2)
        self.assertEqual(summary["known_email_proxy_unique_clicks"], 1)
        self.assertEqual(summary["likely_human_or_unclassified_unique_clicks"], 4)
        self.assertEqual(summary["potential_provider_share"], {"numerator": 2, "denominator": 6, "percent": 33.3})
        self.assertEqual(summary["provider_suspects"], {"google": 1, "other": 1})

    def test_one_weak_signal_does_not_become_provider_proof(self) -> None:
        events = [
            self._event(1, observed_at="2026-08-20T11:00:00+00:00", link_id="weak", fingerprint="fp-a", country="IT", browser="Chrome 151", user_agent="Chrome/151"),
            self._event(2, observed_at="2026-08-20T11:00:10+00:00", link_id="weak", fingerprint="fp-b", country="IT", browser="Chrome 151", user_agent="Chrome/151"),
        ]
        classified = {row["id"]: row for row in classify_click_events(events)}
        self.assertEqual(classified[2]["provider_likelihood"], 40)
        self.assertEqual(classified[2]["provider_classification"], "uncertain")
        self.assertIsNone(classified[2]["provider_guess"])


class ProviderClassificationStoreIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_public = {k: os.environ.get(k) for k in ("PUBLIC_EMAIL_BASE_URL", "PUBLIC_MCP_HOST")}
        os.environ["PUBLIC_EMAIL_BASE_URL"] = "https://postmaster.example.test"
        os.environ["PUBLIC_MCP_HOST"] = ""
        self.analytics = EmailAnalyticsStore(db_path=str(root / "analytics.db"), key_path=str(root / "analytics.key"))
        self.links = LinkTrackingStore(self.analytics)
        self.campaign = self.analytics.create_campaign(account_id="acct", sender="sender@example.test", subject="v9.4.1", track_opens=True, amp_used=False)
        self.delivery = self.analytics.create_delivery(campaign_id=self.campaign["id"], account_id="acct", recipient="reader@example.test", recipient_role="to")

    def tearDown(self) -> None:
        for key, value in self.old_public.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_store_queries_classify_at_read_time_without_schema_columns(self) -> None:
        _, meta = self.links.instrument_html(body_html='<a href="https://example.com/a">Project</a>', delivery=self.delivery)
        occurrence_id = meta[0]["occurrence_id"]
        with self.links._connect() as conn:
            token = str(conn.execute("SELECT tracking_token FROM tracking_links WHERE id=?", (occurrence_id,)).fetchone()[0])
        link = self.links.get_by_token(token)

        first = self.links.record_click(
            link,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/151.0.0.0 Safari/537.36",
            client_ip="203.0.113.10",
            country_code="IT",
        )
        second = self.links.record_click(
            link,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/149.0.0.0 Safari/537.36",
            client_ip="203.0.113.11",
            country_code="US",
        )
        with self.links._connect() as conn:
            conn.execute("UPDATE tracking_clicks SET observed_at=? WHERE id=?", ("2026-08-20T12:00:00+00:00", first["id"]))
            conn.execute("UPDATE tracking_clicks SET observed_at=? WHERE id=?", ("2026-08-20T12:00:03.637000+00:00", second["id"]))
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tracking_clicks)").fetchall()}

        self.assertNotIn("provider_likelihood", columns)
        self.assertNotIn("provider_classification", columns)

        events = self.links.list_click_events(delivery_id=self.delivery["id"])
        by_id = {row["id"]: row for row in events}
        self.assertEqual(by_id[first["id"]]["provider_classification"], "likely_human")
        self.assertGreaterEqual(by_id[second["id"]]["provider_likelihood"], 85)
        self.assertEqual(by_id[second["id"]]["provider_classification"], "likely_email_provider")

        summary = self.links.summary(delivery_id=self.delivery["id"])
        self.assertEqual(summary["unique_clicks"], 2)
        self.assertEqual(summary["unique_click_definition"], "delivery_id + link_id + client_fingerprint")
        self.assertEqual(summary["likely_provider_unique_clicks"], 1)
        self.assertEqual(summary["likely_human_or_unclassified_unique_clicks"], 1)
        self.assertEqual(summary["potential_provider_share"], {"numerator": 1, "denominator": 2, "percent": 50.0})
        self.assertEqual(summary["provider_classification_model"], "heuristic-v1-query-time")


if __name__ == "__main__":
    unittest.main()
