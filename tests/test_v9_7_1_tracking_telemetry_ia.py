from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from postmaster.email_analytics import EmailAnalyticsStore
from postmaster.link_tracking import LinkTrackingStore
from postmaster.tracking_telemetry_v971 import (
    SENSITIVE_RETENTION_DAYS,
    _clean_ip,
    account_tracking_overlay,
    enforce_sensitive_retention,
    install_tracking_telemetry_v971,
    sensitive_events_for_account,
    sent_tracking_block,
)


def _stores(tmp_path):
    analytics = EmailAnalyticsStore(
        db_path=str(tmp_path / "analytics.db"),
        key_path=str(tmp_path / "analytics.key"),
    )
    links = LinkTrackingStore(analytics)
    base = SimpleNamespace(analytics_store=lambda: analytics)
    core = SimpleNamespace(link_store=lambda: links)
    install_tracking_telemetry_v971(base, core)
    return analytics, links


def _delivery(analytics, *, account_id="acct-1", recipient="reader@example.com"):
    campaign = analytics.create_campaign(
        account_id=account_id,
        sender="sender@example.com",
        subject="Telemetry subject",
        track_opens=True,
        amp_used=False,
    )
    delivery = analytics.create_delivery(
        campaign_id=campaign["id"],
        account_id=account_id,
        recipient=recipient,
        recipient_role="to",
    )
    analytics.mark_sent(delivery["id"], "<message-1@example.com>")
    return analytics.get_delivery(delivery["id"])


def test_source_ip_and_network_geography_are_persisted_privately(tmp_path):
    analytics, _links = _stores(tmp_path)
    delivery = _delivery(analytics)

    analytics.record_open(
        delivery["tracking_token"],
        user_agent="Mozilla/5.0",
        client_ip="203.0.113.44",
        country_code="IT",
    )

    public = analytics.list_open_events(account_id="acct-1")
    assert len(public) == 1
    assert "source_ip" not in public[0]
    assert "geo_label" not in public[0]

    private = sensitive_events_for_account(analytics, account_id="acct-1")
    assert private[0]["source_ip"] == "203.0.113.44"
    assert private[0]["geo_country_code"] == "IT"
    assert private[0]["geo_label"] == "Country IT"
    assert private[0]["geo_confidence"] == "low"
    assert private[0]["estimated_classification"] == "human_or_unclassified"
    assert private[0]["classification_confidence"] == "low"
    assert private[0]["message_id"] == "<message-1@example.com>"
    assert private[0]["subject"] == "Telemetry subject"


def test_click_telemetry_reuses_probabilistic_provider_classification(tmp_path):
    analytics, links = _stores(tmp_path)
    delivery = _delivery(analytics)
    inserted = links._insert_link(
        delivery=delivery,
        original_url="https://example.com/path?q=1",
        position=0,
        anchor_text="Read",
    )
    stored = links.get_by_token(inserted["tracking_token"])

    links.record_click(
        stored,
        user_agent="GoogleImageProxy",
        client_ip="2001:db8::1",
        country_code="US",
    )

    public = links.list_click_events(account_id="acct-1")
    assert len(public) == 1
    assert "source_ip" not in public[0]

    private = sensitive_events_for_account(analytics, account_id="acct-1")
    click = next(row for row in private if row["event_kind"] == "click")
    assert click["source_ip"] == "2001:db8::1"
    assert click["original_url"] == "https://example.com/path?q=1"
    assert click["estimated_classification"] == "known_email_proxy"
    assert click["provider_likelihood"] == 100
    assert click["classification_confidence"] == "high"


def test_invalid_ip_is_not_persisted_as_sensitive_data(tmp_path):
    analytics, _links = _stores(tmp_path)
    delivery = _delivery(analytics)
    analytics.record_open(
        delivery["tracking_token"],
        user_agent="Mozilla/5.0",
        client_ip="203.0.113.5\\nforged",
        country_code="DE",
    )
    event = sensitive_events_for_account(analytics, account_id="acct-1")[0]
    assert event["source_ip"] == ""
    assert _clean_ip("not-an-ip") == ""


def test_sensitive_retention_deletes_sidecar_but_keeps_base_event(tmp_path):
    analytics, _links = _stores(tmp_path)
    delivery = _delivery(analytics)
    analytics.record_open(
        delivery["tracking_token"],
        user_agent="Mozilla/5.0",
        client_ip="198.51.100.8",
        country_code="FR",
    )
    old = (datetime.now(timezone.utc) - timedelta(days=SENSITIVE_RETENTION_DAYS + 2)).isoformat()
    with analytics._connect() as conn:
        conn.execute(
            "UPDATE tracking_sensitive_telemetry SET observed_at=?",
            (old,),
        )

    assert enforce_sensitive_retention(analytics) == 1
    assert len(analytics.list_open_events(account_id="acct-1")) == 1
    event = sensitive_events_for_account(analytics, account_id="acct-1")[0]
    assert event["source_ip"] in (None, "")
    assert event["captured_at"] in (None, "")


def test_account_visibility_requires_explicit_account_scope(tmp_path):
    analytics, _links = _stores(tmp_path)
    delivery_a = _delivery(analytics, account_id="acct-a", recipient="a@example.com")
    delivery_b = _delivery(analytics, account_id="acct-b", recipient="b@example.com")
    analytics.record_open(delivery_a["tracking_token"], client_ip="203.0.113.10", country_code="IT")
    analytics.record_open(delivery_b["tracking_token"], client_ip="203.0.113.11", country_code="US")

    assert sensitive_events_for_account(analytics, account_id="") == []
    rows_a = sensitive_events_for_account(analytics, account_id="acct-a")
    assert {row["account_id"] for row in rows_a} == {"acct-a"}
    assert {row["source_ip"] for row in rows_a} == {"203.0.113.10"}


def test_sent_message_tracking_uses_message_id_not_subject_guessing(tmp_path):
    analytics, _links = _stores(tmp_path)
    delivery = _delivery(analytics)
    analytics.record_open(
        delivery["tracking_token"],
        user_agent="Mozilla/5.0",
        client_ip="192.0.2.7",
        country_code="GB",
    )

    matched = sensitive_events_for_account(
        analytics,
        account_id="acct-1",
        message_id="<message-1@example.com>",
    )
    missing = sensitive_events_for_account(
        analytics,
        account_id="acct-1",
        message_id="<different@example.com>",
    )
    assert len(matched) == 1
    assert missing == []

    block = sent_tracking_block(
        analytics,
        account_id="acct-1",
        message_id="<message-1@example.com>",
    )
    assert "Tracking" in block
    assert "Persisted source IP" in block
    assert "192.0.2.7" in block
    assert "Human-vs-machine" in block


def test_account_overlay_contains_required_expanded_fields_and_policy(tmp_path):
    analytics, _links = _stores(tmp_path)
    delivery = _delivery(analytics)
    analytics.record_open(
        delivery["tracking_token"],
        user_agent="Mozilla/5.0",
        client_ip="203.0.113.22",
        country_code="IT",
    )
    html = account_tracking_overlay(analytics, account_id="acct-1")
    assert 'id="v971-tracking-overlay"' in html
    assert "Recent tracking activity" in html
    assert "Telemetry subject" in html
    assert "reader@example.com" in html
    assert "Persisted source IP" in html
    assert "Geolocation estimate" in html
    assert "Fingerprint" in html
    assert "Estimated classification" in html
    assert "30 days" in html
    assert "legacy MCP tracking responses" in html
    assert "never proof" in html


def test_installer_is_idempotent(tmp_path):
    analytics, links = _stores(tmp_path)
    base = SimpleNamespace(analytics_store=lambda: analytics)
    core = SimpleNamespace(link_store=lambda: links)
    first_open = analytics.record_open
    first_click = links.record_click
    install_tracking_telemetry_v971(base, core)
    assert analytics.record_open is first_open
    assert links.record_click is first_click
