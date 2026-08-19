from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from .email_analytics import (
    AnalyticsError, EmailAnalyticsStore, _client_metadata, _now, _safe_base_url, _token, analytics_store,
)
from .link_tracking_queries import LinkTrackingQueriesMixin
from .link_tracking_html import (
    already_tracked_url, collect_anchors, eligible_web_url, normalized_url, replace_href, rewrite_anchor_tags,
)

class LinkTrackingStore(LinkTrackingQueriesMixin):
    """Additive per-link analytics layered on the existing email analytics database."""

    def __init__(self, analytics: EmailAnalyticsStore | None = None):
        self.analytics = analytics or analytics_store()
        self._init_schema()

    def _connect(self):
        return self.analytics._connect()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracking_links (
                    id TEXT PRIMARY KEY,
                    link_id TEXT NOT NULL,
                    tracking_token TEXT NOT NULL UNIQUE,
                    campaign_id TEXT NOT NULL,
                    delivery_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    recipient TEXT NOT NULL COLLATE NOCASE,
                    original_url TEXT NOT NULL,
                    normalized_url TEXT NOT NULL,
                    destination_host TEXT NOT NULL DEFAULT '',
                    position INTEGER NOT NULL,
                    anchor_text TEXT NOT NULL DEFAULT '',
                    message_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(delivery_id) REFERENCES tracking_deliveries(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS ix_tracking_links_campaign
                    ON tracking_links(campaign_id, link_id);
                CREATE INDEX IF NOT EXISTS ix_tracking_links_delivery
                    ON tracking_links(delivery_id, position);
                CREATE INDEX IF NOT EXISTS ix_tracking_links_logical
                    ON tracking_links(link_id, campaign_id);

                CREATE TABLE IF NOT EXISTS tracking_clicks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link_occurrence_id TEXT NOT NULL,
                    link_id TEXT NOT NULL,
                    delivery_id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    recipient TEXT NOT NULL COLLATE NOCASE,
                    observed_at TEXT NOT NULL,
                    event_type TEXT NOT NULL DEFAULT 'link',
                    user_agent TEXT NOT NULL DEFAULT '',
                    client_fingerprint TEXT NOT NULL DEFAULT '',
                    country_code TEXT NOT NULL DEFAULT '',
                    browser TEXT NOT NULL DEFAULT '',
                    os TEXT NOT NULL DEFAULT '',
                    client_source TEXT NOT NULL DEFAULT '',
                    metadata_confidence TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(link_occurrence_id) REFERENCES tracking_links(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS ix_tracking_clicks_link
                    ON tracking_clicks(link_id, observed_at);
                CREATE INDEX IF NOT EXISTS ix_tracking_clicks_delivery
                    ON tracking_clicks(delivery_id, observed_at);
                CREATE INDEX IF NOT EXISTS ix_tracking_clicks_campaign
                    ON tracking_clicks(campaign_id, observed_at);
                CREATE INDEX IF NOT EXISTS ix_tracking_clicks_recipient
                    ON tracking_clicks(recipient COLLATE NOCASE, observed_at);
                """
            )
            cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tracking_links)").fetchall()}
            if "message_id" not in cols:
                conn.execute(
                    "ALTER TABLE tracking_links ADD COLUMN message_id TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def _logical_link_id(campaign_id: str, position: int, normalized_url: str, anchor_text: str) -> str:
        digest = hashlib.sha256(
            f"{campaign_id}\0{position}\0{normalized_url}\0{anchor_text}".encode("utf-8", "ignore")
        ).hexdigest()[:20]
        return f"lnk_{digest}"

    def _insert_link(self, *, delivery: dict[str, Any], original_url: str, position: int, anchor_text: str) -> dict[str, Any]:
        normalized = normalized_url(original_url)
        parts = urlsplit(original_url)
        logical_id = self._logical_link_id(str(delivery["campaign_id"]), position, normalized, anchor_text)
        occurrence_id = f"lno_{_token(12)}"
        token = _token(24)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tracking_links(
                    id,link_id,tracking_token,campaign_id,delivery_id,account_id,recipient,
                    original_url,normalized_url,destination_host,position,anchor_text,message_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    occurrence_id, logical_id, token, delivery["campaign_id"], delivery["id"],
                    delivery["account_id"], delivery["recipient"], original_url, normalized,
                    (parts.hostname or "").lower(), int(position), anchor_text,
                    str(delivery.get("message_id") or ""), now,
                ),
            )
        return {
            "occurrence_id": occurrence_id, "link_id": logical_id, "tracking_token": token,
            "campaign_id": str(delivery["campaign_id"]), "delivery_id": str(delivery["id"]),
            "recipient": str(delivery["recipient"]), "original_url": original_url,
            "normalized_url": normalized, "destination_host": (parts.hostname or "").lower(),
            "position": int(position), "anchor_text": anchor_text,
            "message_id": str(delivery.get("message_id") or ""), "created_at": now,
        }

    def mark_delivery_message(self, delivery_id: str, message_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE tracking_links SET message_id=? WHERE delivery_id=?", (message_id or "", delivery_id))

    def instrument_html(self, *, body_html: str, delivery: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        html = body_html or ""
        anchors = collect_anchors(html)
        if not anchors:
            return html, []
        replacements: list[tuple[str, str]] = []
        public_base = _safe_base_url()
        tracked: list[dict[str, Any]] = []
        for anchor_index, anchor in enumerate(anchors):
            href = str(anchor.get("href") or "")
            if not eligible_web_url(href) or already_tracked_url(href, public_base):
                continue
            record = self._insert_link(
                delivery=delivery, original_url=href, position=anchor_index,
                anchor_text=str(anchor.get("anchor_text") or "")[:500],
            )
            tracked_url = f"{public_base}/t/c/{record['tracking_token']}"
            replacements.append((str(anchor["raw_tag"]), replace_href(str(anchor["raw_tag"]), tracked_url)))
            safe = dict(record)
            safe.pop("tracking_token", None)
            safe["tracked_url_path"] = "/t/c/<token>"
            tracked.append(safe)
        return rewrite_anchor_tags(html, replacements), tracked

    def get_by_token(self, token: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tracking_links WHERE tracking_token=?", (token,)).fetchone()
        if not row:
            raise AnalyticsError("Unknown link tracking token")
        return dict(row)

    def _fingerprint(self, client_ip: str, user_agent: str) -> str:
        return hmac.new(
            self.analytics._fingerprint_key,
            f"{client_ip}|{user_agent}".encode("utf-8", "ignore"),
            hashlib.sha256,
        ).hexdigest()[:24]

    def record_click(self, link: dict[str, Any], *, user_agent: str = "", client_ip: str = "", country_code: str = "") -> dict[str, Any]:
        now = _now()
        ua = (user_agent or "")[:500]
        metadata = _client_metadata("link", ua, country_code)
        fingerprint = self._fingerprint(client_ip or "", ua)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tracking_clicks(
                    link_occurrence_id,link_id,delivery_id,campaign_id,account_id,recipient,
                    observed_at,event_type,user_agent,client_fingerprint,country_code,browser,os,
                    client_source,metadata_confidence
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    link["id"], link["link_id"], link["delivery_id"], link["campaign_id"],
                    link["account_id"], link["recipient"], now, "link", ua, fingerprint,
                    metadata["country_code"], metadata["browser"], metadata["os"],
                    metadata["client_source"], metadata["metadata_confidence"],
                ),
            )
            event_id = int(cur.lastrowid)
        return {
            "ok": True, "id": event_id, "event_type": "link", "link_id": str(link["link_id"]),
            "delivery_id": str(link["delivery_id"]), "campaign_id": str(link["campaign_id"]),
            "recipient": str(link["recipient"]), "observed_at": now,
            "client_fingerprint": fingerprint, "country_code": metadata["country_code"],
            "browser": metadata["browser"], "os": metadata["os"],
            "client_source": metadata["client_source"],
            "metadata_confidence": metadata["metadata_confidence"],
        }

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            links = conn.execute("SELECT COUNT(*) FROM tracking_links").fetchone()[0]
            clicks = conn.execute("SELECT COUNT(*) FROM tracking_clicks").fetchone()[0]
        return {
            "link_tracking": True, "link_occurrences": int(links), "click_events": int(clicks),
            "public_path": "/t/c/*", "unique_click_definition": "delivery_id + link_id + client_fingerprint",
            "tokens_exposed_in_listing": False,
        }


@lru_cache(maxsize=1)
def link_store() -> LinkTrackingStore:
    return LinkTrackingStore(analytics_store())
