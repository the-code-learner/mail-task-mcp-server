from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any


class AnalyticsError(RuntimeError):
    pass


TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
    b"\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def _safe_base_url() -> str:
    raw = (os.getenv("PUBLIC_EMAIL_BASE_URL") or "").strip().rstrip("/")
    if raw:
        return raw
    host = (os.getenv("PUBLIC_MCP_HOST") or "").strip()
    if host:
        return f"https://{host}"
    raise AnalyticsError(
        "PUBLIC_EMAIL_BASE_URL (or PUBLIC_MCP_HOST) is required for tracking/AMP public URLs"
    )


def _replace_ci_before_close(html: str, fragment: str, close_tag: str = "</body>") -> str:
    match = re.search(re.escape(close_tag), html, flags=re.I)
    if not match:
        return html + fragment
    return html[: match.start()] + fragment + html[match.start() :]


def _clean_country_code(value: str) -> str:
    code = (value or "").strip().upper()
    # Cloudflare uses ISO alpha-2 for normal countries; special values such as XX/T1
    # are retained because they are still useful provenance signals.
    if re.fullmatch(r"[A-Z0-9]{2}", code):
        return code
    return ""


def _parse_browser(user_agent: str) -> str:
    ua = user_agent or ""
    low = ua.lower()
    if "googleimageproxy" in low:
        return "Google Image Proxy"
    if "outlook" in low or "microsoft office" in low:
        return "Microsoft Outlook"
    if "thunderbird/" in low:
        m = re.search(r"Thunderbird/([0-9.]+)", ua, flags=re.I)
        return "Thunderbird" + (f" {m.group(1)}" if m else "")
    for pattern, label in (
        (r"EdgA?/([0-9.]+)", "Edge"),
        (r"EdgiOS/([0-9.]+)", "Edge iOS"),
        (r"OPR/([0-9.]+)", "Opera"),
        (r"FxiOS/([0-9.]+)", "Firefox iOS"),
        (r"Firefox/([0-9.]+)", "Firefox"),
        (r"CriOS/([0-9.]+)", "Chrome iOS"),
        (r"Chrome/([0-9.]+)", "Chrome"),
    ):
        m = re.search(pattern, ua, flags=re.I)
        if m:
            return f"{label} {m.group(1)}"
    if "safari/" in low and "version/" in low:
        m = re.search(r"Version/([0-9.]+)", ua, flags=re.I)
        return "Safari" + (f" {m.group(1)}" if m else "")
    if ua:
        return "Other / unknown"
    return "Unknown"


def _parse_os(user_agent: str) -> str:
    ua = user_agent or ""
    low = ua.lower()
    win = re.search(r"Windows NT ([0-9.]+)", ua, flags=re.I)
    if win:
        mapping = {
            "10.0": "Windows 10/11",
            "6.3": "Windows 8.1",
            "6.2": "Windows 8",
            "6.1": "Windows 7",
        }
        return mapping.get(win.group(1), f"Windows NT {win.group(1)}")
    android = re.search(r"Android\s+([0-9.]+)", ua, flags=re.I)
    if android:
        return f"Android {android.group(1)}"
    ios = re.search(r"(?:iPhone OS|CPU (?:iPhone )?OS)\s+([0-9_]+)", ua, flags=re.I)
    if ios:
        return "iOS " + ios.group(1).replace("_", ".")
    mac = re.search(r"Mac OS X\s+([0-9_\.]+)", ua, flags=re.I)
    if mac:
        return "macOS " + mac.group(1).replace("_", ".")
    cros = re.search(r"CrOS\s+[^ ]+\s+([0-9.]+)", ua, flags=re.I)
    if cros:
        return f"ChromeOS {cros.group(1)}"
    if "linux" in low:
        return "Linux"
    if ua:
        return "Other / unknown"
    return "Unknown"


def _client_metadata(event_type: str, user_agent: str, country_code: str) -> dict[str, str]:
    ua = user_agent or ""
    low = ua.lower()
    country = _clean_country_code(country_code)
    if "googleimageproxy" in low:
        source = "gmail_image_proxy"
        confidence = "low"
    elif event_type == "amp_xhr":
        # AMP Email XHR is commonly proxied by the mailbox provider; do not claim
        # the observed network/browser metadata is necessarily the end user.
        source = "amp_mail_proxy_or_client"
        confidence = "low"
    else:
        source = "direct_or_unknown"
        confidence = "medium"
    return {
        "country_code": country,
        "browser": _parse_browser(ua),
        "os": _parse_os(ua),
        "client_source": source,
        "metadata_confidence": confidence,
    }


def validate_amp_document(body_amp: str) -> dict[str, Any]:
    """Fast structural checks. This is not a replacement for the official AMP validator."""
    text = body_amp or ""
    issues: list[str] = []
    if len(text.encode("utf-8")) > 200_000:
        issues.append("AMP HTML exceeds the 200,000-byte AMP for Email limit")
    if not re.match(r"^\s*<!doctype\s+html", text, flags=re.I):
        issues.append("AMP document must start with <!doctype html>")
    if not re.search(r"<html\b[^>]*(?:⚡4email|amp4email)", text, flags=re.I):
        issues.append("Top-level <html> must contain ⚡4email or amp4email")
    if not re.search(r"<head\b", text, flags=re.I) or not re.search(r"<body\b", text, flags=re.I):
        issues.append("AMP document must contain <head> and <body>")
    if not re.search(r'<meta\s+charset=["\']?utf-8["\']?', text, flags=re.I):
        issues.append("AMP document must contain <meta charset=\"utf-8\">")
    if "https://cdn.ampproject.org/v0.js" not in text:
        issues.append("AMP runtime script https://cdn.ampproject.org/v0.js is required")
    if not re.search(r"<style\b[^>]*amp4email-boilerplate", text, flags=re.I):
        issues.append("AMP boilerplate <style amp4email-boilerplate> is required")
    return {
        "ok": not issues,
        "issues": issues,
        "bytes": len(text.encode("utf-8")),
        "note": "Structural preflight only; validate the delivered message with Gmail/AMP tooling before registration.",
    }


class EmailAnalyticsStore:
    """Per-recipient delivery/open tracking and AMP limited-use access tokens."""

    def __init__(self, db_path: str | None = None, key_path: str | None = None):
        self.db_path = db_path or os.getenv("EMAIL_ANALYTICS_DB_PATH", "/data/email_analytics.db")
        self.key_path = key_path or os.getenv("EMAIL_ANALYTICS_KEY_PATH", "/data/email_analytics.key")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.key_path).parent.mkdir(parents=True, exist_ok=True)
        self._fingerprint_key = self._load_key()
        self._init_db()

    def _load_key(self) -> bytes:
        path = Path(self.key_path)
        if path.exists():
            raw = path.read_bytes().strip()
            if len(raw) < 32:
                raise AnalyticsError("Invalid analytics key")
            return raw
        raw = secrets.token_bytes(32)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(raw)
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return raw

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracking_campaigns (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    track_opens INTEGER NOT NULL DEFAULT 0,
                    amp_used INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS tracking_deliveries (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    recipient TEXT NOT NULL COLLATE NOCASE,
                    recipient_role TEXT NOT NULL DEFAULT 'to',
                    tracking_token TEXT NOT NULL UNIQUE,
                    amp_token TEXT NOT NULL UNIQUE,
                    amp_expires_at TEXT NOT NULL,
                    message_id TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL DEFAULT '',
                    first_open_at TEXT NOT NULL DEFAULT '',
                    last_open_at TEXT NOT NULL DEFAULT '',
                    open_count INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(campaign_id) REFERENCES tracking_campaigns(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS ix_tracking_deliveries_campaign
                    ON tracking_deliveries(campaign_id);
                CREATE INDEX IF NOT EXISTS ix_tracking_deliveries_recipient
                    ON tracking_deliveries(recipient COLLATE NOCASE);

                CREATE TABLE IF NOT EXISTS tracking_opens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    recipient TEXT NOT NULL COLLATE NOCASE,
                    opened_at TEXT NOT NULL,
                    event_type TEXT NOT NULL DEFAULT 'pixel',
                    user_agent TEXT NOT NULL DEFAULT '',
                    client_fingerprint TEXT NOT NULL DEFAULT '',
                    country_code TEXT NOT NULL DEFAULT '',
                    browser TEXT NOT NULL DEFAULT '',
                    os TEXT NOT NULL DEFAULT '',
                    client_source TEXT NOT NULL DEFAULT '',
                    metadata_confidence TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(delivery_id) REFERENCES tracking_deliveries(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS ix_tracking_opens_delivery
                    ON tracking_opens(delivery_id, opened_at);
                CREATE INDEX IF NOT EXISTS ix_tracking_opens_campaign
                    ON tracking_opens(campaign_id, opened_at);
                CREATE INDEX IF NOT EXISTS ix_tracking_opens_recipient
                    ON tracking_opens(recipient COLLATE NOCASE, opened_at);
                """
            )
            cols = {
                str(r["name"])
                for r in conn.execute("PRAGMA table_info(tracking_opens)").fetchall()
            }
            if "event_type" not in cols:
                conn.execute(
                    "ALTER TABLE tracking_opens ADD COLUMN event_type TEXT NOT NULL DEFAULT 'pixel'"
                )
            for column in (
                "country_code", "browser", "os", "client_source", "metadata_confidence"
            ):
                if column not in cols:
                    conn.execute(
                        f"ALTER TABLE tracking_opens ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )

    def validate_public_base_url(self) -> str:
        """
        Validate the public tracking/AMP base URL before creating campaign rows.

        This prevents orphan campaign/delivery records when tracking or AMP is requested
        but PUBLIC_EMAIL_BASE_URL / PUBLIC_MCP_HOST is not configured.
        """
        return _safe_base_url()

    def create_campaign(
        self,
        *,
        account_id: str,
        sender: str,
        subject: str,
        track_opens: bool,
        amp_used: bool,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        cid = (campaign_id or "").strip() or f"cmp_{_token(12)}"
        now = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tracking_campaigns WHERE id=?", (cid,)).fetchone()
            if row:
                if str(row["account_id"]) != account_id:
                    raise AnalyticsError("campaign_id already belongs to another account")
                return dict(row)
            conn.execute(
                """
                INSERT INTO tracking_campaigns(
                    id,account_id,sender,subject,created_at,track_opens,amp_used
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (cid, account_id, sender, subject, now, 1 if track_opens else 0, 1 if amp_used else 0),
            )
        return self.get_campaign(cid)

    def create_delivery(
        self,
        *,
        campaign_id: str,
        account_id: str,
        recipient: str,
        recipient_role: str,
    ) -> dict[str, Any]:
        did = f"dlv_{_token(12)}"
        track_token = _token(24)
        amp_token = _token(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tracking_deliveries(
                    id,campaign_id,account_id,recipient,recipient_role,
                    tracking_token,amp_token,amp_expires_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    did, campaign_id, account_id, recipient.strip().lower(),
                    recipient_role, track_token, amp_token, expires,
                ),
            )
        return self.get_delivery(did)

    def mark_sent(self, delivery_id: str, message_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tracking_deliveries SET message_id=?, sent_at=? WHERE id=?",
                (message_id or "", _now(), delivery_id),
            )
        return self.get_delivery(delivery_id)

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT c.*,
                       COUNT(d.id) AS recipient_count,
                       SUM(CASE WHEN d.open_count>0 THEN 1 ELSE 0 END) AS opened_recipient_count,
                       COALESCE(SUM(d.open_count),0) AS total_open_events
                FROM tracking_campaigns c
                LEFT JOIN tracking_deliveries d ON d.campaign_id=c.id
                WHERE c.id=?
                GROUP BY c.id
                """,
                (campaign_id,),
            ).fetchone()
        if not row:
            raise AnalyticsError("Tracking campaign not found")
        out = dict(row)
        out["track_opens"] = bool(out["track_opens"])
        out["amp_used"] = bool(out["amp_used"])
        return out

    def get_delivery(self, delivery_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracking_deliveries WHERE id=?",
                (delivery_id,),
            ).fetchone()
        if not row:
            raise AnalyticsError("Tracking delivery not found")
        return dict(row)

    def get_delivery_by_tracking_token(self, token: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracking_deliveries WHERE tracking_token=?",
                (token,),
            ).fetchone()
        if not row:
            raise AnalyticsError("Unknown tracking token")
        return dict(row)

    def get_delivery_by_amp_token(self, token: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracking_deliveries WHERE amp_token=?",
                (token,),
            ).fetchone()
        if not row:
            raise AnalyticsError("Unknown AMP token")
        if datetime.now(timezone.utc) > _parse_dt(str(row["amp_expires_at"])):
            raise AnalyticsError("AMP access token expired")
        return dict(row)

    def tracking_pixel_url(self, delivery: dict[str, Any]) -> str:
        return f"{_safe_base_url()}/track/open/{delivery['tracking_token']}.gif"

    def amp_status_url(self, delivery: dict[str, Any]) -> str:
        return f"{_safe_base_url()}/api/amp/status?token={delivery['amp_token']}"

    def render_for_recipient(
        self,
        *,
        body_html: str,
        body_amp: str | None,
        delivery: dict[str, Any],
        track_opens: bool,
    ) -> tuple[str, str | None]:
        html = body_html or ""
        amp = body_amp

        replacements = {
            "{{RECIPIENT}}": str(delivery["recipient"]),
            "{{CAMPAIGN_ID}}": str(delivery["campaign_id"]),
            "{{DELIVERY_ID}}": str(delivery["id"]),
            "{{AMP_STATUS_URL}}": self.amp_status_url(delivery),
            "{{TRACKING_PIXEL_URL}}": self.tracking_pixel_url(delivery),
        }
        for key, value in replacements.items():
            html = html.replace(key, escape(value, quote=True))
            if amp is not None:
                amp = amp.replace(key, escape(value, quote=True))

        if track_opens:
            pixel = self.tracking_pixel_url(delivery)
            html_fragment = (
                f'<img src="{escape(pixel, quote=True)}" width="1" height="1" '
                'alt="" style="display:block;width:1px;height:1px;opacity:0;border:0" />'
            )
            html = _replace_ci_before_close(html, html_fragment)

            if amp is not None:
                amp_fragment = (
                    f'<amp-img src="{escape(pixel, quote=True)}" width="1" height="1" '
                    'alt="" style="opacity:0"></amp-img>'
                )
                amp = _replace_ci_before_close(amp, amp_fragment)

        return html, amp

    def _record_delivery_event(
        self,
        delivery: dict[str, Any],
        *,
        event_type: str,
        user_agent: str = "",
        client_ip: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        now = _now()
        ua = (user_agent or "")[:500]
        metadata = _client_metadata(event_type, ua, country_code)
        fingerprint = hmac.new(
            self._fingerprint_key,
            f"{client_ip}|{ua}".encode("utf-8", "ignore"),
            hashlib.sha256,
        ).hexdigest()[:24]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tracking_opens(
                    delivery_id,campaign_id,account_id,recipient,opened_at,
                    event_type,user_agent,client_fingerprint,
                    country_code,browser,os,client_source,metadata_confidence
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    delivery["id"], delivery["campaign_id"], delivery["account_id"],
                    delivery["recipient"], now, event_type, ua, fingerprint,
                    metadata["country_code"], metadata["browser"], metadata["os"],
                    metadata["client_source"], metadata["metadata_confidence"],
                ),
            )
            conn.execute(
                """
                UPDATE tracking_deliveries
                SET open_count=open_count+1,
                    first_open_at=CASE WHEN first_open_at='' THEN ? ELSE first_open_at END,
                    last_open_at=?
                WHERE id=?
                """,
                (now, now, delivery["id"]),
            )
        updated = self.get_delivery(str(delivery["id"]))
        return {
            "ok": True,
            "delivery_id": updated["id"],
            "campaign_id": updated["campaign_id"],
            "recipient": updated["recipient"],
            "event_type": event_type,
            "open_count": updated["open_count"],
            "opened_at": now,
            "country_code": metadata["country_code"],
            "browser": metadata["browser"],
            "os": metadata["os"],
            "client_source": metadata["client_source"],
            "metadata_confidence": metadata["metadata_confidence"],
        }

    def record_open(
        self,
        token: str,
        *,
        user_agent: str = "",
        client_ip: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        delivery = self.get_delivery_by_tracking_token(token)
        return self._record_delivery_event(
            delivery,
            event_type="pixel",
            user_agent=user_agent,
            client_ip=client_ip,
            country_code=country_code,
        )

    def record_amp_view(
        self,
        amp_token: str,
        *,
        user_agent: str = "",
        client_ip: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        delivery = self.get_delivery_by_amp_token(amp_token)
        return self._record_delivery_event(
            delivery,
            event_type="amp_xhr",
            user_agent=user_agent,
            client_ip=client_ip,
            country_code=country_code,
        )

    def list_campaigns(self, *, account_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        where = ""
        params: list[Any] = []
        if account_id:
            where = "WHERE c.account_id=?"
            params.append(account_id)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.*,
                       COUNT(d.id) AS recipient_count,
                       SUM(CASE WHEN d.open_count>0 THEN 1 ELSE 0 END) AS opened_recipient_count,
                       COALESCE(SUM(d.open_count),0) AS total_open_events
                FROM tracking_campaigns c
                LEFT JOIN tracking_deliveries d ON d.campaign_id=c.id
                {where}
                GROUP BY c.id
                ORDER BY c.created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["track_opens"] = bool(item["track_opens"])
            item["amp_used"] = bool(item["amp_used"])
            out.append(item)
        return out

    def list_deliveries(
        self,
        *,
        campaign_id: str | None = None,
        recipient: str | None = None,
        account_id: str | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if campaign_id:
            clauses.append("campaign_id=?")
            params.append(campaign_id)
        if recipient:
            clauses.append("recipient=? COLLATE NOCASE")
            params.append(recipient.strip())
        if account_id:
            clauses.append("account_id=?")
            params.append(account_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 1000))
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tracking_deliveries{where} ORDER BY sent_at DESC, rowid DESC LIMIT ?",
                params,
            ).fetchall()
        # Never expose bearer tokens through dashboard/MCP listing.
        items = []
        for row in rows:
            item = dict(row)
            item.pop("tracking_token", None)
            item.pop("amp_token", None)
            items.append(item)
        return items

    def list_open_events(
        self,
        *,
        delivery_id: str | None = None,
        campaign_id: str | None = None,
        recipient: str | None = None,
        account_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        for column, value in (
            ("delivery_id", delivery_id),
            ("campaign_id", campaign_id),
            ("account_id", account_id),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if recipient:
            clauses.append("recipient=? COLLATE NOCASE")
            params.append(recipient.strip())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 2000))
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tracking_opens{where} ORDER BY opened_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            campaigns = conn.execute("SELECT COUNT(*) FROM tracking_campaigns").fetchone()[0]
            deliveries = conn.execute("SELECT COUNT(*) FROM tracking_deliveries").fetchone()[0]
            opens = conn.execute("SELECT COUNT(*) FROM tracking_opens").fetchone()[0]
            opened = conn.execute(
                "SELECT COUNT(*) FROM tracking_deliveries WHERE open_count>0"
            ).fetchone()[0]
        return {
            "ok": True,
            "db_path": self.db_path,
            "public_base_url": _safe_base_url(),
            "campaigns": campaigns,
            "deliveries": deliveries,
            "opened_deliveries": opened,
            "open_events": opens,
            "privacy_note": (
                "Open events are remote-image/AMP fetches, not guaranteed human reads. "
                "Country/browser/OS are observed or inferred metadata; mail proxies, scanners "
                "and prefetching can hide or replace the end-user details."
            ),
        }


@lru_cache(maxsize=1)
def analytics_store() -> EmailAnalyticsStore:
    return EmailAnalyticsStore()
