from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from functools import lru_cache
from pathlib import Path
from typing import Any


_OUTBOUND_DB_ENV = "POSTMASTER_OUTBOUND_OPERATION_DB_PATH"
_DEFAULT_OUTBOUND_DB = Path("/data/outbound_operations_v969.db")
_MESSAGE_ID_RE = re.compile(r"<[^<>\s]+>")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fallback_path() -> Path:
    configured = str(os.getenv("XDG_STATE_HOME") or "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    return root / "postmaster" / _DEFAULT_OUTBOUND_DB.name


def resolve_outbound_operation_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    configured = str(os.getenv(_OUTBOUND_DB_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    try:
        if _DEFAULT_OUTBOUND_DB.parent.is_dir() and os.access(
            _DEFAULT_OUTBOUND_DB.parent, os.W_OK | os.X_OK
        ):
            return _DEFAULT_OUTBOUND_DB
    except OSError:
        pass
    return _fallback_path()


class OutboundOperationStore:
    """Private sender-side logical-send metadata.

    Delivery MIME never reads from this store. The canonical Sent message and every
    per-recipient delivery Message-ID map to the same logical operation, while original
    Bcc is kept only in this server-side database.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = resolve_outbound_operation_path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS outbound_operations_v969 (
                    operation_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    canonical_message_id TEXT NOT NULL DEFAULT '',
                    canonical_sent_archived INTEGER NOT NULL DEFAULT 0,
                    to_json TEXT NOT NULL DEFAULT '[]',
                    cc_json TEXT NOT NULL DEFAULT '[]',
                    bcc_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_outbound_operations_v969_canonical
                    ON outbound_operations_v969(account_id,canonical_message_id);
                CREATE TABLE IF NOT EXISTS outbound_operation_deliveries_v969 (
                    delivery_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL DEFAULT '',
                    recipient TEXT NOT NULL DEFAULT '',
                    recipient_role TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(operation_id) REFERENCES outbound_operations_v969(operation_id)
                );
                CREATE INDEX IF NOT EXISTS ix_outbound_deliveries_v969_message
                    ON outbound_operation_deliveries_v969(message_id);
                CREATE INDEX IF NOT EXISTS ix_outbound_deliveries_v969_operation
                    ON outbound_operation_deliveries_v969(operation_id);
                """
            )

    @staticmethod
    def _json_addresses(values: list[str] | None) -> str:
        clean = [str(value).strip() for value in values or [] if str(value).strip()]
        return json.dumps(clean, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode_addresses(value: str | None) -> list[str]:
        try:
            parsed = json.loads(str(value or "[]"))
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if str(item).strip()]

    def record_operation(
        self,
        *,
        operation_id: str,
        account_id: str,
        canonical_message_id: str,
        canonical_sent_archived: bool,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        deliveries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        op = str(operation_id or "").strip()
        if not op:
            raise ValueError("logical outbound operation_id is required")
        now = _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO outbound_operations_v969(
                    operation_id,account_id,canonical_message_id,canonical_sent_archived,
                    to_json,cc_json,bcc_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    canonical_message_id=excluded.canonical_message_id,
                    canonical_sent_archived=excluded.canonical_sent_archived,
                    to_json=excluded.to_json,
                    cc_json=excluded.cc_json,
                    bcc_json=excluded.bcc_json,
                    updated_at=excluded.updated_at
                """,
                (
                    op,
                    str(account_id or ""),
                    str(canonical_message_id or ""),
                    1 if canonical_sent_archived else 0,
                    self._json_addresses(to),
                    self._json_addresses(cc),
                    self._json_addresses(bcc),
                    now,
                    now,
                ),
            )
            for row in deliveries:
                did = str(row.get("delivery_id") or "").strip()
                if not did:
                    continue
                conn.execute(
                    """
                    INSERT INTO outbound_operation_deliveries_v969(
                        delivery_id,operation_id,message_id,recipient,recipient_role
                    ) VALUES(?,?,?,?,?)
                    ON CONFLICT(delivery_id) DO UPDATE SET
                        operation_id=excluded.operation_id,
                        message_id=excluded.message_id,
                        recipient=excluded.recipient,
                        recipient_role=excluded.recipient_role
                    """,
                    (
                        did,
                        op,
                        str(row.get("message_id") or ""),
                        str(row.get("recipient") or ""),
                        str(row.get("role") or row.get("recipient_role") or ""),
                    ),
                )
        return self.get_operation(op) or {}

    def _public_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["canonical_sent_archived"] = bool(value.get("canonical_sent_archived"))
        value["to"] = self._decode_addresses(value.pop("to_json", "[]"))
        value["cc"] = self._decode_addresses(value.pop("cc_json", "[]"))
        value["bcc"] = self._decode_addresses(value.pop("bcc_json", "[]"))
        return value

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM outbound_operations_v969 WHERE operation_id=?",
                (str(operation_id or ""),),
            ).fetchone()
            if not row:
                return None
            deliveries = conn.execute(
                """
                SELECT delivery_id,message_id,recipient,recipient_role AS role
                FROM outbound_operation_deliveries_v969
                WHERE operation_id=? ORDER BY rowid
                """,
                (str(operation_id or ""),),
            ).fetchall()
        result = self._public_row(row)
        result["deliveries"] = [dict(item) for item in deliveries]
        return result

    def delivery_by_message_id(
        self, account_id: str, message_id: str
    ) -> dict[str, Any] | None:
        """Return the exact delivery row and its logical operation for a Message-ID."""
        mid = str(message_id or "").strip()
        if not mid:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT d.delivery_id,d.message_id,d.recipient,
                       d.recipient_role AS role,o.operation_id,
                       o.canonical_message_id,o.canonical_sent_archived
                FROM outbound_operation_deliveries_v969 d
                JOIN outbound_operations_v969 o ON o.operation_id=d.operation_id
                WHERE o.account_id=? AND d.message_id=?
                ORDER BY d.rowid LIMIT 1
                """,
                (str(account_id or ""), mid),
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["canonical_sent_archived"] = bool(value.get("canonical_sent_archived"))
        return value

    def by_message_id(self, account_id: str, message_id: str) -> dict[str, Any] | None:
        mid = str(message_id or "").strip()
        if not mid:
            return None
        operation_id = ""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT o.operation_id FROM outbound_operations_v969 o
                WHERE o.account_id=? AND o.canonical_message_id=?
                LIMIT 1
                """,
                (str(account_id or ""), mid),
            ).fetchone()
            if not row:
                row = conn.execute(
                    """
                    SELECT o.operation_id FROM outbound_operations_v969 o
                    JOIN outbound_operation_deliveries_v969 d
                      ON d.operation_id=o.operation_id
                    WHERE o.account_id=? AND d.message_id=?
                    LIMIT 1
                    """,
                    (str(account_id or ""), mid),
                ).fetchone()
            if row:
                operation_id = str(row["operation_id"] or "")
        return self.get_operation(operation_id) if operation_id else None

    @staticmethod
    def _reply_reference_ids(raw: bytes) -> list[tuple[str, str]]:
        try:
            msg = BytesParser(policy=policy.default).parsebytes(raw)
        except Exception:
            return []
        ordered: list[tuple[str, str]] = []
        seen: set[str] = set()
        in_reply_to = str(msg.get("In-Reply-To") or "")
        references = str(msg.get("References") or "")
        for source, value in (("in_reply_to", in_reply_to), ("references", references)):
            ids = _MESSAGE_ID_RE.findall(value)
            if source == "references":
                ids = list(reversed(ids))
            for mid in ids:
                if mid in seen:
                    continue
                seen.add(mid)
                ordered.append((source, mid))
        return ordered

    def resolve_reply(self, account_id: str, raw: bytes) -> dict[str, Any] | None:
        """Resolve an inbound reply to one delivery and the existing logical root.

        This is read-only: it never creates or mutates a logical outbound operation.
        In-Reply-To wins; References are checked newest-first as a fallback.
        """
        account = str(account_id or "")
        for source, mid in self._reply_reference_ids(raw):
            delivery = self.delivery_by_message_id(account, mid)
            if delivery:
                return {
                    "logical_outbound_operation_id": str(delivery["operation_id"]),
                    "matched_delivery_id": str(delivery["delivery_id"]),
                    "matched_delivery_message_id": str(delivery["message_id"]),
                    "canonical_sent_message_id": str(delivery["canonical_message_id"]),
                    "recipient": str(delivery.get("recipient") or ""),
                    "recipient_role": str(delivery.get("role") or ""),
                    "matched_via": source,
                    "logical_outbound_root_created": False,
                }
            operation = self.by_message_id(account, mid)
            if operation:
                return {
                    "logical_outbound_operation_id": str(operation["operation_id"]),
                    "matched_delivery_id": "",
                    "matched_delivery_message_id": mid,
                    "canonical_sent_message_id": str(
                        operation.get("canonical_message_id") or ""
                    ),
                    "recipient": "",
                    "recipient_role": "",
                    "matched_via": source,
                    "logical_outbound_root_created": False,
                }
        return None


@lru_cache(maxsize=1)
def outbound_operation_store() -> OutboundOperationStore:
    return OutboundOperationStore()


__all__ = [
    "OutboundOperationStore",
    "outbound_operation_store",
    "resolve_outbound_operation_path",
]
