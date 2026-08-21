from __future__ import annotations

import json
import os
import random
import smtplib
import socket
import sqlite3
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .mail_protocols import detect_auto_reply, parse_dsn_message


class ReliabilityError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recipient_domain(address: str) -> str:
    value = (address or "").strip().lower()
    return value.rsplit("@", 1)[-1] if "@" in value else ""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_min: float = 0.75
    jitter_max: float = 1.25

    @classmethod
    def from_env(cls) -> "RetryPolicy":
        return cls(
            max_attempts=max(1, min(int(os.getenv("SMTP_RETRY_MAX_ATTEMPTS", "3")), 10)),
            base_delay_seconds=max(0.0, float(os.getenv("SMTP_RETRY_BASE_DELAY_SECONDS", "1"))),
            max_delay_seconds=max(0.0, float(os.getenv("SMTP_RETRY_MAX_DELAY_SECONDS", "30"))),
            jitter_min=max(0.0, float(os.getenv("SMTP_RETRY_JITTER_MIN", "0.75"))),
            jitter_max=max(0.0, float(os.getenv("SMTP_RETRY_JITTER_MAX", "1.25"))),
        )

    def delay_for(self, attempt_number: int, rand: Callable[[float, float], float] = random.uniform) -> float:
        raw = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, attempt_number - 1)))
        low, high = sorted((self.jitter_min, self.jitter_max))
        return max(0.0, raw * rand(low, high))


def classify_smtp_failure(exc: BaseException, *, phase: str = "connect") -> dict[str, Any]:
    code = None
    detail: Any = str(exc)
    classification = "transport_failure"
    temporary = True
    uncertain = phase in {"data_sending", "data_waiting"}
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        code = int(getattr(exc, "smtp_code", 0) or 0)
        classification = "authentication_failure"
        temporary = False
        uncertain = False
    elif isinstance(exc, smtplib.SMTPNotSupportedError):
        classification = "unsupported_smtp_capability"
        temporary = False
        uncertain = False
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = []
        for _, response in getattr(exc, "recipients", {}).items():
            if isinstance(response, tuple) and response:
                try:
                    codes.append(int(response[0]))
                except Exception:
                    pass
        code = max(codes) if codes else None
        temporary = bool(codes) and all(400 <= value < 500 for value in codes)
        classification = "temporary_smtp_failure" if temporary else "permanent_smtp_failure"
        uncertain = False
    elif isinstance(exc, smtplib.SMTPResponseException):
        code = int(getattr(exc, "smtp_code", 0) or 0)
        detail = getattr(exc, "smtp_error", detail)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        temporary = 400 <= code < 500
        classification = "temporary_smtp_failure" if temporary else "permanent_smtp_failure"
        uncertain = False
    elif isinstance(exc, (socket.timeout, TimeoutError)):
        classification = "timeout"
        temporary = not uncertain
    elif isinstance(exc, (ConnectionError, OSError, smtplib.SMTPServerDisconnected)):
        classification = "connection_failure"
        temporary = not uncertain
    if uncertain:
        classification = "delivery_uncertain"
        temporary = False
    return {
        "classification": classification,
        "temporary": temporary,
        "permanent": not temporary and not uncertain,
        "uncertain": uncertain,
        "smtp_code": code,
        "detail": str(detail)[:2000],
        "phase": phase,
    }


class ThrottleController:
    """Thread-safe sliding-window limits for global/account/domain sends."""

    def __init__(self, *, global_per_second: int | None = None, account_per_second: int | None = None, domain_per_second: int | None = None, clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep):
        self.global_limit = max(1, int(global_per_second or os.getenv("SMTP_THROTTLE_GLOBAL_PER_SECOND", "10")))
        self.account_limit = max(1, int(account_per_second or os.getenv("SMTP_THROTTLE_ACCOUNT_PER_SECOND", "5")))
        self.domain_limit = max(1, int(domain_per_second or os.getenv("SMTP_THROTTLE_DOMAIN_PER_SECOND", "2")))
        self.clock = clock
        self.sleeper = sleeper
        self._lock = threading.RLock()
        self._global: deque[float] = deque()
        self._accounts: dict[str, deque[float]] = defaultdict(deque)
        self._domains: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _trim(queue: deque[float], now: float) -> None:
        while queue and now - queue[0] >= 1.0:
            queue.popleft()

    @staticmethod
    def _wait(queue: deque[float], limit: int, now: float) -> float:
        if len(queue) < limit:
            return 0.0
        return max(0.0, 1.0 - (now - queue[0]))

    def acquire(self, account_id: str, recipients: list[str]) -> float:
        domains = sorted({recipient_domain(value) for value in recipients if recipient_domain(value)})
        total_wait = 0.0
        while True:
            with self._lock:
                now = self.clock()
                self._trim(self._global, now)
                account_queue = self._accounts[account_id or "default"]
                self._trim(account_queue, now)
                domain_queues = [self._domains[name] for name in domains]
                for queue in domain_queues:
                    self._trim(queue, now)
                waits = [self._wait(self._global, self.global_limit, now), self._wait(account_queue, self.account_limit, now)]
                waits.extend(self._wait(queue, self.domain_limit, now) for queue in domain_queues)
                delay = max(waits or [0.0])
                if delay <= 0:
                    self._global.append(now)
                    account_queue.append(now)
                    for queue in domain_queues:
                        queue.append(now)
                    return total_wait
            self.sleeper(delay)
            total_wait += delay


class ReliabilityStore:
    """Additive delivery/retry/suppression state in the existing analytics database."""

    DELIVERY_COLUMNS = {
        "delivery_state": "TEXT NOT NULL DEFAULT 'submitted'",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "last_attempt_at": "TEXT NOT NULL DEFAULT ''",
        "next_retry_at": "TEXT NOT NULL DEFAULT ''",
        "last_error_classification": "TEXT NOT NULL DEFAULT ''",
        "bounce_classification": "TEXT NOT NULL DEFAULT ''",
        "bounce_status": "TEXT NOT NULL DEFAULT ''",
        "bounce_diagnostic": "TEXT NOT NULL DEFAULT ''",
        "conversation_state": "TEXT NOT NULL DEFAULT 'awaiting_reply'",
        "replied_at": "TEXT NOT NULL DEFAULT ''",
        "auto_reply_at": "TEXT NOT NULL DEFAULT ''",
        "correlation_confidence": "TEXT NOT NULL DEFAULT ''",
    }

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("EMAIL_ANALYTICS_DB_PATH", "/data/email_analytics.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            existing_tables = {str(row["name"]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "tracking_deliveries" in existing_tables:
                columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tracking_deliveries)").fetchall()}
                for name, declaration in self.DELIVERY_COLUMNS.items():
                    if name not in columns:
                        conn.execute(f'ALTER TABLE tracking_deliveries ADD COLUMN "{name}" {declaration}')
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS delivery_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL,
                    delivery_id TEXT NOT NULL DEFAULT '',
                    account_id TEXT NOT NULL,
                    recipient TEXT NOT NULL COLLATE NOCASE,
                    attempt_number INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    classification TEXT NOT NULL DEFAULT '',
                    smtp_code INTEGER,
                    detail TEXT NOT NULL DEFAULT '',
                    phase TEXT NOT NULL DEFAULT '',
                    message_id TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    next_retry_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS ix_delivery_attempts_operation ON delivery_attempts(operation_id, attempt_number);
                CREATE INDEX IF NOT EXISTS ix_delivery_attempts_delivery ON delivery_attempts(delivery_id, attempt_number);
                CREATE TABLE IF NOT EXISTS recipient_suppressions (
                    recipient TEXT PRIMARY KEY COLLATE NOCASE,
                    active INTEGER NOT NULL DEFAULT 1,
                    reason TEXT NOT NULL,
                    source TEXT NOT NULL,
                    soft_bounce_count INTEGER NOT NULL DEFAULT 0,
                    related_delivery_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS suppression_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipient TEXT NOT NULL COLLATE NOCASE,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    related_delivery_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_suppression_events_recipient ON suppression_events(recipient, created_at);
                CREATE TABLE IF NOT EXISTS conversation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_id TEXT NOT NULL DEFAULT '',
                    account_id TEXT NOT NULL DEFAULT '',
                    recipient TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
                    event_type TEXT NOT NULL,
                    observed TEXT NOT NULL DEFAULT '{}',
                    classification TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_conversation_events_delivery ON conversation_events(delivery_id, created_at);
            """)

    def _delivery_exists(self, conn: sqlite3.Connection, delivery_id: str) -> bool:
        if not delivery_id:
            return False
        try:
            return conn.execute("SELECT 1 FROM tracking_deliveries WHERE id=?", (delivery_id,)).fetchone() is not None
        except sqlite3.OperationalError:
            return False

    def record_attempt(self, *, operation_id: str, delivery_id: str, account_id: str, recipient: str, attempt_number: int, state: str, message_id: str, idempotency_key: str, classification: str = "", smtp_code: int | None = None, detail: str = "", phase: str = "", next_retry_at: str = "") -> int:
        now = utc_now()
        with self._lock, self._connect() as conn:
            cursor = conn.execute("""
                INSERT INTO delivery_attempts(operation_id,delivery_id,account_id,recipient,attempt_number,state,classification,smtp_code,detail,phase,message_id,idempotency_key,started_at,completed_at,next_retry_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (operation_id, delivery_id or "", account_id or "", recipient.lower(), int(attempt_number), state, classification, smtp_code, detail[:2000], phase, message_id, idempotency_key, now, now if state not in {"submitted", "retrying"} else "", next_retry_at))
            if self._delivery_exists(conn, delivery_id):
                conn.execute("""
                    UPDATE tracking_deliveries SET delivery_state=?,attempt_count=MAX(attempt_count,?),last_attempt_at=?,next_retry_at=?,last_error_classification=? WHERE id=?
                """, (state, int(attempt_number), now, next_retry_at, classification if state not in {"sent", "submitted"} else "", delivery_id))
            return int(cursor.lastrowid)

    def operation_sent(self, operation_id: str, message_id: str = "") -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM delivery_attempts WHERE operation_id=? AND state='sent' AND (?='' OR message_id=?) LIMIT 1", (operation_id, message_id, message_id)).fetchone()
        return row is not None

    def list_attempts(self, delivery_id: str | None = None, limit: int = 250) -> list[dict[str, Any]]:
        query = "SELECT * FROM delivery_attempts"
        params: list[Any] = []
        if delivery_id:
            query += " WHERE delivery_id=?"
            params.append(delivery_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def suppress(self, recipient: str, *, reason: str, source: str = "manual", related_delivery_id: str = "", soft_bounce_count: int | None = None) -> dict[str, Any]:
        address = recipient.strip().lower()
        if "@" not in address:
            raise ReliabilityError("A valid suppression recipient is required")
        allowed = {"hard_bounce", "repeated_soft_bounce", "unsubscribe", "manual"}
        if reason not in allowed:
            raise ReliabilityError(f"Unsupported suppression reason: {reason}")
        now = utc_now()
        with self._lock, self._connect() as conn:
            old = conn.execute("SELECT * FROM recipient_suppressions WHERE recipient=?", (address,)).fetchone()
            count = int(soft_bounce_count if soft_bounce_count is not None else (old["soft_bounce_count"] if old else 0))
            created = str(old["created_at"]) if old else now
            conn.execute("""
                INSERT INTO recipient_suppressions(recipient,active,reason,source,soft_bounce_count,related_delivery_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(recipient) DO UPDATE SET active=1,reason=excluded.reason,source=excluded.source,soft_bounce_count=MAX(recipient_suppressions.soft_bounce_count, excluded.soft_bounce_count),related_delivery_id=excluded.related_delivery_id,updated_at=excluded.updated_at
            """, (address, 1, reason, source, count, related_delivery_id, created, now))
            conn.execute("INSERT INTO suppression_events(recipient,action,reason,source,related_delivery_id,created_at) VALUES(?,?,?,?,?,?)", (address, "suppress", reason, source, related_delivery_id, now))
        return self.get_suppression(address) or {}

    def unsuppress(self, recipient: str, *, source: str = "manual") -> dict[str, Any]:
        address = recipient.strip().lower()
        now = utc_now()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM recipient_suppressions WHERE recipient=?", (address,)).fetchone()
            if row:
                conn.execute("UPDATE recipient_suppressions SET active=0,updated_at=? WHERE recipient=?", (now, address))
            conn.execute("INSERT INTO suppression_events(recipient,action,reason,source,created_at) VALUES(?,?,?,?,?)", (address, "unsuppress", "", source, now))
        return {"ok": True, "recipient": address, "active": False}

    def get_suppression(self, recipient: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM recipient_suppressions WHERE recipient=?", (recipient.strip().lower(),)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["active"] = bool(result["active"])
        return result

    def list_suppressions(self, *, active_only: bool = True, limit: int = 500) -> list[dict[str, Any]]:
        query = "SELECT * FROM recipient_suppressions"
        if active_only:
            query += " WHERE active=1"
        query += " ORDER BY updated_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(query, (max(1, min(int(limit), 5000)),)).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row["active"] = bool(row["active"])
        return result

    def blocked_recipients(self, recipients: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for address in recipients:
            record = self.get_suppression(address)
            if record and record.get("active"):
                out.append(record)
        return out

    def _soft_bounce_count(self, recipient: str) -> int:
        record = self.get_suppression(recipient)
        return int(record.get("soft_bounce_count", 0)) if record else 0

    def record_bounce(self, *, recipient: str, classification: str, delivery_id: str = "", status: str = "", diagnostic: str = "", confidence: str = "") -> dict[str, Any]:
        address = recipient.strip().lower()
        now = utc_now()
        with self._lock, self._connect() as conn:
            if self._delivery_exists(conn, delivery_id):
                state = "bounced" if classification not in {"delayed", "deferred", "greylisting", "soft_bounce", "mailbox_full"} else "temporarily_failed"
                conn.execute("UPDATE tracking_deliveries SET delivery_state=?,bounce_classification=?,bounce_status=?,bounce_diagnostic=?,conversation_state='bounced',correlation_confidence=? WHERE id=?", (state, classification, status, diagnostic[:2000], confidence, delivery_id))
            conn.execute("INSERT INTO conversation_events(delivery_id,recipient,event_type,observed,classification,confidence,created_at) VALUES(?,?,?,?,?,?,?)", (delivery_id, address, "dsn", json.dumps({"status": status, "diagnostic": diagnostic}, ensure_ascii=False), classification, confidence, now))
        hard = classification in {"hard_bounce", "user_unknown"}
        soft = classification in {"soft_bounce", "mailbox_full", "delayed", "deferred", "greylisting"}
        if hard:
            return {"suppression": self.suppress(address, reason="hard_bounce", source="dsn", related_delivery_id=delivery_id), "soft_bounce_count": self._soft_bounce_count(address)}
        if soft:
            threshold = max(2, int(os.getenv("SOFT_BOUNCE_SUPPRESSION_THRESHOLD", "3")))
            previous = self.get_suppression(address)
            count = int(previous.get("soft_bounce_count", 0)) + 1 if previous else 1
            current = utc_now()
            with self._lock, self._connect() as conn:
                old = conn.execute("SELECT created_at FROM recipient_suppressions WHERE recipient=?", (address,)).fetchone()
                created = str(old["created_at"]) if old else current
                conn.execute("""
                    INSERT INTO recipient_suppressions(recipient,active,reason,source,soft_bounce_count,related_delivery_id,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(recipient) DO UPDATE SET soft_bounce_count=excluded.soft_bounce_count,related_delivery_id=excluded.related_delivery_id,source=excluded.source,updated_at=excluded.updated_at
                """, (address, 0, "repeated_soft_bounce", "dsn", count, delivery_id, created, current))
            if count >= threshold:
                return {"suppression": self.suppress(address, reason="repeated_soft_bounce", source="dsn", related_delivery_id=delivery_id, soft_bounce_count=count), "soft_bounce_count": count}
            return {"suppression": None, "soft_bounce_count": count}
        return {"suppression": None, "soft_bounce_count": self._soft_bounce_count(address)}

    def correlate_delivery(self, dsn: dict[str, Any]) -> dict[str, Any]:
        correlation = dsn.get("correlation") or {}
        envelope_id = str(correlation.get("envelope_id") or "").strip()
        message_id = str(correlation.get("message_id") or "").strip()
        recipient = str((dsn.get("derived") or {}).get("recipient") or "").strip().lower()
        with self._connect() as conn:
            if envelope_id:
                row = conn.execute("SELECT id FROM tracking_deliveries WHERE id=?", (envelope_id,)).fetchone()
                if row:
                    return {"delivery_id": row["id"], "method": "original_envelope_id", "confidence": "high"}
            if message_id:
                row = conn.execute("SELECT id FROM tracking_deliveries WHERE message_id=? ORDER BY sent_at DESC LIMIT 1", (message_id,)).fetchone()
                if row:
                    return {"delivery_id": row["id"], "method": "original_message_id", "confidence": "high"}
            refs = " ".join(str(correlation.get(key) or "") for key in ("in_reply_to", "references"))
            for value in re_message_ids(refs):
                row = conn.execute("SELECT id FROM tracking_deliveries WHERE message_id=? ORDER BY sent_at DESC LIMIT 1", (value,)).fetchone()
                if row:
                    return {"delivery_id": row["id"], "method": "thread_reference", "confidence": "medium"}
            if recipient:
                row = conn.execute("SELECT id FROM tracking_deliveries WHERE recipient=? ORDER BY sent_at DESC LIMIT 1", (recipient,)).fetchone()
                if row:
                    return {"delivery_id": row["id"], "method": "recipient_recent", "confidence": "low"}
        return {"delivery_id": "", "method": "none", "confidence": "low"}

    def process_inbound(self, raw: bytes, *, account_id: str = "") -> dict[str, Any]:
        from email import policy
        from email.parser import BytesParser
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        dsn = parse_dsn_message(msg)
        if dsn["is_dsn"]:
            correlated = self.correlate_delivery(dsn)
            delivery_id = correlated["delivery_id"]
            derived = dsn.get("derived") or {}
            observed = dsn.get("observed") or {}
            bounce = self.record_bounce(recipient=str(derived.get("recipient") or ""), classification=str(derived.get("classification") or "unknown"), delivery_id=delivery_id, status=str(observed.get("status") or ""), diagnostic=str(observed.get("diagnostic_code") or ""), confidence=str(correlated.get("confidence") or dsn.get("confidence") or "low")) if derived.get("recipient") else {"suppression": None}
            return {"kind": "dsn", "dsn": dsn, "correlation": correlated, "bounce": bounce}
        auto = detect_auto_reply(msg)
        ids = re_message_ids(" ".join([str(msg.get("In-Reply-To") or ""), str(msg.get("References") or "")]))
        matched = self._find_delivery_by_message_ids(ids)
        if matched:
            state = "auto_reply" if auto["is_auto_reply"] else "replied"
            timestamp_column = "auto_reply_at" if auto["is_auto_reply"] else "replied_at"
            with self._lock, self._connect() as conn:
                conn.execute(f"UPDATE tracking_deliveries SET conversation_state=?,{timestamp_column}=? WHERE id=?", (state, utc_now(), matched["id"]))
                conn.execute("INSERT INTO conversation_events(delivery_id,account_id,recipient,event_type,observed,classification,confidence,created_at) VALUES(?,?,?,?,?,?,?,?)", (matched["id"], account_id, matched["recipient"], state, json.dumps(auto.get("observed_headers") or {}, ensure_ascii=False), state, auto.get("confidence") or "low", utc_now()))
            return {"kind": state, "delivery_id": matched["id"], "auto_reply": auto}
        return {"kind": "unmatched", "auto_reply": auto}

    def _find_delivery_by_message_ids(self, message_ids: list[str]) -> dict[str, Any] | None:
        with self._connect() as conn:
            for value in message_ids:
                row = conn.execute("SELECT id,recipient,message_id FROM tracking_deliveries WHERE message_id=? ORDER BY sent_at DESC LIMIT 1", (value,)).fetchone()
                if row:
                    return dict(row)
        return None

    def enrich_delivery(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        delivery_id = str(result.get("id") or "")
        result["attempts"] = self.list_attempts(delivery_id=delivery_id, limit=50)
        result["suppression"] = self.get_suppression(str(result.get("recipient") or ""))
        return result

    def metrics(self, *, account_id: str | None = None, campaign_id: str | None = None) -> dict[str, Any]:
        where = []
        params: list[Any] = []
        if account_id:
            where.append("account_id=?")
            params.append(account_id)
        if campaign_id:
            where.append("campaign_id=?")
            params.append(campaign_id)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self._connect() as conn:
            try:
                rows = conn.execute("SELECT * FROM tracking_deliveries" + clause, params).fetchall()
            except sqlite3.OperationalError:
                rows = []
            attempts = conn.execute("SELECT classification,state FROM delivery_attempts").fetchall()
            suppressions = conn.execute("SELECT COUNT(*) FROM recipient_suppressions WHERE active=1").fetchone()[0]
        deliveries = [dict(row) for row in rows]
        total = len(deliveries)
        hard = sum(1 for row in deliveries if row.get("bounce_classification") in {"hard_bounce", "user_unknown"})
        soft = sum(1 for row in deliveries if row.get("bounce_classification") in {"soft_bounce", "mailbox_full", "delayed", "deferred", "greylisting"})
        human_replies = [row for row in deliveries if row.get("conversation_state") == "replied"]
        auto_replies = [row for row in deliveries if row.get("conversation_state") == "auto_reply"]
        reply_seconds: list[float] = []
        for row in human_replies:
            try:
                if row.get("sent_at") and row.get("replied_at"):
                    reply_seconds.append((datetime.fromisoformat(str(row["replied_at"]).replace("Z", "+00:00")) - datetime.fromisoformat(str(row["sent_at"]).replace("Z", "+00:00"))).total_seconds())
            except Exception:
                pass
        domain_stats: dict[str, dict[str, int]] = {}
        for row in deliveries:
            domain = recipient_domain(str(row.get("recipient") or "")) or "unknown"
            item = domain_stats.setdefault(domain, {"deliveries": 0, "bounces": 0, "replies": 0})
            item["deliveries"] += 1
            if row.get("bounce_classification"):
                item["bounces"] += 1
            if row.get("conversation_state") == "replied":
                item["replies"] += 1
        temporary_attempts = sum(1 for row in attempts if row["classification"] in {"temporary_smtp_failure", "timeout", "connection_failure"})
        permanent_attempts = sum(1 for row in attempts if row["classification"] in {"permanent_smtp_failure", "authentication_failure", "unsupported_smtp_capability"})
        return {
            "observed": {"deliveries": total, "hard_bounces": hard, "soft_bounces": soft, "human_replies": len(human_replies), "auto_replies": len(auto_replies), "active_suppressions": int(suppressions), "temporary_failure_attempts": temporary_attempts, "permanent_failure_attempts": permanent_attempts},
            "rates": {"bounce_rate": round(((hard + soft) / total) * 100.0, 2) if total else 0.0, "hard_bounce_rate": round((hard / total) * 100.0, 2) if total else 0.0, "soft_bounce_rate": round((soft / total) * 100.0, 2) if total else 0.0, "human_response_rate": round((len(human_replies) / total) * 100.0, 2) if total else 0.0, "auto_reply_rate": round((len(auto_replies) / total) * 100.0, 2) if total else 0.0},
            "average_time_to_human_reply_seconds": round(sum(reply_seconds) / len(reply_seconds), 2) if reply_seconds else None,
            "by_recipient_domain": [{"domain": domain, **stats} for domain, stats in sorted(domain_stats.items(), key=lambda item: (-item[1]["bounces"], -item[1]["deliveries"], item[0]))],
            "semantics": {"observed": "Persisted delivery, bounce, reply, suppression and attempt rows.", "inferred": "Bounce/auto-reply classifications are derived from message evidence and expose confidence.", "estimated": "No proxy fetch is labeled a human open by this reliability layer."},
        }


def re_message_ids(value: str) -> list[str]:
    import re
    return re.findall(r"<[^<>@\s]+@[^<>\s]+>", value or "")
