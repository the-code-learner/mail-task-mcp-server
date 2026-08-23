from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable


PENDING_APPROVAL_TTL_SECONDS = 300
_PENDING_DB_ENV = "POSTMASTER_MCP_PENDING_APPROVAL_DB_PATH"
_DEFAULT_PENDING_DB = Path("/data/mcp_pending_approvals_v969.db")


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _fallback_path() -> Path:
    configured = str(os.getenv("XDG_STATE_HOME") or "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    return root / "postmaster" / _DEFAULT_PENDING_DB.name


def resolve_pending_approval_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    configured = str(os.getenv(_PENDING_DB_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    try:
        if _DEFAULT_PENDING_DB.parent.is_dir() and os.access(
            _DEFAULT_PENDING_DB.parent, os.W_OK | os.X_OK
        ):
            return _DEFAULT_PENDING_DB
    except OSError:
        pass
    return _fallback_path()


class PendingApprovalStore:
    """Persistent server-side preview state.

    `preview_id` is correlation metadata, not authorization. Execute may omit it and is
    authorized only by an unexpired server-side pending row whose exact binding still
    matches current runtime state. Consumption is atomic and one-shot.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        ttl_seconds: int = PENDING_APPROVAL_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db_path = resolve_pending_approval_path(db_path)
        self.ttl_seconds = max(30, min(int(ttl_seconds), PENDING_APPROVAL_TTL_SECONDS))
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_pending_approvals_v969 (
                    preview_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    binding_digest TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS ix_mcp_pending_v969_match
                    ON mcp_pending_approvals_v969(scope,binding_digest,expires_at,consumed_at);
                """
            )

    def issue(self, scope: str, binding: dict[str, Any]) -> str:
        normalized_scope = str(scope or "").strip().lower()
        if not normalized_scope:
            raise ValueError("pending approval scope is required")
        now = int(self._clock())
        preview_id = "pv_" + secrets.token_urlsafe(12)
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM mcp_pending_approvals_v969 WHERE expires_at < ?",
                (now,),
            )
            conn.execute(
                """
                INSERT INTO mcp_pending_approvals_v969(
                    preview_id,scope,binding_digest,created_at,expires_at,consumed_at
                ) VALUES(?,?,?,?,?,NULL)
                """,
                (
                    preview_id,
                    normalized_scope,
                    _digest(binding),
                    now,
                    now + self.ttl_seconds,
                ),
            )
        return preview_id

    def consume_matching(
        self,
        scope: str,
        binding: dict[str, Any],
        *,
        preview_id: str | None = None,
    ) -> bool:
        """Atomically consume one exact pending approval.

        A supplied preview_id only narrows correlation; it is never sufficient without
        the exact binding. Omitting it remains valid, which deliberately prevents the ID
        from becoming bearer authority.
        """
        normalized_scope = str(scope or "").strip().lower()
        digest = _digest(binding)
        now = int(self._clock())
        candidate_id = str(preview_id or "").strip()
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM mcp_pending_approvals_v969 WHERE expires_at < ?",
                    (now,),
                )
                if candidate_id:
                    row = conn.execute(
                        """
                        SELECT preview_id FROM mcp_pending_approvals_v969
                        WHERE preview_id=? AND scope=? AND binding_digest=?
                          AND consumed_at IS NULL AND expires_at>=?
                        LIMIT 1
                        """,
                        (candidate_id, normalized_scope, digest, now),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT preview_id FROM mcp_pending_approvals_v969
                        WHERE scope=? AND binding_digest=?
                          AND consumed_at IS NULL AND expires_at>=?
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (normalized_scope, digest, now),
                    ).fetchone()
                if not row:
                    conn.rollback()
                    return False
                updated = conn.execute(
                    """
                    UPDATE mcp_pending_approvals_v969
                    SET consumed_at=?
                    WHERE preview_id=? AND consumed_at IS NULL
                    """,
                    (now, str(row["preview_id"])),
                )
                conn.commit()
                return int(updated.rowcount or 0) == 1
            except Exception:
                conn.rollback()
                raise

    def pending_count(self, scope: str | None = None) -> int:
        now = int(self._clock())
        with self._connect() as conn:
            if scope:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM mcp_pending_approvals_v969
                    WHERE scope=? AND consumed_at IS NULL AND expires_at>=?
                    """,
                    (str(scope).strip().lower(), now),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM mcp_pending_approvals_v969
                    WHERE consumed_at IS NULL AND expires_at>=?
                    """,
                    (now,),
                ).fetchone()
        return int(row[0] if row else 0)


class PendingConfirmationAdapter:
    """Compatibility adapter for preview/execute services that expect issue/consume."""

    def __init__(
        self,
        store: PendingApprovalStore,
        *,
        scope: str,
        state_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.store = store
        self.scope = str(scope).strip().lower()
        self.state_provider = state_provider
        self.ttl_seconds = store.ttl_seconds

    def _binding(self, binding: dict[str, Any]) -> dict[str, Any]:
        if self.state_provider is None:
            return {"operation_binding": binding}
        return {
            "operation_binding": binding,
            "current_state": self.state_provider(),
        }

    def issue(self, binding: dict[str, Any]) -> str:
        return self.store.issue(self.scope, self._binding(binding))

    def consume(self, preview_id: str | None, binding: dict[str, Any]) -> bool:
        return self.store.consume_matching(
            self.scope,
            self._binding(binding),
            preview_id=preview_id,
        )


__all__ = [
    "PENDING_APPROVAL_TTL_SECONDS",
    "PendingApprovalStore",
    "PendingConfirmationAdapter",
    "resolve_pending_approval_path",
]
