"""Instance-global contact book; owner/project/account are organizational metadata."""
from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any

_ADDRESS = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


def _email(value: str) -> str:
    _, parsed = parseaddr(str(value or ""))
    addr = str(parsed or value or "").strip().lower()
    if len(addr) > 254 or not _ADDRESS.fullmatch(addr):
        raise ValueError("Invalid email address")
    return addr


class ContactBook:
    def __init__(self, db_path: str = "/data/contacts-v984.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(
                """CREATE TABLE IF NOT EXISTS contacts_v984 (
                    email TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    source_account_id TEXT NOT NULL DEFAULT '',
                    owner_id TEXT NOT NULL DEFAULT '',
                    project_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('saved','ignored')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_contacts_v984_account_status
                  ON contacts_v984(source_account_id,status);
                CREATE INDEX IF NOT EXISTS ix_contacts_v984_owner_project
                  ON contacts_v984(owner_id,project_id);"""
            )

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def list(self, *, owner_id: str | None = None, project_id: str | None = None,
             account_id: str | None = None, status: str | None = None,
             limit: int = 200, offset: int = 0) -> dict[str, Any]:
        conditions, params = [], []
        for column, val in (
            ("owner_id", owner_id), ("project_id", project_id),
            ("source_account_id", account_id), ("status", status),
        ):
            if val is not None and str(val).strip():
                conditions.append(f"{column}=?")
                params.append(str(val).strip())
        sql = "SELECT * FROM contacts_v984" + (" WHERE " + " AND ".join(conditions) if conditions else "")
        bounded = max(1, min(int(limit), 1000))
        with self._conn() as conn:
            rows = [
                dict(row) for row in conn.execute(
                    sql + " ORDER BY updated_at DESC,email LIMIT ? OFFSET ?",
                    (*params, bounded, max(0, int(offset))),
                )
            ]
        return {"ok": True, "contacts": rows, "count": len(rows), "access_scope": "instance_global"}

    def save(self, *, email: str, name: str = "", note: str = "",
             source_account_id: str = "", owner_id: str = "",
             project_id: str = "") -> dict[str, Any]:
        address = _email(email)
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO contacts_v984(
                   email,name,note,source_account_id,owner_id,project_id,status,created_at,updated_at
                   ) VALUES(?,?,?,?,? ,?,'saved',?,?)
                   ON CONFLICT(email) DO UPDATE SET
                   name=excluded.name,note=excluded.note,
                   source_account_id=excluded.source_account_id,
                   owner_id=excluded.owner_id,project_id=excluded.project_id,
                   status='saved',updated_at=excluded.updated_at""",
                (address, name[:200], note[:2000], source_account_id[:128],
                 owner_id[:128], project_id[:128], now, now),
            )
        return {"ok": True, "email": address, "status": "saved", "recipient_authorized": False}

    def set_ignored(self, *, email: str, ignored: bool = True,
                    source_account_id: str = "") -> dict[str, Any]:
        address = _email(email)
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            if ignored:
                conn.execute(
                    """INSERT INTO contacts_v984(email,source_account_id,status,created_at,updated_at)
                       VALUES(?,?,'ignored',?,?)
                       ON CONFLICT(email) DO UPDATE SET status='ignored',updated_at=excluded.updated_at""",
                    (address, source_account_id[:128], now, now),
                )
            else:
                conn.execute("DELETE FROM contacts_v984 WHERE email=? AND status='ignored'", (address,))
        return {"ok": True, "email": address, "status": "ignored" if ignored else "candidate",
                "recipient_authorized": False}

    def status_map(self) -> dict[str, str]:
        with self._conn() as conn:
            return {str(x["email"]): str(x["status"]) for x in conn.execute(
                "SELECT email,status FROM contacts_v984"
            )}


def incoming_candidates(
    base: Any, book: ContactBook, *,
    account_id: str | None = None, limit: int = 100, since_days: int = 365,
) -> dict[str, Any]:
    """Explicit user-triggered IMAP lookup; no automatic authorization or send."""
    accounts = base.account_store().list_accounts()
    selected = [x for x in accounts if not account_id or x.get("id") == account_id]
    found: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    bounded = max(1, min(int(limit), 200))
    statuses = book.status_map()
    for account in selected:
        aid = str(account.get("id") or "")
        if not aid:
            continue
        try:
            messages = base.mail_client(aid).search_emails(
                mailbox="INBOX", since_days=max(1, min(int(since_days), 3650)),
                limit=min(100, bounded),
            )
        except Exception as exc:
            failures.append({"account_id": aid, "error": type(exc).__name__})
            continue
        if not isinstance(messages, list):
            continue
        for message in messages:
            raw = str(message.get("from") or "")
            name, address = parseaddr(raw)
            try:
                email = _email(address)
            except ValueError:
                continue
            if email not in found:
                found[email] = {
                    "email": email, "name": name[:200],
                    "account_id": aid, "last_message_at": message.get("date"),
                    "status": statuses.get(email, "candidate"),
                }
            if len(found) >= bounded:
                break
        if len(found) >= bounded:
            break
    return {
        "ok": True, "contacts": list(found.values()),
        "count": len(found), "mailbox_errors": failures,
        "authorization_effect": "none",
    }
