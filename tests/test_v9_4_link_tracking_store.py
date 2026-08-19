from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.link_tracking import LinkTrackingStore
from postmaster.tracked_mail import _sent_clean_html

class LinkStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_public = {k: os.environ.get(k) for k in ("PUBLIC_EMAIL_BASE_URL", "PUBLIC_MCP_HOST")}
        os.environ["PUBLIC_EMAIL_BASE_URL"] = "https://postmaster.example.test"
        os.environ["PUBLIC_MCP_HOST"] = ""
        self.analytics = EmailAnalyticsStore(db_path=str(root / "analytics.db"), key_path=str(root / "analytics.key"))
        self.links = LinkTrackingStore(self.analytics)
        self.campaign = self.analytics.create_campaign(account_id="acct", sender="sender@example.test", subject="v9.4", track_opens=True, amp_used=False)
        self.delivery = self.analytics.create_delivery(campaign_id=self.campaign["id"], account_id="acct", recipient="reader@example.test", recipient_role="to")

    def tearDown(self) -> None:
        for key, value in self.old_public.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        self.tmp.cleanup()

    def _token_for_occurrence(self, occurrence_id: str) -> str:
        with self.links._connect() as conn:
            return str(conn.execute("SELECT tracking_token FROM tracking_links WHERE id=?", (occurrence_id,)).fetchone()[0])

    def test_html_rewrite_rules_query_fragment_positions_and_no_double_wrap(self) -> None:
        original = (
            '<p><a href="http://one.example/a">HTTP One</a>'
            '<a href="https://two.example/page?a=1&amp;b=2#section">HTTPS Two</a>'
            '<a href="mailto:hello@example.test">Mail</a><a href="tel:+123">Tel</a>'
            '<a href="cid:image1">Cid</a><a href="#local">Anchor</a>'
            '<a href="data:text/plain,hello">Data</a><a href="javascript:void(0)">JS</a>'
            '<a href="https://postmaster.example.test/t/c/already">Already tracked</a>'
            '<a href="https://third.example/t/c/legitimate">Third-party /t/c</a>'
            '<a href="https://two.example/page?a=1&amp;b=2#section">HTTPS Two footer</a></p>'
        )
        rewritten, meta = self.links.instrument_html(body_html=original, delivery=self.delivery)
        self.assertEqual(len(meta), 4)
        self.assertEqual([row["position"] for row in meta], [0, 1, 9, 10])
        self.assertEqual(len({row["occurrence_id"] for row in meta}), 4)
        self.assertEqual(len({row["link_id"] for row in meta}), 4)
        for untouched in ('href="mailto:hello@example.test"','href="tel:+123"','href="cid:image1"','href="#local"','href="data:text/plain,hello"','href="javascript:void(0)"','href="https://postmaster.example.test/t/c/already"'):
            self.assertIn(untouched, rewritten)
        self.assertNotIn('href="http://one.example/a"', rewritten)
        self.assertEqual(rewritten.count("https://postmaster.example.test/t/c/"), 5)
        self.assertIn("HTTP One", rewritten)
        self.assertIn("HTTPS Two footer", rewritten)
        rows = self.links.list_links(delivery_id=self.delivery["id"], limit=20)
        urls = [row["original_url"] for row in rows]
        self.assertIn("https://two.example/page?a=1&b=2#section", urls)
        self.assertIn("https://third.example/t/c/legitimate", urls)
        repeated = [row for row in rows if row["normalized_url"] == "https://two.example/page?a=1&b=2#section"]
        self.assertEqual(len(repeated), 2)
        self.assertNotEqual(repeated[0]["position"], repeated[1]["position"])
        self.assertNotEqual(repeated[0]["link_id"], repeated[1]["link_id"])
        self.assertTrue(all("tracking_token" not in row for row in rows))

    def test_click_persistence_unique_definition_filters_and_enrichment(self) -> None:
        rewritten, meta = self.links.instrument_html(body_html='<a href="https://example.com/a?x=1#frag">Project</a>', delivery=self.delivery)
        self.assertIn("/t/c/", rewritten)
        link = self.links.get_by_token(self._token_for_occurrence(meta[0]["occurrence_id"]))
        first = self.links.record_click(link, user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/140.0.0.0 Safari/537.36", client_ip="203.0.113.10", country_code="IT")
        second = self.links.record_click(link, user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/140.0.0.0 Safari/537.36", client_ip="203.0.113.10", country_code="IT")
        third = self.links.record_click(link, user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/18.0 Safari/605.1.15", client_ip="203.0.113.11", country_code="US")
        self.assertEqual(first["event_type"], "link")
        self.assertEqual(first["client_fingerprint"], second["client_fingerprint"])
        self.assertNotEqual(first["client_fingerprint"], third["client_fingerprint"])
        self.assertEqual(first["country_code"], "IT")
        self.assertIn("Chrome", first["browser"])
        self.assertIn("Windows", first["os"])
        self.assertEqual(first["client_source"], "direct_or_unknown")
        for kwargs in ({"campaign_id": self.campaign["id"]},{"delivery_id": self.delivery["id"]},{"link_id": meta[0]["link_id"]}):
            summary = self.links.summary(**kwargs)
            self.assertEqual(summary["total_clicks"], 3)
            self.assertEqual(summary["unique_clicks"], 2)
            self.assertEqual(summary["unique_recipients"], 1)
            self.assertTrue(summary["first_click"])
            self.assertTrue(summary["last_click"])
            self.assertEqual(summary["unique_click_definition"], "delivery_id + link_id + client_fingerprint")
        events = self.links.list_click_events(link_id=meta[0]["link_id"])
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["original_url"], "https://example.com/a?x=1#frag")
        self.assertTrue(events[0]["user_agent"])
        top = self.links.top_links(campaign_id=self.campaign["id"])
        self.assertEqual((top[0]["total_clicks"], top[0]["unique_clicks"], top[0]["unique_recipients"]), (3,2,1))

    def test_existing_pixel_pipeline_remains_unchanged(self) -> None:
        rendered, _ = self.analytics.render_for_recipient(body_html="<html><body>Hello</body></html>", body_amp=None, delivery=self.delivery, track_opens=True)
        self.assertIn(f"/track/open/{self.delivery['tracking_token']}.gif", rendered)
        result = self.analytics.record_open(self.delivery["tracking_token"], user_agent="Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36", client_ip="203.0.113.20", country_code="IT")
        self.assertEqual(result["event_type"], "pixel")
        self.assertEqual(self.analytics.list_open_events(delivery_id=self.delivery["id"])[0]["event_type"], "pixel")

    def test_sent_clean_placeholder_rendering_has_no_recipient_callbacks(self) -> None:
        source = '<a href="https://example.com/page?a=1&amp;b=2#section">Visible label</a><img src="{{TRACKING_PIXEL_URL}}"><a href="{{AMP_STATUS_URL}}">status</a>{{RECIPIENT}} {{CAMPAIGN_ID}} {{DELIVERY_ID}}'
        clean = _sent_clean_html(source, self.delivery)
        self.assertNotIn("/track/open/", clean)
        self.assertNotIn("/t/c/", clean)
        self.assertNotIn("{{TRACKING_PIXEL_URL}}", clean)
        self.assertNotIn("{{AMP_STATUS_URL}}", clean)
        self.assertIn("https://example.com/page?a=1&amp;b=2#section", clean)
        self.assertIn("Visible label", clean)
