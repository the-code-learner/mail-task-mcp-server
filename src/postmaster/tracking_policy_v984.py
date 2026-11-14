from __future__ import annotations

from contextlib import closing

import hashlib
import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

TRACKING_DISABLE_APPROVAL_TTL_SECONDS = 300
GOOGLE_PROXY_HUMAN_DELAY_SECONDS = 60


class TrackingApprovalError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(UTC)


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set)):
        return [_normalize(v) for v in value]
    return str(value)


def canonical_intent_hash(intent: Mapping[str, Any]) -> str:
    encoded = json.dumps(_normalize(dict(intent)), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def body_digest(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def attachment_intent_digest(attachments: Any) -> str:
    """Hash stable attachment descriptors; never persist attachment bytes in approval storage."""
    return canonical_intent_hash({"attachments": _normalize(attachments or [])})


def effective_tracking(track_opens: bool | None, *, account_tracking_default: bool) -> bool:
    return bool(account_tracking_default if track_opens is None else track_opens)


def generated_subject_guidance(subject: str, *, explicitly_user_supplied: bool = False) -> dict[str, Any]:
    """Soft policy only: generated subjects prefer ASCII; user text is never silently rewritten."""
    non_ascii = [ch for ch in str(subject) if ord(ch) > 127]
    return {
        "subject": subject,
        "ascii": not non_ascii,
        "preferred_ascii": True,
        "should_suggest_ascii": bool(non_ascii) and not explicitly_user_supplied,
        "preserve_user_supplied_unicode": bool(explicitly_user_supplied),
    }


class TrackingApprovalStore:
    """Persistent one-use approvals bound to an exact outbound intent.

    The preview id is a correlation handle, not reusable consent. A successful consume
    marks the record used before the outbound action is delegated. A failed send therefore
    needs a fresh chat approval, matching the requested "every time" boundary.
    """

    def __init__(self, db_path: str | Path = "/data/tracking-approvals-v990.db", *, ttl_seconds: int = TRACKING_DISABLE_APPROVAL_TTL_SECONDS):
        self.db_path = str(db_path)
        self.ttl_seconds = int(ttl_seconds)
        self._lock = threading.RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracking_disable_approvals (
                    id TEXT PRIMARY KEY,
                    intent_hash TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    intent_summary TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracking_disable_expiry ON tracking_disable_approvals(expires_at)")

    def create(self, *, operation: str, intent: Mapping[str, Any], summary: Mapping[str, Any] | None = None) -> dict[str, Any]:
        now = _utcnow()
        approval_id = "trkoff_" + secrets.token_urlsafe(18)
        digest = canonical_intent_hash(intent)
        expires = now + timedelta(seconds=self.ttl_seconds)
        safe_summary = json.dumps(_normalize(dict(summary or {})), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM tracking_disable_approvals WHERE expires_at < ?", (_iso(now),))
            conn.execute(
                "INSERT INTO tracking_disable_approvals(id,intent_hash,operation,created_at,expires_at,intent_summary) VALUES(?,?,?,?,?,?)",
                (approval_id, digest, str(operation), _iso(now), _iso(expires), safe_summary),
            )
        return {
            "ok": True,
            "approval_required": True,
            "approval_type": "tracking_disabled",
            "preview_id": approval_id,
            "intent_hash": digest,
            "expires_at": _iso(expires),
            "one_use": True,
            "send_performed": False,
            "message": "Fresh explicit chat approval is required because the effective outbound tracking state is OFF.",
            "summary": _normalize(dict(summary or {})),
        }

    def consume(self, approval_id: str, *, operation: str, intent: Mapping[str, Any]) -> dict[str, Any]:
        aid = str(approval_id or "").strip()
        if not aid:
            raise TrackingApprovalError("tracking_disable_approval_id is required after explicit chat approval")
        expected = canonical_intent_hash(intent)
        now = _utcnow()
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM tracking_disable_approvals WHERE id=?", (aid,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise TrackingApprovalError("Tracking-disable approval does not exist or has already expired")
            if row["consumed_at"]:
                conn.execute("ROLLBACK")
                raise TrackingApprovalError("Tracking-disable approval has already been consumed")
            if _parse_iso(row["expires_at"]) < now:
                conn.execute("DELETE FROM tracking_disable_approvals WHERE id=?", (aid,))
                conn.execute("COMMIT")
                raise TrackingApprovalError("Tracking-disable approval expired; ask for fresh explicit chat approval")
            if str(row["operation"]) != str(operation) or str(row["intent_hash"]) != expected:
                conn.execute("ROLLBACK")
                raise TrackingApprovalError("Tracking-disable approval does not match this exact outbound action")
            conn.execute("UPDATE tracking_disable_approvals SET consumed_at=? WHERE id=?", (_iso(now), aid))
            conn.execute("COMMIT")
        return {"ok": True, "consumed": True, "preview_id": aid, "intent_hash": expected, "consumed_at": _iso(now)}


def classify_open_event(
    *,
    sent_at: str | datetime | None,
    opened_at: str | datetime | None,
    user_agent: str = "",
    client_source: str = "",
    threshold_seconds: int = GOOGLE_PROXY_HUMAN_DELAY_SECONDS,
) -> dict[str, Any]:
    ua = str(user_agent or "").lower()
    source = str(client_source or "").lower()
    google_proxy = "googleimageproxy" in ua or "google_image_proxy" in source or "gmail_image_proxy" in source

    def parse_dt(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    sent = parse_dt(sent_at)
    opened = parse_dt(opened_at)
    delay: float | None = None
    if sent is not None and opened is not None:
        delay = max(0.0, (opened - sent).total_seconds())

    metadata_note = "Proxy-derived IP/location/browser/OS metadata is unreliable and must not be treated as end-user attribution."
    if google_proxy:
        if delay is not None and delay > float(threshold_seconds):
            return {
                "classification": "likely_human_via_proxy",
                "confidence": "medium",
                "google_image_proxy": True,
                "delay_seconds": delay,
                "threshold_seconds": int(threshold_seconds),
                "reason": f"Google Image Proxy fetch occurred more than {int(threshold_seconds)} seconds after delivery; timing is consistent with a later human-triggered open, while the fetch remains proxied.",
                "metadata_reliability": "low",
                "metadata_note": metadata_note,
            }
        return {
            "classification": "likely_machine_or_proxy",
            "confidence": "medium" if delay is not None else "low",
            "google_image_proxy": True,
            "delay_seconds": delay,
            "threshold_seconds": int(threshold_seconds),
            "reason": f"Google Image Proxy fetch occurred within {int(threshold_seconds)} seconds of delivery or delivery timing is unavailable; prefetch/automation remains plausible.",
            "metadata_reliability": "low",
            "metadata_note": metadata_note,
        }
    if "proxy" in ua or "proxy" in source or "scanner" in ua or "scanner" in source:
        return {
            "classification": "uncertain_proxy_or_scanner",
            "confidence": "low",
            "google_image_proxy": False,
            "delay_seconds": delay,
            "threshold_seconds": int(threshold_seconds),
            "reason": "Proxy/scanner indicators are present and do not establish a human read.",
            "metadata_reliability": "low",
            "metadata_note": metadata_note,
        }
    return {
        "classification": "human_or_unclassified",
        "confidence": "low",
        "google_image_proxy": False,
        "delay_seconds": delay,
        "threshold_seconds": int(threshold_seconds),
        "reason": "No known image-proxy/scanner signature was identified; an image fetch alone still does not prove a human read.",
        "metadata_reliability": "medium",
        "metadata_note": "Observed network/client metadata may still reflect relays, VPNs or security software.",
    }
