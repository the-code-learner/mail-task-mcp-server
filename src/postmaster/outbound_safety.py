from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


class OutboundSafetyError(RuntimeError):
    """Raised when an outbound operation is rejected before SMTP."""


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes_sha256__": hashlib.sha256(value).hexdigest(), "length": len(value)}
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class OutboundSafetyStore:
    """Persistent, process-safe idempotency and near-duplicate barrier.

    Explicit idempotency is stronger than the heuristic duplicate guard:
    `force_send` never overrides an explicit key that already exists.
    """

    TERMINAL_REPLAY_STATES = {"sent"}
    BLOCKING_STATES = {"in_progress", "delivery_uncertain"}
    FAILED_STATES = {"failed"}

    def __init__(
        self,
        db_path: str | None = None,
        *,
        duplicate_window_seconds: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = db_path or os.getenv("EMAIL_ANALYTICS_DB_PATH", "/data/email_analytics.db")
        self.duplicate_window_seconds = max(
            0,
            int(
                duplicate_window_seconds
                if duplicate_window_seconds is not None
                else os.getenv("OUTBOUND_DUPLICATE_WINDOW_SECONDS", "120")
            ),
        )
        self.clock = clock
        self._lock = threading.RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS outbound_operations (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    payload_hash TEXT NOT NULL,
                    duplicate_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '',
                    error_text TEXT NOT NULL DEFAULT '',
                    created_at_epoch REAL NOT NULL,
                    updated_at_epoch REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_outbound_idempotency
                  ON outbound_operations(account_id, action, idempotency_key)
                  WHERE idempotency_key <> '';
                CREATE INDEX IF NOT EXISTS ix_outbound_duplicate_guard
                  ON outbound_operations(account_id, action, duplicate_fingerprint, created_at_epoch DESC);
                """
            )

    @staticmethod
    def _clean_key(value: str | None) -> str:
        key = (value or "").strip()
        if not key:
            return ""
        if len(key) > 240:
            raise OutboundSafetyError("idempotency_key must be at most 240 characters")
        if "\r" in key or "\n" in key:
            raise OutboundSafetyError("idempotency_key contains invalid control characters")
        return key

    @staticmethod
    def _decoded_result(row: sqlite3.Row) -> dict[str, Any]:
        raw = str(row["result_json"] or "")
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except Exception:
            return {}
        return value if isinstance(value, dict) else {"result": value}

    @classmethod
    def _replay(cls, row: sqlite3.Row, *, heuristic: bool) -> dict[str, Any]:
        result = cls._decoded_result(row)
        result.update(
            {
                "outbound_operation_id": str(row["id"]),
                "idempotent_replay": not heuristic,
                "duplicate_guard_replay": heuristic,
                "smtp_send_performed": False,
            }
        )
        return result

    @staticmethod
    def _blocking_error(row: sqlite3.Row, *, explicit: bool) -> OutboundSafetyError:
        state = str(row["state"])
        operation_id = str(row["id"])
        if state == "delivery_uncertain":
            return OutboundSafetyError(
                "Previous outbound operation has delivery_uncertain state; "
                f"automatic retry is blocked to prevent duplicate delivery (operation {operation_id})"
            )
        if state == "in_progress":
            return OutboundSafetyError(
                "Equivalent outbound operation is already in progress; "
                f"no second SMTP submission is allowed (operation {operation_id})"
            )
        if state == "failed" and explicit:
            detail = str(row["error_text"] or "previous outbound attempt failed")
            return OutboundSafetyError(
                "This idempotency key already completed with a failure; "
                f"the previous result is retained and no automatic resend is performed: {detail[:500]}"
            )
        return OutboundSafetyError("Equivalent outbound operation is blocked")

    def _reserve(
        self,
        *,
        account_id: str,
        action: str,
        key: str,
        full_hash: str,
        fingerprint: str,
        force_send: bool,
    ) -> tuple[str, sqlite3.Row | None, bool]:
        now = float(self.clock())
        cutoff = now - float(self.duplicate_window_seconds)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if key:
                    row = conn.execute(
                        """
                        SELECT * FROM outbound_operations
                        WHERE account_id=? AND action=? AND idempotency_key=?
                        LIMIT 1
                        """,
                        (account_id, action, key),
                    ).fetchone()
                    if row is not None:
                        if str(row["payload_hash"]) != full_hash:
                            raise OutboundSafetyError(
                                "idempotency_key was already used with a different outbound payload"
                            )
                        state = str(row["state"])
                        conn.execute("COMMIT")
                        if state in self.TERMINAL_REPLAY_STATES:
                            return str(row["id"]), row, False
                        raise self._blocking_error(row, explicit=True)

                if not force_send and self.duplicate_window_seconds > 0:
                    row = conn.execute(
                        """
                        SELECT * FROM outbound_operations
                        WHERE account_id=? AND action=? AND duplicate_fingerprint=?
                          AND created_at_epoch>=?
                          AND state IN ('sent','in_progress','delivery_uncertain')
                        ORDER BY created_at_epoch DESC
                        LIMIT 1
                        """,
                        (account_id, action, fingerprint, cutoff),
                    ).fetchone()
                    if row is not None:
                        state = str(row["state"])
                        conn.execute("COMMIT")
                        if state == "sent":
                            return str(row["id"]), row, True
                        raise self._blocking_error(row, explicit=False)

                operation_id = "out_" + uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO outbound_operations(
                        id,account_id,action,idempotency_key,payload_hash,
                        duplicate_fingerprint,state,created_at_epoch,updated_at_epoch
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        operation_id,
                        account_id,
                        action,
                        key,
                        full_hash,
                        fingerprint,
                        "in_progress",
                        now,
                        now,
                    ),
                )
                conn.execute("COMMIT")
                return operation_id, None, False
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    def _finish(
        self,
        operation_id: str,
        *,
        state: str,
        result: dict[str, Any] | None = None,
        error_text: str = "",
    ) -> None:
        now = float(self.clock())
        result_json = canonical_json(result or {}) if result is not None else ""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    UPDATE outbound_operations
                    SET state=?,result_json=?,error_text=?,updated_at_epoch=?
                    WHERE id=?
                    """,
                    (state, result_json, error_text[:4000], now, operation_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def execute(
        self,
        *,
        account_id: str,
        action: str,
        payload: dict[str, Any],
        duplicate_payload: dict[str, Any] | None,
        callback: Callable[[], dict[str, Any]],
        idempotency_key: str | None = None,
        force_send: bool = False,
    ) -> dict[str, Any]:
        account = (account_id or "default").strip() or "default"
        action_name = (action or "send_email").strip() or "send_email"
        key = self._clean_key(idempotency_key)
        full_hash = payload_hash(payload)
        fingerprint = payload_hash(duplicate_payload if duplicate_payload is not None else payload)

        operation_id, replay_row, heuristic = self._reserve(
            account_id=account,
            action=action_name,
            key=key,
            full_hash=full_hash,
            fingerprint=fingerprint,
            force_send=bool(force_send),
        )
        if replay_row is not None:
            return self._replay(replay_row, heuristic=heuristic)

        try:
            result = callback()
            if not isinstance(result, dict):
                result = {"result": result}
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            state = "delivery_uncertain" if "delivery_uncertain" in str(exc).casefold() else "failed"
            self._finish(operation_id, state=state, error_text=text)
            raise

        state = "delivery_uncertain" if str(result.get("delivery_state") or "").casefold() == "delivery_uncertain" else "sent"
        stored = dict(result)
        stored.update(
            {
                "outbound_operation_id": operation_id,
                "idempotency_key_recorded": bool(key),
                "duplicate_guard_recorded": True,
                "smtp_send_performed": True,
            }
        )
        self._finish(operation_id, state=state, result=stored)
        if state == "delivery_uncertain":
            raise OutboundSafetyError(
                "Outbound operation ended in delivery_uncertain state; retry is blocked"
            )
        return stored

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM outbound_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["result"] = self._decoded_result(row)
        return result
