from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Callable

from .mail_bridge import MailBridgeError, _message_to_dict


_SYNC_INTERVAL_SECONDS = 300.0
_UID_RE = re.compile(rb"\bUID\s+(\d+)\b", re.I)
_SIZE_RE = re.compile(rb"\bRFC822\.SIZE\s+(\d+)\b", re.I)
_FLAGS_RE = re.compile(rb"\bFLAGS\s*\(([^)]*)\)", re.I)
_UIDVALIDITY_RE = re.compile(rb"\bUIDVALIDITY\s+(\d+)\b", re.I)
_HIGHESTMODSEQ_RE = re.compile(rb"\bHIGHESTMODSEQ\s+(\d+)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _flags_from_fetch(value: bytes) -> list[str]:
    match = _FLAGS_RE.search(value or b"")
    if not match:
        return []
    return [part.decode("utf-8", errors="replace") for part in match.group(1).split() if part]


def _metadata_bytes(data: Any) -> bytes:
    for item in data or []:
        raw = item[0] if isinstance(item, tuple) else item
        if isinstance(raw, bytes):
            return raw
    return b""


def _payload_bytes(data: Any) -> bytes:
    for item in data or []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return b""


def _status_number(data: Any, pattern: re.Pattern[bytes]) -> int | None:
    raw = b" ".join(item for item in (data or []) if isinstance(item, bytes))
    match = pattern.search(raw)
    return int(match.group(1)) if match else None


class MailboxCacheStore:
    """Durable read model for Inbox/WebGUI state.

    IMAP remains authoritative. This store only caches mailbox metadata, headers, flags,
    on-demand message bodies and remote passive resources. It has no send/write-to-IMAP
    capability.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.getenv("MAILBOX_CACHE_DB_PATH", "/data/mailbox_cache.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mailbox_cache_state (
                    account_id TEXT NOT NULL,
                    mailbox TEXT NOT NULL,
                    uidvalidity INTEGER,
                    highest_uid INTEGER NOT NULL DEFAULT 0,
                    highest_modseq INTEGER,
                    last_sync_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    last_sync_kind TEXT NOT NULL DEFAULT '',
                    message_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(account_id, mailbox)
                );
                CREATE TABLE IF NOT EXISTS mailbox_cache_mailboxes (
                    account_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'other',
                    flags_json TEXT NOT NULL DEFAULT '[]',
                    last_sync_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(account_id, name)
                );
                CREATE TABLE IF NOT EXISTS mailbox_cache_messages (
                    account_id TEXT NOT NULL,
                    mailbox TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    uid_int INTEGER NOT NULL,
                    uidvalidity INTEGER,
                    message_id TEXT NOT NULL DEFAULT '',
                    in_reply_to TEXT NOT NULL DEFAULT '',
                    references_value TEXT NOT NULL DEFAULT '',
                    date_value TEXT,
                    from_text TEXT NOT NULL DEFAULT '',
                    from_json TEXT NOT NULL DEFAULT '[]',
                    to_text TEXT NOT NULL DEFAULT '',
                    to_json TEXT NOT NULL DEFAULT '[]',
                    cc_text TEXT NOT NULL DEFAULT '',
                    cc_json TEXT NOT NULL DEFAULT '[]',
                    subject TEXT NOT NULL DEFAULT '',
                    snippet TEXT NOT NULL DEFAULT '',
                    seen INTEGER NOT NULL DEFAULT 0,
                    flags_json TEXT NOT NULL DEFAULT '[]',
                    size_bytes INTEGER,
                    header_bytes BLOB,
                    raw_bytes BLOB,
                    body_text TEXT NOT NULL DEFAULT '',
                    body_html TEXT NOT NULL DEFAULT '',
                    content_truncated INTEGER NOT NULL DEFAULT 0,
                    cached_at TEXT NOT NULL,
                    body_cached_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(account_id, mailbox, uid)
                );
                CREATE INDEX IF NOT EXISTS ix_mailbox_cache_messages_order
                    ON mailbox_cache_messages(account_id, mailbox, uid_int DESC);
                CREATE INDEX IF NOT EXISTS ix_mailbox_cache_messages_seen
                    ON mailbox_cache_messages(account_id, mailbox, seen, uid_int DESC);
                CREATE TABLE IF NOT EXISTS mailbox_cache_remote_resources (
                    cache_key TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    mailbox TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    url TEXT NOT NULL,
                    url_hash TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT '',
                    body BLOB,
                    http_status INTEGER,
                    redirect_location TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL,
                    classification TEXT NOT NULL DEFAULT 'unknown',
                    tracking_score INTEGER NOT NULL DEFAULT 0,
                    error_state TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS ix_mailbox_cache_resource_message
                    ON mailbox_cache_remote_resources(account_id, mailbox, uid);
                CREATE TABLE IF NOT EXISTS mailbox_cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR REPLACE INTO mailbox_cache_meta(key,value)
                    VALUES('schema_version','9.6.3');
                """
            )

    def list_mailboxes(self, account_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name,role,flags_json,last_sync_at FROM mailbox_cache_mailboxes "
                "WHERE account_id=? ORDER BY CASE role WHEN 'received' THEN 0 WHEN 'sent' THEN 1 "
                "WHEN 'spam' THEN 2 WHEN 'drafts' THEN 3 WHEN 'trash' THEN 4 ELSE 5 END, lower(name)",
                (account_id,),
            ).fetchall()
        return [
            {
                "name": str(row["name"]),
                "role": str(row["role"]),
                "flags": json.loads(str(row["flags_json"] or "[]")),
                "last_sync_at": str(row["last_sync_at"] or ""),
            }
            for row in rows
        ]

    def replace_mailboxes(self, account_id: str, rows: list[dict[str, Any]], *, synced_at: str) -> None:
        names = [str(row.get("name") or "").strip() for row in rows if str(row.get("name") or "").strip()]
        with self._lock, self._connect() as conn:
            for row in rows:
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                conn.execute(
                    """
                    INSERT INTO mailbox_cache_mailboxes(account_id,name,role,flags_json,last_sync_at)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(account_id,name) DO UPDATE SET
                        role=excluded.role, flags_json=excluded.flags_json, last_sync_at=excluded.last_sync_at
                    """,
                    (account_id, name, str(row.get("role") or "other"), _json(row.get("flags") or []), synced_at),
                )
            if names:
                placeholders = ",".join("?" for _ in names)
                conn.execute(
                    f"DELETE FROM mailbox_cache_mailboxes WHERE account_id=? AND name NOT IN ({placeholders})",
                    (account_id, *names),
                )

    def state(self, account_id: str, mailbox: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_cache_state WHERE account_id=? AND mailbox=?",
                (account_id, mailbox),
            ).fetchone()
        return dict(row) if row else {
            "account_id": account_id,
            "mailbox": mailbox,
            "uidvalidity": None,
            "highest_uid": 0,
            "highest_modseq": None,
            "last_sync_at": "",
            "last_error": "",
            "last_sync_kind": "never",
            "message_count": 0,
        }

    def reset_mailbox(self, account_id: str, mailbox: str, uidvalidity: int | None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM mailbox_cache_messages WHERE account_id=? AND mailbox=?",
                (account_id, mailbox),
            )
            conn.execute(
                "DELETE FROM mailbox_cache_remote_resources WHERE account_id=? AND mailbox=?",
                (account_id, mailbox),
            )
            conn.execute(
                """
                INSERT INTO mailbox_cache_state(account_id,mailbox,uidvalidity,highest_uid,last_sync_kind)
                VALUES(?,?,?,0,'uidvalidity-reset')
                ON CONFLICT(account_id,mailbox) DO UPDATE SET
                    uidvalidity=excluded.uidvalidity, highest_uid=0, highest_modseq=NULL,
                    last_sync_kind='uidvalidity-reset', last_error=''
                """,
                (account_id, mailbox, uidvalidity),
            )

    def cached_uids(self, account_id: str, mailbox: str) -> set[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT uid_int FROM mailbox_cache_messages WHERE account_id=? AND mailbox=?",
                (account_id, mailbox),
            ).fetchall()
        return {int(row[0]) for row in rows}

    def delete_missing_uids(self, account_id: str, mailbox: str, live_uids: set[int]) -> int:
        cached = self.cached_uids(account_id, mailbox)
        missing = sorted(cached - live_uids)
        if not missing:
            return 0
        with self._lock, self._connect() as conn:
            conn.executemany(
                "DELETE FROM mailbox_cache_messages WHERE account_id=? AND mailbox=? AND uid_int=?",
                [(account_id, mailbox, uid) for uid in missing],
            )
        return len(missing)

    def upsert_header(
        self,
        *,
        account_id: str,
        mailbox: str,
        uid: str,
        uidvalidity: int | None,
        row: dict[str, Any],
        flags: list[str],
        size_bytes: int | None,
        header_bytes: bytes,
    ) -> None:
        now = _now()
        seen = any(str(flag).casefold() == r"\seen" for flag in flags)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mailbox_cache_messages(
                    account_id,mailbox,uid,uid_int,uidvalidity,message_id,in_reply_to,references_value,
                    date_value,from_text,from_json,to_text,to_json,cc_text,cc_json,subject,seen,flags_json,
                    size_bytes,header_bytes,cached_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account_id,mailbox,uid) DO UPDATE SET
                    uidvalidity=excluded.uidvalidity,message_id=excluded.message_id,
                    in_reply_to=excluded.in_reply_to,references_value=excluded.references_value,
                    date_value=excluded.date_value,from_text=excluded.from_text,from_json=excluded.from_json,
                    to_text=excluded.to_text,to_json=excluded.to_json,cc_text=excluded.cc_text,cc_json=excluded.cc_json,
                    subject=excluded.subject,seen=excluded.seen,flags_json=excluded.flags_json,
                    size_bytes=excluded.size_bytes,header_bytes=excluded.header_bytes,cached_at=excluded.cached_at
                """,
                (
                    account_id, mailbox, uid, int(uid), uidvalidity,
                    str(row.get("message_id") or ""), str(row.get("in_reply_to") or ""),
                    str(row.get("references") or ""), row.get("date"), str(row.get("from") or ""),
                    _json(row.get("from_addresses") or []), str(row.get("to") or ""),
                    _json(row.get("to_addresses") or []), str(row.get("cc") or ""),
                    _json(row.get("cc_addresses") or []), str(row.get("subject") or ""),
                    int(seen), _json(flags), size_bytes, header_bytes, now,
                ),
            )

    def update_flags(self, account_id: str, mailbox: str, flags_by_uid: dict[int, list[str]]) -> None:
        if not flags_by_uid:
            return
        with self._lock, self._connect() as conn:
            conn.executemany(
                "UPDATE mailbox_cache_messages SET seen=?,flags_json=?,cached_at=? "
                "WHERE account_id=? AND mailbox=? AND uid_int=?",
                [
                    (
                        int(any(str(flag).casefold() == r"\seen" for flag in flags)),
                        _json(flags), _now(), account_id, mailbox, uid,
                    )
                    for uid, flags in flags_by_uid.items()
                ],
            )

    def finish_sync(
        self,
        account_id: str,
        mailbox: str,
        *,
        uidvalidity: int | None,
        highest_uid: int,
        highest_modseq: int | None,
        message_count: int,
        sync_kind: str,
        error: str = "",
        synced_at: str | None = None,
    ) -> dict[str, Any]:
        stamp = synced_at or _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mailbox_cache_state(
                    account_id,mailbox,uidvalidity,highest_uid,highest_modseq,last_sync_at,last_error,last_sync_kind,message_count
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account_id,mailbox) DO UPDATE SET
                    uidvalidity=excluded.uidvalidity,highest_uid=excluded.highest_uid,
                    highest_modseq=excluded.highest_modseq,last_sync_at=excluded.last_sync_at,
                    last_error=excluded.last_error,last_sync_kind=excluded.last_sync_kind,message_count=excluded.message_count
                """,
                (account_id, mailbox, uidvalidity, highest_uid, highest_modseq, stamp, error, sync_kind, message_count),
            )
        return self.state(account_id, mailbox)

    def mark_sync_error(self, account_id: str, mailbox: str, error: BaseException) -> None:
        current = self.state(account_id, mailbox)
        self.finish_sync(
            account_id,
            mailbox,
            uidvalidity=current.get("uidvalidity"),
            highest_uid=int(current.get("highest_uid") or 0),
            highest_modseq=current.get("highest_modseq"),
            message_count=int(current.get("message_count") or 0),
            sync_kind="error",
            error=f"{type(error).__name__}: {error}"[:500],
        )

    def query_messages(
        self,
        *,
        account_id: str,
        mailbox: str,
        page: int = 1,
        page_size: int = 25,
        subject: str = "",
        text: str = "",
        unread_only: bool = False,
        since_days: int = 90,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        clauses = ["account_id=?", "mailbox=?"]
        values: list[Any] = [account_id, mailbox]
        subject_value = (subject or "").strip().casefold()
        text_value = (text or "").strip().casefold()
        if subject_value:
            clauses.append("lower(subject) LIKE ?")
            values.append(f"%{subject_value}%")
        if text_value:
            clauses.append("(lower(subject) LIKE ? OR lower(from_text) LIKE ? OR lower(to_text) LIKE ? OR lower(cc_text) LIKE ? OR lower(snippet) LIKE ? OR lower(body_text) LIKE ?)")
            values.extend([f"%{text_value}%"] * 6)
        if unread_only:
            clauses.append("seen=0")
        if since_days >= 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=min(int(since_days), 3650))).isoformat()
            clauses.append("(date_value IS NULL OR date_value='' OR date_value>=?)")
            values.append(cutoff)
        where = " AND ".join(clauses)
        offset = (page - 1) * page_size
        with self._connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM mailbox_cache_messages WHERE {where}", values).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM mailbox_cache_messages WHERE {where} ORDER BY uid_int DESC LIMIT ? OFFSET ?",
                (*values, page_size, offset),
            ).fetchall()
        messages = [self._public_message(row, include_body=False) for row in rows]
        return {"messages": messages, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def _public_message(row: sqlite3.Row | dict[str, Any], *, include_body: bool) -> dict[str, Any]:
        value = dict(row)
        result = {
            "mailbox": str(value.get("mailbox") or ""),
            "uid": str(value.get("uid") or ""),
            "message_id": str(value.get("message_id") or ""),
            "in_reply_to": str(value.get("in_reply_to") or ""),
            "references": str(value.get("references_value") or ""),
            "date": value.get("date_value"),
            "from": str(value.get("from_text") or ""),
            "from_addresses": json.loads(str(value.get("from_json") or "[]")),
            "to": str(value.get("to_text") or ""),
            "to_addresses": json.loads(str(value.get("to_json") or "[]")),
            "cc": str(value.get("cc_text") or ""),
            "cc_addresses": json.loads(str(value.get("cc_json") or "[]")),
            "subject": str(value.get("subject") or ""),
            "snippet": str(value.get("snippet") or ""),
            "seen": bool(value.get("seen")),
            "flags": json.loads(str(value.get("flags_json") or "[]")),
            "size_bytes": value.get("size_bytes"),
            "body_cached": bool(value.get("body_cached_at")),
            "body_cached_at": str(value.get("body_cached_at") or ""),
        }
        if include_body:
            result.update(
                {
                    "body": str(value.get("body_text") or ""),
                    "body_html": str(value.get("body_html") or ""),
                    "content_truncated": bool(value.get("content_truncated")),
                }
            )
        return result

    def get_message(self, account_id: str, mailbox: str, uid: str, *, include_body: bool = True) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_cache_messages WHERE account_id=? AND mailbox=? AND uid=?",
                (account_id, mailbox, str(uid)),
            ).fetchone()
        return self._public_message(row, include_body=include_body) if row else None

    def raw_message(self, account_id: str, mailbox: str, uid: str) -> bytes | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT raw_bytes FROM mailbox_cache_messages WHERE account_id=? AND mailbox=? AND uid=?",
                (account_id, mailbox, str(uid)),
            ).fetchone()
        if not row or row[0] is None:
            return None
        return bytes(row[0])

    def store_body(self, account_id: str, mailbox: str, uid: str, raw: bytes, *, truncated: bool) -> dict[str, Any]:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        parsed = _message_to_dict(msg, uid=str(uid), mailbox=mailbox, include_body=not truncated, truncated=truncated)
        body = str(parsed.get("body") or "")
        snippet = re.sub(r"\s+", " ", body).strip()[:600]
        stamp = _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE mailbox_cache_messages SET raw_bytes=?,body_text=?,body_html=?,snippet=?,
                    content_truncated=?,body_cached_at=?,cached_at=?
                WHERE account_id=? AND mailbox=? AND uid=?
                """,
                (
                    raw, body, str(parsed.get("body_html") or ""), snippet, int(bool(truncated)), stamp, stamp,
                    account_id, mailbox, str(uid),
                ),
            )
            changed = conn.total_changes
        if not changed:
            raise MailBridgeError(f"UID {uid} is not present in the local mailbox cache")
        return self.get_message(account_id, mailbox, str(uid), include_body=True) or {}

    @staticmethod
    def resource_key(account_id: str, mailbox: str, uid: str, url_hash: str) -> str:
        return f"{account_id}\x1f{mailbox}\x1f{uid}\x1f{url_hash}"

    def get_resource(self, cache_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_cache_remote_resources WHERE cache_key=?", (cache_key,)
            ).fetchone()
        return dict(row) if row else None

    def put_resource(
        self,
        *,
        cache_key: str,
        account_id: str,
        mailbox: str,
        uid: str,
        url: str,
        url_hash: str,
        content_type: str,
        body: bytes | None,
        http_status: int | None,
        redirect_location: str,
        classification: str,
        tracking_score: int,
        error_state: str = "",
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mailbox_cache_remote_resources(
                    cache_key,account_id,mailbox,uid,url,url_hash,content_type,body,http_status,
                    redirect_location,fetched_at,classification,tracking_score,error_state
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    content_type=excluded.content_type,body=excluded.body,http_status=excluded.http_status,
                    redirect_location=excluded.redirect_location,fetched_at=excluded.fetched_at,
                    classification=excluded.classification,tracking_score=excluded.tracking_score,
                    error_state=excluded.error_state
                """,
                (
                    cache_key, account_id, mailbox, str(uid), url, url_hash, content_type, body,
                    http_status, redirect_location, _now(), classification, int(tracking_score), error_state,
                ),
            )
        return self.get_resource(cache_key) or {}


@dataclass
class _InflightSync:
    event: threading.Event
    result: dict[str, Any] | None = None
    error: BaseException | None = None


class MailboxCacheSynchronizer:
    """Read-only incremental IMAP synchronizer with per-mailbox request coalescing."""

    def __init__(self, store: MailboxCacheStore) -> None:
        self.store = store
        self._guard = threading.RLock()
        self._inflight: dict[tuple[str, str], _InflightSync] = {}
        self.network_sync_count = 0

    def sync_mailbox(self, client: Any, *, account_id: str, mailbox: str, role: str = "other") -> dict[str, Any]:
        key = (account_id, mailbox)
        with self._guard:
            state = self._inflight.get(key)
            if state is None:
                state = _InflightSync(threading.Event())
                self._inflight[key] = state
                leader = True
            else:
                leader = False
        if not leader:
            state.event.wait(timeout=60.0)
            if state.error is not None:
                raise state.error
            return dict(state.result or {})

        try:
            self.network_sync_count += 1
            result = self._sync_mailbox_network(client, account_id=account_id, mailbox=mailbox, role=role)
            state.result = dict(result)
            return result
        except BaseException as exc:
            state.error = exc
            self.store.mark_sync_error(account_id, mailbox, exc)
            raise
        finally:
            with self._guard:
                self._inflight.pop(key, None)
                state.event.set()

    def _sync_mailbox_network(self, client: Any, *, account_id: str, mailbox: str, role: str) -> dict[str, Any]:
        previous = self.store.state(account_id, mailbox)
        with client._imap() as conn:
            typ, status_data = conn.status(mailbox, "(UIDVALIDITY MESSAGES HIGHESTMODSEQ)")
            if typ != "OK":
                status_data = []
            uidvalidity = _status_number(status_data, _UIDVALIDITY_RE)
            highest_modseq = _status_number(status_data, _HIGHESTMODSEQ_RE)
            prior_uidvalidity = previous.get("uidvalidity")
            reset = bool(prior_uidvalidity is not None and uidvalidity is not None and int(prior_uidvalidity) != uidvalidity)
            if reset:
                self.store.reset_mailbox(account_id, mailbox, uidvalidity)
                previous = self.store.state(account_id, mailbox)

            client._select(conn, mailbox, readonly=True)
            typ, data = conn.uid("SEARCH", None, "ALL")
            if typ != "OK" or not data:
                live_uids: list[int] = []
            else:
                live_uids = [int(value) for value in data[0].decode("ascii", errors="ignore").split() if value.isdigit()]
            live_set = set(live_uids)
            cached = self.store.cached_uids(account_id, mailbox)
            new_uids = sorted(live_set - cached)
            deleted = self.store.delete_missing_uids(account_id, mailbox, live_set)

            fetched_headers = 0
            for uid_int in new_uids:
                uid = str(uid_int)
                typ, fetch_data = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER] FLAGS RFC822.SIZE)")
                if typ != "OK" or not fetch_data:
                    continue
                header_bytes = _payload_bytes(fetch_data)
                if not header_bytes:
                    continue
                metadata = _metadata_bytes(fetch_data)
                flags = _flags_from_fetch(metadata)
                size_match = _SIZE_RE.search(metadata)
                size_bytes = int(size_match.group(1)) if size_match else None
                header_msg = BytesParser(policy=policy.default).parsebytes(header_bytes)
                row = _message_to_dict(
                    header_msg, uid=uid, mailbox=mailbox, include_body=False, truncated=False
                )
                self.store.upsert_header(
                    account_id=account_id,
                    mailbox=mailbox,
                    uid=uid,
                    uidvalidity=uidvalidity,
                    row=row,
                    flags=flags,
                    size_bytes=size_bytes,
                    header_bytes=header_bytes,
                )
                fetched_headers += 1

            # FLAGS are deliberately refreshed without refetching headers/bodies. This keeps Seen
            # state current while preserving the incremental, metadata-only periodic contract.
            flags_by_uid: dict[int, list[str]] = {}
            if live_uids:
                typ, flags_data = conn.uid("FETCH", "1:*", "(UID FLAGS)")
                if typ == "OK":
                    for item in flags_data or []:
                        raw = item[0] if isinstance(item, tuple) else item
                        if not isinstance(raw, bytes):
                            continue
                        uid_match = _UID_RE.search(raw)
                        if uid_match:
                            flags_by_uid[int(uid_match.group(1))] = _flags_from_fetch(raw)
            self.store.update_flags(account_id, mailbox, flags_by_uid)

        highest_uid = max(live_uids, default=0)
        sync_kind = "uidvalidity-reset+incremental" if reset else "incremental"
        result = self.store.finish_sync(
            account_id,
            mailbox,
            uidvalidity=uidvalidity,
            highest_uid=highest_uid,
            highest_modseq=highest_modseq,
            message_count=len(live_uids),
            sync_kind=sync_kind,
        )
        result.update(
            {
                "ok": True,
                "role": role,
                "new_headers": fetched_headers,
                "removed": deleted,
                "flags_refreshed": len(flags_by_uid),
                "full_body_fetches": 0,
                "send_capability": False,
                "coalesced": True,
            }
        )
        return result

    def sync_account(self, client: Any, *, account_id: str) -> dict[str, Any]:
        catalog = client.mailbox_catalog()
        stamp = _now()
        self.store.replace_mailboxes(account_id, catalog, synced_at=stamp)
        results: list[dict[str, Any]] = []
        for row in catalog:
            mailbox = str(row.get("name") or "").strip()
            if not mailbox:
                continue
            try:
                results.append(
                    self.sync_mailbox(
                        client,
                        account_id=account_id,
                        mailbox=mailbox,
                        role=str(row.get("role") or "other"),
                    )
                )
            except Exception as exc:
                results.append({"ok": False, "mailbox": mailbox, "error": f"{type(exc).__name__}: {exc}"})
        return {"ok": all(row.get("ok") for row in results) if results else True, "mailboxes": results, "synced_at": stamp}

    def ensure_body(self, client: Any, *, account_id: str, mailbox: str, uid: str) -> dict[str, Any]:
        cached = self.store.get_message(account_id, mailbox, uid, include_body=True)
        if cached and cached.get("body_cached"):
            return cached
        key = (account_id, f"{mailbox}\x1fbody:{uid}")
        with self._guard:
            state = self._inflight.get(key)
            if state is None:
                state = _InflightSync(threading.Event())
                self._inflight[key] = state
                leader = True
            else:
                leader = False
        if not leader:
            state.event.wait(timeout=60.0)
            if state.error:
                raise state.error
            return dict(state.result or {})
        try:
            with client._imap() as conn:
                client._select(conn, mailbox, readonly=True)
                raw, truncated = client._fetch_raw(conn, str(uid))
            result = self.store.store_body(account_id, mailbox, str(uid), raw, truncated=truncated)
            state.result = dict(result)
            return result
        except BaseException as exc:
            state.error = exc
            raise
        finally:
            with self._guard:
                self._inflight.pop(key, None)
                state.event.set()


class MailboxSyncService:
    """Five-minute read-only synchronizer, intentionally independent of SchedulerEngine."""

    def __init__(
        self,
        synchronizer: MailboxCacheSynchronizer,
        *,
        list_accounts: Callable[[], list[dict[str, Any]]],
        client_factory: Callable[[str], Any],
        interval_seconds: float = _SYNC_INTERVAL_SECONDS,
    ) -> None:
        self.synchronizer = synchronizer
        self.list_accounts = list_accounts
        self.client_factory = client_factory
        self.interval_seconds = max(60.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="postmaster-mailbox-cache-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Do not perform network I/O during import/startup. The first automatic pass is exactly
        # one cadence later; the WebGUI Refresh action can request an immediate pass explicitly.
        while not self._stop.wait(self.interval_seconds):
            self.sync_all()

    def sync_all(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for row in self.list_accounts():
            if not isinstance(row, dict) or not bool(row.get("enabled", True)):
                continue
            account_id = str(row.get("id") or "").strip()
            if not account_id:
                continue
            try:
                results.append(self.synchronizer.sync_account(self.client_factory(account_id), account_id=account_id))
            except Exception as exc:
                results.append({"ok": False, "account_id": account_id, "error": f"{type(exc).__name__}: {exc}"})
        return results


__all__ = [
    "MailboxCacheStore",
    "MailboxCacheSynchronizer",
    "MailboxSyncService",
    "_SYNC_INTERVAL_SECONDS",
]
