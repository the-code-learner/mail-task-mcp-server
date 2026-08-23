from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable


CONFIRMATION_TTL_SECONDS = 300
_CONFIRMATION_KEY_ENV = "POSTMASTER_MCP_CONFIRMATION_KEY_PATH"
_CONFIRMATION_DB_ENV = "POSTMASTER_MCP_CONFIRMATION_DB_PATH"
_DEFAULT_CONFIRMATION_KEY_PATH = Path("/data/mcp_confirmation_v967.key")
_DEFAULT_CONFIRMATION_DB_PATH = Path("/data/mcp_confirmation_v967.db")
_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,95}$")
_TOKEN_PREFIX = "pmc1"
_MAX_FUTURE_SKEW_SECONDS = 30


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _binding_digest(binding: dict[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(binding)).hexdigest()


def _user_state_directory() -> Path:
    configured = str(os.getenv("XDG_STATE_HOME") or "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    return root / "postmaster"


def _default_path(default_path: Path) -> Path:
    parent = default_path.parent
    try:
        if parent.is_dir() and os.access(parent, os.W_OK | os.X_OK):
            return default_path
    except OSError:
        pass
    return _user_state_directory() / default_path.name


def _resolve_path(
    explicit: str | Path | None,
    env_name: str,
    default_path: Path,
) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    configured = str(os.getenv(env_name) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return _default_path(default_path)


def resolve_confirmation_paths(
    *,
    key_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Resolve persistent confirmation storage without creating files or directories.

    Explicit function arguments take precedence over environment overrides. With neither set,
    production prefers the persistent /data volume when it already exists and is writable;
    non-container/CI processes fall back to the user's persistent state directory.
    """
    return (
        _resolve_path(key_path, _CONFIRMATION_KEY_ENV, _DEFAULT_CONFIRMATION_KEY_PATH),
        _resolve_path(db_path, _CONFIRMATION_DB_ENV, _DEFAULT_CONFIRMATION_DB_PATH),
    )


class PersistentConfirmationTokens:
    """Stateless authenticated previews with persistent one-time nonce consumption.

    Issuing a token performs no database write. The service key and nonce table are initialized
    when this object is constructed (application startup in the composed runtime), so MCP preview
    tools remain genuinely read-only. A valid execute attempt atomically consumes the token nonce
    before its exact binding is accepted, which makes mismatched attempts one-time as well.
    """

    def __init__(
        self,
        *,
        scope: str,
        ttl_seconds: int = CONFIRMATION_TTL_SECONDS,
        key_path: str | Path | None = None,
        db_path: str | Path | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        normalized_scope = str(scope or "").strip().lower()
        if not _SCOPE_RE.fullmatch(normalized_scope):
            raise ValueError("confirmation scope is invalid")
        self.scope = normalized_scope
        self.ttl_seconds = max(30, min(int(ttl_seconds), CONFIRMATION_TTL_SECONDS))
        self.key_path, self.db_path = resolve_confirmation_paths(
            key_path=key_path,
            db_path=db_path,
        )
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._key = self._load_or_create_key()
        self._init_db()

    def _load_or_create_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            encoded = self.key_path.read_bytes().strip()
        except FileNotFoundError:
            raw = secrets.token_bytes(32)
            encoded = base64.urlsafe_b64encode(raw)
            try:
                fd = os.open(
                    self.key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                encoded = self.key_path.read_bytes().strip()
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.chmod(self.key_path, 0o600)
                except OSError:
                    pass
        try:
            raw = base64.urlsafe_b64decode(encoded)
        except Exception as exc:
            raise RuntimeError("persistent MCP confirmation key is invalid") from exc
        if len(raw) != 32:
            raise RuntimeError("persistent MCP confirmation key is invalid")
        return raw

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_confirmation_consumed (
                    scope TEXT NOT NULL,
                    nonce_digest TEXT NOT NULL,
                    consumed_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY(scope, nonce_digest)
                );
                CREATE INDEX IF NOT EXISTS ix_mcp_confirmation_expiry
                    ON mcp_confirmation_consumed(expires_at);
                """
            )

    def issue(self, binding: dict[str, Any]) -> str:
        now = int(self._clock())
        payload = {
            "v": 1,
            "scope": self.scope,
            "nonce": secrets.token_urlsafe(24),
            "iat": now,
            "exp": now + self.ttl_seconds,
            "binding": _binding_digest(binding),
        }
        canonical = _json_bytes(payload)
        signature = hmac.new(self._key, canonical, hashlib.sha256).digest()
        return f"{_TOKEN_PREFIX}.{_b64url(canonical)}.{_b64url(signature)}"

    def _decode_authenticated(self, token: str | None) -> dict[str, Any] | None:
        candidate = str(token or "").strip()
        parts = candidate.split(".")
        if len(parts) != 3 or parts[0] != _TOKEN_PREFIX:
            return None
        try:
            canonical = _b64url_decode(parts[1])
            signature = _b64url_decode(parts[2])
            expected = hmac.new(self._key, canonical, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(canonical.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return None
        if str(payload.get("scope") or "") != self.scope:
            return None
        nonce = str(payload.get("nonce") or "")
        binding = str(payload.get("binding") or "")
        try:
            issued_at = int(payload.get("iat"))
            expires_at = int(payload.get("exp"))
        except (TypeError, ValueError):
            return None
        now = int(self._clock())
        if not nonce or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", nonce):
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", binding):
            return None
        if expires_at <= issued_at or expires_at - issued_at > self.ttl_seconds:
            return None
        if issued_at > now + _MAX_FUTURE_SKEW_SECONDS or expires_at < now:
            return None
        return {
            "nonce": nonce,
            "binding": binding,
            "iat": issued_at,
            "exp": expires_at,
        }

    def _consume_nonce_once(self, nonce: str, expires_at: int) -> bool:
        nonce_digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        now = int(self._clock())
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM mcp_confirmation_consumed WHERE expires_at < ?",
                    (now,),
                )
                conn.execute(
                    """
                    INSERT INTO mcp_confirmation_consumed(
                        scope,nonce_digest,consumed_at,expires_at
                    ) VALUES(?,?,?,?)
                    """,
                    (self.scope, nonce_digest, now, int(expires_at)),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                conn.rollback()
                return False
            except Exception:
                conn.rollback()
                raise

    def consume(self, token: str | None, binding: dict[str, Any]) -> bool:
        payload = self._decode_authenticated(token)
        if payload is None:
            return False
        if not self._consume_nonce_once(str(payload["nonce"]), int(payload["exp"])):
            return False
        return hmac.compare_digest(str(payload["binding"]), _binding_digest(binding))

    def clear_consumed(self) -> None:
        """Delete consumed nonces for this scope. Intended for deterministic tests only."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM mcp_confirmation_consumed WHERE scope=?",
                (self.scope,),
            )

    def consumed_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM mcp_confirmation_consumed WHERE scope=?",
                (self.scope,),
            ).fetchone()
        return int(row[0] if row else 0)


class StateBoundConfirmationTokens:
    """Bind an existing preview contract to an additional live-state snapshot."""

    def __init__(
        self,
        tokens: PersistentConfirmationTokens,
        state_provider: Callable[[], dict[str, Any]],
    ) -> None:
        self.tokens = tokens
        self.state_provider = state_provider
        self.ttl_seconds = tokens.ttl_seconds

    def _binding(self, binding: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation_binding": binding,
            "current_state": self.state_provider(),
        }

    def issue(self, binding: dict[str, Any]) -> str:
        return self.tokens.issue(self._binding(binding))

    def consume(self, token: str, binding: dict[str, Any]) -> bool:
        return self.tokens.consume(token, self._binding(binding))


__all__ = [
    "CONFIRMATION_TTL_SECONDS",
    "PersistentConfirmationTokens",
    "StateBoundConfirmationTokens",
    "resolve_confirmation_paths",
]
