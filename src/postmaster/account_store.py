from __future__ import annotations

import os
import re
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from .mail_bridge import Settings, MailBridgeError


class AccountStoreError(RuntimeError):
    pass


_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-.")
    return (value or "mail")[:64]


class MailAccountStore:
    """Persistent encrypted multi-account IMAP/SMTP configuration."""

    def __init__(
        self,
        db_path: str | None = None,
        key_path: str | None = None,
    ):
        self.db_path = db_path or os.getenv("MAIL_ACCOUNTS_DB_PATH", "/data/mail_accounts.db")
        self.key_path = key_path or os.getenv("MAIL_ACCOUNTS_KEY_PATH", "/data/mail_accounts.key")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.key_path).parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._load_or_create_key())
        self._init_db()
        self._migrate_legacy_env()

    def _load_or_create_key(self) -> bytes:
        path = Path(self.key_path)
        if path.exists():
            key = path.read_bytes().strip()
            try:
                Fernet(key)
                return key
            except Exception as exc:
                raise AccountStoreError(f"Invalid account encryption key at {path}") from exc
        key = Fernet.generate_key()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(key + b"\n")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return key

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mail_accounts (
                    id TEXT PRIMARY KEY COLLATE NOCASE,
                    label TEXT NOT NULL DEFAULT '',
                    email_address TEXT NOT NULL,
                    imap_host TEXT NOT NULL,
                    imap_port INTEGER NOT NULL DEFAULT 993,
                    imap_security TEXT NOT NULL DEFAULT 'ssl',
                    imap_username TEXT NOT NULL DEFAULT '',
                    imap_password_enc TEXT NOT NULL,
                    smtp_host TEXT NOT NULL,
                    smtp_port INTEGER NOT NULL DEFAULT 465,
                    smtp_security TEXT NOT NULL DEFAULT 'ssl',
                    smtp_username TEXT NOT NULL DEFAULT '',
                    smtp_password_enc TEXT NOT NULL,
                    sent_mailbox TEXT NOT NULL DEFAULT 'Sent',
                    draft_mailbox TEXT NOT NULL DEFAULT 'Drafts',
                    inbox_mailbox TEXT NOT NULL DEFAULT 'INBOX',
                    junk_mailbox TEXT NOT NULL DEFAULT 'Junk',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    amp_enabled INTEGER NOT NULL DEFAULT 0,
                    amp_tested INTEGER NOT NULL DEFAULT 0,
                    amp_registered INTEGER NOT NULL DEFAULT 0,
                    amp_review_sent_at TEXT NOT NULL DEFAULT '',
                    amp_notes TEXT NOT NULL DEFAULT '',
                    tracking_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            # v8.2 idempotent migration for existing v8/v8.1 account databases.
            existing = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(mail_accounts)").fetchall()
            }
            migrations = {
                "amp_enabled": "INTEGER NOT NULL DEFAULT 0",
                "amp_tested": "INTEGER NOT NULL DEFAULT 0",
                "amp_registered": "INTEGER NOT NULL DEFAULT 0",
                "amp_review_sent_at": "TEXT NOT NULL DEFAULT ''",
                "amp_notes": "TEXT NOT NULL DEFAULT ''",
                "tracking_default": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, sql_type in migrations.items():
                if column not in existing:
                    conn.execute(f'ALTER TABLE mail_accounts ADD COLUMN "{column}" {sql_type}')

            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_mail_accounts_default "
                "ON mail_accounts(is_default) WHERE is_default=1"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mail_accounts_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO mail_accounts_meta(key,value) VALUES('schema_version','8.2')"
            )

    def _enc(self, value: str) -> str:
        return self._fernet.encrypt((value or "").encode("utf-8")).decode("ascii")

    def _dec(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise AccountStoreError(
                "Could not decrypt stored mail credentials. The persistent encryption key "
                "does not match the database."
            ) from exc

    def _migrate_legacy_env(self) -> None:
        """One-time import of the v7 single-account environment when credentials are present."""
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM mail_accounts").fetchone()[0]
            marker = conn.execute(
                "SELECT value FROM mail_accounts_meta WHERE key='legacy_env_import'"
            ).fetchone()
            if count or marker:
                return

        email = os.getenv("MAIL_EMAIL", "").strip()
        password = os.getenv("MAIL_PASSWORD", "")
        if email and password:
            account_id = _slug(email.replace("@", "-"))
            self.save_account(
                account_id=account_id,
                label="Primary / migrated from v7",
                email_address=email,
                imap_host=os.getenv("IMAP_HOST", "imap.example.com").strip(),
                imap_port=int(os.getenv("IMAP_PORT", "993")),
                imap_security="ssl",
                imap_username=email,
                imap_password=password,
                smtp_host=os.getenv("SMTP_HOST", "smtp.example.com").strip(),
                smtp_port=int(os.getenv("SMTP_PORT", "465")),
                smtp_security="starttls" if _env_bool("SMTP_STARTTLS", False) else "ssl",
                smtp_username=email,
                smtp_password=password,
                sent_mailbox=os.getenv("SENT_MAILBOX", "INBOX.Sent").strip() or "INBOX.Sent",
                draft_mailbox=os.getenv("DRAFT_MAILBOX", "INBOX.Drafts").strip() or "INBOX.Drafts",
                inbox_mailbox=os.getenv("INBOX_MAILBOX", "INBOX").strip() or "INBOX",
                junk_mailbox=os.getenv("JUNK_MAILBOX", "INBOX.Junk").strip() or "INBOX.Junk",
                enabled=True,
                make_default=True,
            )
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mail_accounts_meta(key,value) VALUES('legacy_env_import',?)",
                (_now(),),
            )

    def _validate_security(self, value: str, kind: str) -> str:
        value = (value or "").strip().lower()
        allowed = {"ssl", "starttls", "plain"}
        if value not in allowed:
            raise AccountStoreError(f"{kind}_security must be one of {sorted(allowed)}")
        return value

    def list_accounts(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        q = "SELECT * FROM mail_accounts"
        if not include_disabled:
            q += " WHERE enabled=1"
        q += " ORDER BY is_default DESC, label COLLATE NOCASE, email_address COLLATE NOCASE"
        with self._connect() as conn:
            rows = conn.execute(q).fetchall()
        return [self._public_row(r) for r in rows]

    def _public_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        d.pop("imap_password_enc", None)
        d.pop("smtp_password_enc", None)
        d["enabled"] = bool(d.get("enabled"))
        d["is_default"] = bool(d.get("is_default"))
        d["amp_enabled"] = bool(d.get("amp_enabled"))
        d["amp_tested"] = bool(d.get("amp_tested"))
        d["amp_registered"] = bool(d.get("amp_registered"))
        d["tracking_default"] = bool(d.get("tracking_default"))
        d["capabilities"] = {
            "html_email": True,
            "attachments": True,
            "open_tracking": True,
            "amp_email": bool(d.get("amp_enabled")),
        }
        d["imap_password_saved"] = True
        d["smtp_password_saved"] = True
        return d

    def resolve_id(self, account_id: str | None = None) -> str:
        with self._connect() as conn:
            if account_id:
                row = conn.execute(
                    "SELECT id FROM mail_accounts WHERE id=? AND enabled=1",
                    (account_id.strip(),),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id FROM mail_accounts WHERE enabled=1 "
                    "ORDER BY is_default DESC, created_at ASC LIMIT 1"
                ).fetchone()
        if not row:
            if account_id:
                raise AccountStoreError(f"Unknown or disabled email account: {account_id}")
            raise AccountStoreError(
                "No enabled email accounts are configured. Add one from the WebGUI Accounts tab."
            )
        return str(row["id"])

    def get_account(self, account_id: str | None = None, *, include_secrets: bool = False) -> dict[str, Any]:
        resolved = self.resolve_id(account_id)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mail_accounts WHERE id=?", (resolved,)).fetchone()
        if not row:
            raise AccountStoreError(f"Unknown email account: {resolved}")
        if not include_secrets:
            return self._public_row(row)
        d = dict(row)
        d["imap_password"] = self._dec(d.pop("imap_password_enc"))
        d["smtp_password"] = self._dec(d.pop("smtp_password_enc"))
        d["enabled"] = bool(d["enabled"])
        d["is_default"] = bool(d["is_default"])
        d["amp_enabled"] = bool(d.get("amp_enabled"))
        d["amp_tested"] = bool(d.get("amp_tested"))
        d["amp_registered"] = bool(d.get("amp_registered"))
        d["tracking_default"] = bool(d.get("tracking_default"))
        return d

    def save_account(
        self,
        *,
        account_id: str,
        label: str,
        email_address: str,
        imap_host: str,
        imap_port: int,
        imap_security: str,
        imap_username: str,
        imap_password: str,
        smtp_host: str,
        smtp_port: int,
        smtp_security: str,
        smtp_username: str,
        smtp_password: str,
        sent_mailbox: str,
        draft_mailbox: str,
        inbox_mailbox: str,
        junk_mailbox: str,
        enabled: bool = True,
        make_default: bool = False,
        tracking_default: bool | None = None,
    ) -> dict[str, Any]:
        account_id = (account_id or "").strip() or _slug(email_address.replace("@", "-"))
        if not _ID_RE.fullmatch(account_id):
            raise AccountStoreError(
                "account_id must use letters, numbers, dot, underscore or dash (max 64 chars)"
            )
        email_address = email_address.strip()
        if "@" not in email_address:
            raise AccountStoreError("A valid email address is required")
        imap_host = imap_host.strip()
        smtp_host = smtp_host.strip()
        if not imap_host or not smtp_host:
            raise AccountStoreError("IMAP and SMTP hosts are required")
        imap_port = int(imap_port)
        smtp_port = int(smtp_port)
        if not 1 <= imap_port <= 65535 or not 1 <= smtp_port <= 65535:
            raise AccountStoreError("IMAP/SMTP ports must be between 1 and 65535")
        imap_security = self._validate_security(imap_security, "imap")
        smtp_security = self._validate_security(smtp_security, "smtp")
        imap_username = (imap_username or email_address).strip()
        smtp_username = (smtp_username or email_address).strip()

        with _LOCK, self._connect() as conn:
            old = conn.execute("SELECT * FROM mail_accounts WHERE id=?", (account_id,)).fetchone()
            if old:
                imap_enc = (
                    self._enc(imap_password)
                    if imap_password
                    else str(old["imap_password_enc"])
                )
                smtp_enc = (
                    self._enc(smtp_password)
                    if smtp_password
                    else str(old["smtp_password_enc"])
                )
                created = str(old["created_at"])
            else:
                if not imap_password:
                    raise AccountStoreError("IMAP password is required for a new account")
                if not smtp_password:
                    smtp_password = imap_password
                imap_enc = self._enc(imap_password)
                smtp_enc = self._enc(smtp_password)
                created = _now()

            if make_default:
                conn.execute("UPDATE mail_accounts SET is_default=0")
            elif not old:
                existing = conn.execute(
                    "SELECT COUNT(*) FROM mail_accounts WHERE enabled=1"
                ).fetchone()[0]
                make_default = existing == 0

            now = _now()
            conn.execute(
                """
                INSERT INTO mail_accounts(
                    id,label,email_address,
                    imap_host,imap_port,imap_security,imap_username,imap_password_enc,
                    smtp_host,smtp_port,smtp_security,smtp_username,smtp_password_enc,
                    sent_mailbox,draft_mailbox,inbox_mailbox,junk_mailbox,
                    enabled,is_default,tracking_default,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    label=excluded.label,
                    email_address=excluded.email_address,
                    imap_host=excluded.imap_host,
                    imap_port=excluded.imap_port,
                    imap_security=excluded.imap_security,
                    imap_username=excluded.imap_username,
                    imap_password_enc=excluded.imap_password_enc,
                    smtp_host=excluded.smtp_host,
                    smtp_port=excluded.smtp_port,
                    smtp_security=excluded.smtp_security,
                    smtp_username=excluded.smtp_username,
                    smtp_password_enc=excluded.smtp_password_enc,
                    sent_mailbox=excluded.sent_mailbox,
                    draft_mailbox=excluded.draft_mailbox,
                    inbox_mailbox=excluded.inbox_mailbox,
                    junk_mailbox=excluded.junk_mailbox,
                    enabled=excluded.enabled,
                    is_default=CASE WHEN excluded.is_default=1 THEN 1 ELSE mail_accounts.is_default END,
                    tracking_default=excluded.tracking_default,
                    updated_at=excluded.updated_at
                """,
                (
                    account_id, label.strip(), email_address,
                    imap_host, imap_port, imap_security, imap_username, imap_enc,
                    smtp_host, smtp_port, smtp_security, smtp_username, smtp_enc,
                    sent_mailbox.strip() or "Sent",
                    draft_mailbox.strip() or "Drafts",
                    inbox_mailbox.strip() or "INBOX",
                    junk_mailbox.strip() or "Junk",
                    1 if enabled else 0,
                    1 if make_default else (int(old["is_default"]) if old else 0),
                    (
                        1 if tracking_default else 0
                        if tracking_default is not None
                        else (int(old["tracking_default"]) if old else 0)
                    ),
                    created, now,
                ),
            )
            if make_default:
                conn.execute(
                    "UPDATE mail_accounts SET is_default=CASE WHEN id=? THEN 1 ELSE 0 END",
                    (account_id,),
                )
        return self.get_account(account_id)

    def set_amp_state(
        self,
        account_id: str,
        *,
        enabled: bool | None = None,
        tested: bool | None = None,
        registered: bool | None = None,
        review_sent: bool = False,
        notes: str | None = None,
    ) -> dict[str, Any]:
        resolved = self.resolve_id(account_id)
        updates = []
        params: list[Any] = []
        for column, value in (
            ("amp_enabled", enabled),
            ("amp_tested", tested),
            ("amp_registered", registered),
        ):
            if value is not None:
                updates.append(f"{column}=?")
                params.append(1 if value else 0)
        if review_sent:
            updates.append("amp_review_sent_at=?")
            params.append(_now())
        if notes is not None:
            updates.append("amp_notes=?")
            params.append(notes.strip()[:4000])
        if not updates:
            return self.get_account(resolved)
        updates.append("updated_at=?")
        params.append(_now())
        params.append(resolved)
        with _LOCK, self._connect() as conn:
            conn.execute(
                f"UPDATE mail_accounts SET {', '.join(updates)} WHERE id=?",
                params,
            )
        return self.get_account(resolved)

    def amp_status(self, account_id: str | None = None) -> dict[str, Any]:
        row = self.get_account(account_id)
        return {
            "ok": True,
            "account_id": row["id"],
            "email_address": row["email_address"],
            "amp_enabled": bool(row.get("amp_enabled")),
            "amp_tested": bool(row.get("amp_tested")),
            "amp_registered": bool(row.get("amp_registered")),
            "amp_review_sent_at": row.get("amp_review_sent_at", ""),
            "amp_notes": row.get("amp_notes", ""),
            "mcp_capability_visible": bool(row.get("amp_enabled")),
            "procedure": [
                "Enable AMP for this sender account.",
                "In the destination Gmail test account: Settings > General > Dynamic email > Developer settings; allow this sender.",
                "Send a production-system AMP test with text/plain and text/html fallbacks.",
                "Verify SPF and aligned DKIM pass; DMARC is recommended; delivery must use TLS.",
                "Validate the delivered AMP MIME part and dynamic endpoints.",
                "Send one real production-ready AMP email from this exact sender to ampforemail.whitelisting@gmail.com.",
                "Submit Google's sender registration form. Registration is per sender email address.",
            ],
        }

    def set_default(self, account_id: str) -> dict[str, Any]:
        resolved = self.resolve_id(account_id)
        with _LOCK, self._connect() as conn:
            conn.execute("UPDATE mail_accounts SET is_default=0")
            conn.execute(
                "UPDATE mail_accounts SET is_default=1, updated_at=? WHERE id=?",
                (_now(), resolved),
            )
        return self.get_account(resolved)

    def delete_account(self, account_id: str) -> dict[str, Any]:
        account_id = (account_id or "").strip()
        with _LOCK, self._connect() as conn:
            row = conn.execute("SELECT * FROM mail_accounts WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise AccountStoreError(f"Unknown email account: {account_id}")
            was_default = bool(row["is_default"])
            conn.execute("DELETE FROM mail_accounts WHERE id=?", (account_id,))
            if was_default:
                replacement = conn.execute(
                    "SELECT id FROM mail_accounts WHERE enabled=1 ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if replacement:
                    conn.execute(
                        "UPDATE mail_accounts SET is_default=1 WHERE id=?",
                        (replacement["id"],),
                    )
        return {"ok": True, "deleted": account_id}

    def settings(self, account_id: str | None = None) -> Settings:
        row = self.get_account(account_id, include_secrets=True)
        allowlist = tuple(
            x.strip().lower()
            for x in os.getenv("SEND_RECIPIENT_ALLOWLIST", "").split(",")
            if x.strip()
        )
        return Settings(
            email_address=row["email_address"],
            email_password=row["imap_password"],
            imap_host=row["imap_host"],
            imap_port=int(row["imap_port"]),
            smtp_host=row["smtp_host"],
            smtp_port=int(row["smtp_port"]),
            smtp_starttls=row["smtp_security"] == "starttls",
            enable_send=_env_bool("ENABLE_SEND", False),
            max_message_bytes=int(os.getenv("MAX_MESSAGE_BYTES", "2000000")),
            search_candidate_limit=int(os.getenv("SEARCH_CANDIDATE_LIMIT", "500")),
            sent_mailbox=row["sent_mailbox"],
            save_sent_copy=_env_bool("SAVE_SENT_COPY", True),
            send_recipient_allowlist=allowlist,
            allow_previous_sent_recipients=_env_bool("ALLOW_PREVIOUS_SENT_RECIPIENTS", True),
            contact_history_limit=max(1, min(int(os.getenv("CONTACT_HISTORY_LIMIT", "5000")), 20000)),
            account_id=row["id"],
            imap_security=row["imap_security"],
            imap_username=row["imap_username"],
            imap_password=row["imap_password"],
            smtp_security=row["smtp_security"],
            smtp_username=row["smtp_username"],
            smtp_password=row["smtp_password"],
            draft_mailbox=row["draft_mailbox"],
            inbox_mailbox=row["inbox_mailbox"],
            junk_mailbox=row["junk_mailbox"],
            amp_enabled=bool(row.get("amp_enabled")),
            amp_registered=bool(row.get("amp_registered")),
            tracking_default=bool(row.get("tracking_default")),
        )
