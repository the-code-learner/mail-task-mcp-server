from __future__ import annotations

import unittest
from pathlib import Path

from starlette.requests import Request

from postmaster import webgui_v951 as v951
from postmaster import webgui_v954 as v954
from postmaster.tracked_mail import _sent_clean_html


ACCOUNTS = [
    {
        "id": "alpha",
        "label": "Primary",
        "email_address": "primary@example.invalid",
        "enabled": True,
        "is_default": True,
        "sent_mailbox": "INBOX.Sent",
    },
    {
        "id": "beta",
        "label": "Secondary",
        "email_address": "secondary@example.invalid",
        "enabled": True,
        "is_default": False,
        "sent_mailbox": "Custom Outbox",
    },
]


def request_for(query: str = "") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": query.encode("utf-8"),
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("localhost", 8000),
    })


class FakeBase:
    def __init__(self):
        self.delivery_calls = []
        self.rows = [
            {"uid": "1", "message_id": "<untracked@example.invalid>", "from": "primary@example.invalid", "subject": "Untracked", "date": "2026-08-21T12:00:00+00:00"},
            {"uid": "2", "message_id": "<idle@example.invalid>", "from": "primary@example.invalid", "subject": "Idle", "date": "2026-08-21T12:01:00+00:00"},
            {"uid": "3", "message_id": "<open@example.invalid>", "from": "primary@example.invalid", "subject": "Opened", "date": "2026-08-21T12:02:00+00:00"},
            {"uid": "4", "message_id": "<click@example.invalid>", "from": "primary@example.invalid", "subject": "Clicked", "date": "2026-08-21T12:03:00+00:00"},
            {"uid": "5", "message_id": "<multi-a@example.invalid>", "from": "primary@example.invalid", "subject": "Multi", "date": "2026-08-21T12:04:00+00:00"},
        ]
        self.deliveries = [
            {"id": "d-idle", "campaign_id": "cmp-idle", "account_id": "alpha", "recipient": "idle@example.invalid", "recipient_role": "to", "message_id": "<idle@example.invalid>", "open_count": 0, "first_open_at": "", "last_open_at": "", "delivery_state": "delivered", "conversation_state": "sent"},
            {"id": "d-open", "campaign_id": "cmp-open", "account_id": "alpha", "recipient": "open@example.invalid", "recipient_role": "to", "message_id": "<open@example.invalid>", "open_count": 1, "first_open_at": "2026-08-21T12:20:00+00:00", "last_open_at": "2026-08-21T12:20:00+00:00", "delivery_state": "delivered", "conversation_state": "sent"},
            {"id": "d-click", "campaign_id": "cmp-click", "account_id": "alpha", "recipient": "click@example.invalid", "recipient_role": "to", "message_id": "<click@example.invalid>", "open_count": 1, "first_open_at": "2026-08-21T12:21:00+00:00", "last_open_at": "2026-08-21T12:22:00+00:00", "delivery_state": "delivered", "conversation_state": "sent"},
            {"id": "d-m1", "campaign_id": "cmp-multi", "account_id": "alpha", "recipient": "one@example.invalid", "recipient_role": "to", "message_id": "<multi-a@example.invalid>", "open_count": 1, "first_open_at": "2026-08-21T12:30:00+00:00", "last_open_at": "2026-08-21T12:31:00+00:00", "delivery_state": "delivered", "conversation_state": "sent"},
            {"id": "d-m2", "campaign_id": "cmp-multi", "account_id": "alpha", "recipient": "two@example.invalid", "recipient_role": "cc", "message_id": "<multi-b@example.invalid>", "open_count": 1, "first_open_at": "2026-08-21T12:32:00+00:00", "last_open_at": "2026-08-21T12:33:00+00:00", "delivery_state": "delivered", "conversation_state": "sent"},
            {"id": "d-m3", "campaign_id": "cmp-multi", "account_id": "alpha", "recipient": "three@example.invalid", "recipient_role": "bcc", "message_id": "<multi-c@example.invalid>", "open_count": 0, "first_open_at": "", "last_open_at": "", "delivery_state": "delivered", "conversation_state": "sent"},
            {"id": "d-beta-cross", "campaign_id": "cmp-beta", "account_id": "beta", "recipient": "cross@example.invalid", "recipient_role": "to", "message_id": "<untracked@example.invalid>", "open_count": 9, "first_open_at": "2026-08-21T10:00:00+00:00", "last_open_at": "2026-08-21T11:00:00+00:00", "delivery_state": "delivered", "conversation_state": "sent"},
        ]

    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def list_email_accounts(self):
        return {"ok": True, "accounts": [dict(row) for row in ACCOUNTS]}

    def list_mailboxes(self, *, account_id=None):
        return ["INBOX", "INBOX.Sent", "Sent", "Archive", "Custom Outbox"]

    def search_emails(self, **kwargs):
        return [dict(row) for row in self.rows]

    def get_email(self, **kwargs):
        uid = str(kwargs["uid"])
        row = next(row for row in self.rows if row["uid"] == uid)
        return {**row, "body_text": f"Body for {row['subject']}"}

    def list_tracking_deliveries(self, campaign_id=None, recipient=None, account_id=None, limit=250):
        self.delivery_calls.append({
            "campaign_id": campaign_id,
            "recipient": recipient,
            "account_id": account_id,
            "limit": limit,
        })
        rows = list(self.deliveries)
        if campaign_id:
            rows = [row for row in rows if row["campaign_id"] == campaign_id]
        if recipient:
            rows = [row for row in rows if row["recipient"].casefold() == recipient.casefold()]
        if account_id:
            rows = [row for row in rows if row["account_id"] == account_id]
        return rows[:limit]


class FakeCore:
    def __init__(self):
        self.links = [
            {"link_id": "pricing", "campaign_id": "cmp-click", "delivery_id": "d-click", "account_id": "alpha", "recipient": "click@example.invalid", "message_id": "<click@example.invalid>", "original_url": "https://example.invalid/pricing?a=1#plans", "normalized_url": "https://example.invalid/pricing?a=1#plans", "destination_host": "example.invalid", "anchor_text": "Pricing plans", "total_clicks": 3, "unique_clicks": 2, "first_click": "2026-08-21T12:23:00+00:00", "last_click": "2026-08-21T12:25:00+00:00"},
            {"link_id": "multi", "campaign_id": "cmp-multi", "delivery_id": "d-m2", "account_id": "alpha", "recipient": "two@example.invalid", "message_id": "<multi-b@example.invalid>", "original_url": "https://example.invalid/multi", "normalized_url": "https://example.invalid/multi", "destination_host": "example.invalid", "anchor_text": "Multi link", "total_clicks": 1, "unique_clicks": 1, "first_click": "2026-08-21T12:34:00+00:00", "last_click": "2026-08-21T12:34:00+00:00"},
            {"link_id": "beta", "campaign_id": "cmp-beta", "delivery_id": "d-beta-cross", "account_id": "beta", "recipient": "cross@example.invalid", "message_id": "<untracked@example.invalid>", "original_url": "https://example.invalid/beta", "destination_host": "example.invalid", "anchor_text": "Cross account", "total_clicks": 7, "unique_clicks": 4, "first_click": "2026-08-21T10:01:00+00:00", "last_click": "2026-08-21T10:30:00+00:00"},
        ]

    def list_tracking_links(self, campaign_id=None, delivery_id=None, link_id=None, account_id=None, clicked_only=False, limit=500):
        rows = list(self.links)
        if campaign_id:
            rows = [row for row in rows if row["campaign_id"] == campaign_id]
        if delivery_id:
            rows = [row for row in rows if row["delivery_id"] == delivery_id]
        if account_id:
            rows = [row for row in rows if row["account_id"] == account_id]
        if clicked_only:
            rows = [row for row in rows if row["total_clicks"] > 0]
        return rows[:limit]

    def get_tracking_summary(self, campaign_id=None, delivery_id=None, link_id=None, account_id=None):
        if delivery_id == "d-click":
            return {
                "total_clicks": 3,
                "unique_clicks": 2,
                "unique_click_definition": "delivery_id + link_id + client_fingerprint",
                "qualitative_estimate": {
                    "likely_provider_unique_clicks": 1,
                    "uncertain_unique_clicks": 1,
                },
            }
        if delivery_id == "d-m2":
            return {"total_clicks": 1, "unique_clicks": 1, "unique_click_definition": "delivery_id + link_id + client_fingerprint"}
        return {"total_clicks": 0, "unique_clicks": 0, "unique_click_definition": "delivery_id + link_id + client_fingerprint"}


class V954SentTrackingUxTests(unittest.TestCase):
    def setUp(self):
        self.base = FakeBase()
        self.core = FakeCore()
        v954._CORE = self.core

    def sent_html(self, extra: str = "") -> str:
        suffix = "&" + extra if extra else ""
        return v954.render_inbox(
            self.base,
            request_for("ui_view=inbox&account_id=alpha&mailbox=INBOX.Sent" + suffix),
        )

    def row_after(self, html: str, subject: str) -> str:
        return html.split(subject, 1)[1].split("</tr>", 1)[0]

    def test_sent_without_tracking_is_non_tracked_and_does_not_cross_accounts(self):
        row = self.row_after(self.sent_html(), "Untracked")
        self.assertIn("Non tracciata", row)
        self.assertNotIn("Link cliccato", row)

    def test_tracked_without_events_is_no_activity(self):
        self.assertIn("Nessuna attività", self.row_after(self.sent_html(), "Idle"))

    def test_open_uses_observed_wording(self):
        html = self.sent_html()
        self.assertIn("Apertura rilevata", self.row_after(html, "Opened"))
        self.assertNotIn("Mail letta", html)
        self.assertNotIn(">Letta<", html)

    def test_click_is_dominant_single_recipient_state(self):
        self.assertIn("Link cliccato", self.row_after(self.sent_html(), "Clicked"))

    def test_multi_recipient_aggregation_is_correct(self):
        self.assertIn("Aperti 2/3 · Click 1/3", self.row_after(self.sent_html(), "Multi"))

    def test_sent_view_shows_tracking_detail_and_clicked_link_fields(self):
        html = self.sent_html("message_uid=4")
        for expected in (
            "<h3>Tracking</h3>", "click@example.invalid", "Delivery state", "Conversation state",
            "Open rilevati", "Prima apertura", "Ultima apertura", "Click totali", "Click unici",
            "Pricing plans", "https://example.invalid/pricing?a=1#plans",
            "provider/proxy", "I conteggi raw non vengono riscritti",
        ):
            self.assertIn(expected, html)
        self.assertIn(">3</td>", html)
        self.assertIn(">2</td>", html)

    def test_multi_recipient_view_keeps_delivery_sections_separate(self):
        html = self.sent_html("message_uid=5")
        for recipient in ("one@example.invalid", "two@example.invalid", "three@example.invalid"):
            self.assertIn(recipient, html)
        self.assertGreaterEqual(html.count('class="card v954-delivery"'), 3)
        self.assertIn("Multi link", html)

    def test_non_sent_mailbox_has_no_outbound_tracking_panel_or_store_read(self):
        self.base.delivery_calls.clear()
        html = v954.render_inbox(
            self.base,
            request_for("ui_view=inbox&account_id=alpha&mailbox=INBOX&message_uid=4"),
        )
        self.assertNotIn("<th>Tracking</th>", html)
        self.assertNotIn('class="v954-tracking"', html)
        self.assertEqual(self.base.delivery_calls, [])

    def test_sent_aliases_and_configured_custom_mailbox_are_recognized(self):
        conventional = v954.render_inbox(
            self.base,
            request_for("ui_view=inbox&account_id=alpha&mailbox=Sent"),
        )
        configured = v954.render_inbox(
            self.base,
            request_for("ui_view=inbox&account_id=beta&mailbox=Custom%20Outbox"),
        )
        self.assertIn("<th>Tracking</th>", conventional)
        self.assertIn("<th>Tracking</th>", configured)

    def test_read_model_join_is_strict_account_id_plus_message_id(self):
        model = v954._build_tracking_read_model(
            self.base,
            self.core,
            account_id="alpha",
            message_ids=["<untracked@example.invalid>", "<click@example.invalid>"],
        )
        self.assertNotIn("<untracked@example.invalid>", model)
        self.assertEqual(
            {row["account_id"] for row in model["<click@example.invalid>"]["deliveries"]},
            {"alpha"},
        )


class V954InvariantTests(unittest.TestCase):
    def test_sender_clean_copy_has_original_link_and_no_active_tracking_callbacks(self):
        delivery = {"recipient": "reader@example.invalid", "campaign_id": "cmp", "id": "dlv"}
        source = '<a href="https://example.invalid/original">Original</a><img src="{{TRACKING_PIXEL_URL}}"><a href="{{AMP_STATUS_URL}}">status</a>'
        clean = _sent_clean_html(source, delivery)
        self.assertIn('href="https://example.invalid/original"', clean)
        self.assertNotIn("/track/open/", clean)
        self.assertNotIn("/t/c/", clean)
        self.assertNotIn("{{TRACKING_PIXEL_URL}}", clean)
        self.assertNotIn("{{AMP_STATUS_URL}}", clean)

    def test_v954_is_read_only_webgui_and_defines_no_mcp_mutation(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "postmaster" / "webgui_v954.py").read_text(encoding="utf-8")
        for forbidden in ("@mcp.tool", "add_tool(", "remove_tool(", "record_open(", "record_click(", "instrument_html("):
            self.assertNotIn(forbidden, source)
        self.assertIn("base.list_tracking_deliveries", source)
        self.assertIn("core.list_tracking_links", source)

    def test_runtime_installs_v954_after_v953(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "postmaster" / "runtime.py").read_text(encoding="utf-8")
        self.assertIn("from .webgui_v954 import install_webgui_v954", source)
        self.assertIn("dashboard_home = install_webgui_v954(app, _base, _core, dashboard_home)", source)
        self.assertLess(source.index("install_webgui_v953(app"), source.index("install_webgui_v954(app"))

    def test_unique_click_definition_is_unchanged(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "postmaster" / "link_tracking_queries.py").read_text(encoding="utf-8")
        self.assertIn("delivery_id + link_id + client_fingerprint", source)
        self.assertIn("c.delivery_id || '|' || c.link_id || '|' || c.client_fingerprint", source)

    def test_existing_mcp_coverage_stays_at_90_names(self):
        names = {name for group in v951.MCP_COVERAGE.values() for name in group}
        self.assertEqual(len(names), 90)
        self.assertNotIn("sent_tracking", names)
        self.assertNotIn("get_sent_tracking", names)


if __name__ == "__main__":
    unittest.main()
