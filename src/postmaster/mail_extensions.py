from __future__ import annotations

import base64
import hashlib
import imaplib
import io
import mimetypes
import os
import re
import sqlite3
import threading
import zipfile
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from html import escape
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from .mail_bridge import MailClient, MailBridgeError
from .email_analytics import analytics_store, validate_amp_document


_URL_RE = re.compile(r"(https?://[^\s<]+)")
_POLICY_MIGRATION_LOCK = threading.Lock()


def _plain_to_html(text: str) -> str:
    safe = escape(text or "")
    linked = _URL_RE.sub(r'<a href="\1">\1</a>', safe)
    linked = linked.replace("\n", "<br>\n")
    return (
        '<!doctype html><html><body>'
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5">'
        f"{linked}</div></body></html>"
    )


def _strip_html_local(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class EnhancedMailClient(MailClient):
    """Generic IMAP/SMTP mail client with HTML, drafts, attachments, recipient policy and mailbox writes."""

    @property
    def draft_mailbox(self) -> str:
        value = getattr(self.settings, "draft_mailbox", "")
        return (value or os.getenv("DRAFT_MAILBOX", "INBOX.Drafts")).strip() or "INBOX.Drafts"

    @property
    def inbox_mailbox(self) -> str:
        value = getattr(self.settings, "inbox_mailbox", "")
        return (value or os.getenv("INBOX_MAILBOX", "INBOX")).strip() or "INBOX"

    @property
    def junk_mailbox(self) -> str:
        value = getattr(self.settings, "junk_mailbox", "")
        return (value or os.getenv("JUNK_MAILBOX", "INBOX.Junk")).strip() or "INBOX.Junk"

    @property
    def policy_db_path(self) -> str:
        return os.getenv("RECIPIENT_POLICY_DB_PATH", "/data/mail_policy.db").strip() or "/data/mail_policy.db"

    @property
    def max_attachment_bytes(self) -> int:
        return max(1_000_000, min(int(os.getenv("MAX_ATTACHMENT_BYTES", "20000000")), 50_000_000))

    @property
    def max_attachment_message_bytes(self) -> int:
        return max(
            self.max_attachment_bytes,
            min(int(os.getenv("MAX_ATTACHMENT_MESSAGE_BYTES", "30000000")), 100_000_000),
        )

    @property
    def attachment_read_max_chars(self) -> int:
        return max(1_000, min(int(os.getenv("ATTACHMENT_READ_MAX_CHARS", "60000")), 500_000))

    @staticmethod
    def _qident(name: str) -> str:
        return '"' + str(name).replace('"', '""') + '"'

    @classmethod
    def _policy_columns(cls, conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
        qtable = cls._qident(table)
        return {str(r["name"]): r for r in conn.execute(f"PRAGMA table_info({qtable})").fetchall()}

    @staticmethod
    def _pick_column(columns: dict[str, sqlite3.Row], *names: str) -> str | None:
        for name in names:
            if name in columns:
                return name
        return None

    def _migrate_policy_schema(self, conn: sqlite3.Connection) -> None:
        """Idempotently migrate old recipient-policy DBs to the v7.2 canonical schema."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        current = conn.execute(
            "SELECT value FROM policy_meta WHERE key='schema_version'"
        ).fetchone()

        # Do not trust only the version marker: verify the two critical key columns too.
        recipient_cols = self._policy_columns(conn, "authorized_recipients")
        domain_cols = self._policy_columns(conn, "authorized_domains")
        schema_already_ok = (
            current is not None
            and str(current["value"]) == "7.2"
            and {"email", "note", "created_at"}.issubset(recipient_cols)
            and {"domain", "note", "created_at"}.issubset(domain_cols)
            and int(recipient_cols["email"]["pk"] or 0) > 0
            and int(domain_cols["domain"]["pk"] or 0) > 0
        )
        if schema_already_ok:
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            tables = {
                str(r["name"])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

            # --- Exact recipients ---
            conn.execute("DROP TABLE IF EXISTS __v72_authorized_recipients")
            conn.execute(
                """
                CREATE TABLE __v72_authorized_recipients (
                    email TEXT PRIMARY KEY COLLATE NOCASE,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            if "authorized_recipients" in tables:
                cols = self._policy_columns(conn, "authorized_recipients")
                email_col = self._pick_column(
                    cols, "email", "email_address", "address", "recipient"
                )
                if email_col is None:
                    raise sqlite3.OperationalError(
                        "authorized_recipients has no recognizable email column: "
                        + ", ".join(cols)
                    )
                note_col = self._pick_column(cols, "note", "notes", "description")
                created_col = self._pick_column(
                    cols, "created_at", "created", "added_at", "timestamp"
                )
                qe = self._qident(email_col)
                note_expr = (
                    f"COALESCE(CAST({self._qident(note_col)} AS TEXT), '')"
                    if note_col else "''"
                )
                created_expr = (
                    f"COALESCE(CAST({self._qident(created_col)} AS TEXT), CURRENT_TIMESTAMP)"
                    if created_col else "CURRENT_TIMESTAMP"
                )
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO __v72_authorized_recipients(email, note, created_at)
                    SELECT LOWER(TRIM(CAST({qe} AS TEXT))), {note_expr}, {created_expr}
                    FROM authorized_recipients
                    WHERE {qe} IS NOT NULL AND TRIM(CAST({qe} AS TEXT)) <> ''
                    """
                )
                conn.execute("DROP TABLE authorized_recipients")
            conn.execute(
                "ALTER TABLE __v72_authorized_recipients RENAME TO authorized_recipients"
            )

            # --- Authorized domains ---
            conn.execute("DROP TABLE IF EXISTS __v72_authorized_domains")
            conn.execute(
                """
                CREATE TABLE __v72_authorized_domains (
                    domain TEXT PRIMARY KEY COLLATE NOCASE,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            if "authorized_domains" in tables:
                cols = self._policy_columns(conn, "authorized_domains")
                domain_col = self._pick_column(cols, "domain", "domain_name", "hostname")
                if domain_col is None:
                    raise sqlite3.OperationalError(
                        "authorized_domains has no recognizable domain column: "
                        + ", ".join(cols)
                    )
                note_col = self._pick_column(cols, "note", "notes", "description")
                created_col = self._pick_column(
                    cols, "created_at", "created", "added_at", "timestamp"
                )
                qd = self._qident(domain_col)
                note_expr = (
                    f"COALESCE(CAST({self._qident(note_col)} AS TEXT), '')"
                    if note_col else "''"
                )
                created_expr = (
                    f"COALESCE(CAST({self._qident(created_col)} AS TEXT), CURRENT_TIMESTAMP)"
                    if created_col else "CURRENT_TIMESTAMP"
                )
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO __v72_authorized_domains(domain, note, created_at)
                    SELECT LOWER(RTRIM(TRIM(CAST({qd} AS TEXT)), '.')), {note_expr}, {created_expr}
                    FROM authorized_domains
                    WHERE {qd} IS NOT NULL AND TRIM(CAST({qd} AS TEXT)) <> ''
                    """
                )
                conn.execute("DROP TABLE authorized_domains")
            conn.execute(
                "ALTER TABLE __v72_authorized_domains RENAME TO authorized_domains"
            )

            # Verify the actual UPSERT syntax used by authorize_recipient/domain.
            conn.execute("SAVEPOINT v72_probe")
            try:
                probe_time = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO authorized_recipients(email, note, created_at)
                    VALUES ('__v72_probe__@invalid.invalid', 'probe', ?)
                    ON CONFLICT(email) DO UPDATE SET note=excluded.note
                    """,
                    (probe_time,),
                )
                conn.execute(
                    """
                    INSERT INTO authorized_recipients(email, note, created_at)
                    VALUES ('__V72_PROBE__@INVALID.INVALID', 'probe2', ?)
                    ON CONFLICT(email) DO UPDATE SET note=excluded.note
                    """,
                    (probe_time,),
                )
            finally:
                conn.execute("ROLLBACK TO v72_probe")
                conn.execute("RELEASE v72_probe")

            conn.execute(
                "INSERT OR REPLACE INTO policy_meta(key, value) VALUES ('schema_version', '7.2')"
            )
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if str(integrity).lower() != "ok":
                raise sqlite3.DatabaseError(f"policy DB integrity_check failed: {integrity}")
            conn.commit()
            print("[mail-policy] v7.2 idempotent migration complete", flush=True)
        except Exception:
            conn.rollback()
            raise

    def _policy_connect(self) -> sqlite3.Connection:
        path = Path(self.policy_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")

        if not getattr(self, "_policy_schema_v72_ready", False):
            with _POLICY_MIGRATION_LOCK:
                if not getattr(self, "_policy_schema_v72_ready", False):
                    self._migrate_policy_schema(conn)
                    self._policy_schema_v72_ready = True

        # These CREATEs remain as a defensive no-op for brand-new/healthy DBs.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS authorized_recipients (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS authorized_domains (
                domain TEXT PRIMARY KEY COLLATE NOCASE,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        seeded = conn.execute(
            "SELECT value FROM policy_meta WHERE key='env_domains_seeded'"
        ).fetchone()
        if not seeded:
            now = datetime.now(timezone.utc).isoformat()
            for domain in self.settings.send_recipient_allowlist:
                domain = str(domain).strip().lower().lstrip("@")
                if domain:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO authorized_domains(domain, note, created_at)
                        VALUES (?, 'Seeded from SEND_RECIPIENT_ALLOWLIST', ?)
                        """,
                        (domain, now),
                    )
            conn.execute(
                "INSERT OR REPLACE INTO policy_meta(key, value) VALUES ('env_domains_seeded', '1')"
            )
            conn.commit()
        return conn

    def _normalize_domain(self, domain: str) -> str:
        value = (domain or "").strip().lower().lstrip("@").rstrip(".")
        if not value or "." not in value or " " in value or "/" in value:
            raise MailBridgeError(f"Invalid domain: {domain}")
        if not re.fullmatch(r"[a-z0-9.-]+", value):
            raise MailBridgeError(f"Invalid domain: {domain}")
        return value

    def _authorized_domains(self) -> set[str]:
        with self._policy_connect() as conn:
            rows = conn.execute(
                "SELECT domain FROM authorized_domains ORDER BY domain"
            ).fetchall()
        return {str(r["domain"]).strip().lower() for r in rows}

    def list_authorized_domains(self) -> dict:
        with self._policy_connect() as conn:
            rows = conn.execute(
                """
                SELECT domain, note, created_at
                FROM authorized_domains
                ORDER BY domain
                """
            ).fetchall()
        return {
            "ok": True,
            "domains": [dict(r) for r in rows],
            "count": len(rows),
            "mode": "managed_persistent",
        }

    def authorize_domain(self, domain: str, note: str = "") -> dict:
        value = self._normalize_domain(domain)
        with self._policy_connect() as conn:
            conn.execute(
                """
                INSERT INTO authorized_domains(domain, note, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET note=excluded.note
                """,
                (value, note.strip(), datetime.now(timezone.utc).isoformat()),
            )
        return {
            "ok": True,
            "authorized_domain": value,
            "scope": "domain_and_subdomains",
            "note": note.strip(),
        }

    def revoke_domain(self, domain: str) -> dict:
        value = self._normalize_domain(domain)
        with self._policy_connect() as conn:
            cur = conn.execute(
                "DELETE FROM authorized_domains WHERE domain=?",
                (value,),
            )
        return {
            "ok": True,
            "revoked_domain": value,
            "removed": cur.rowcount > 0,
        }

    def _explicit_authorized_addresses(self) -> set[str]:
        with self._policy_connect() as conn:
            rows = conn.execute("SELECT email FROM authorized_recipients").fetchall()
        return {str(r["email"]).strip().lower() for r in rows}

    def authorize_recipient(self, email_address: str, note: str = "") -> dict:
        _, addr = parseaddr(email_address)
        if not addr or "@" not in addr:
            raise MailBridgeError(f"Invalid recipient address: {email_address}")
        addr = addr.strip().lower()
        with self._policy_connect() as conn:
            conn.execute(
                """
                INSERT INTO authorized_recipients(email, note, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET note=excluded.note
                """,
                (addr, note.strip(), datetime.now(timezone.utc).isoformat()),
            )
        return {"ok": True, "authorized": addr, "scope": "exact_address", "note": note.strip()}

    def revoke_recipient(self, email_address: str) -> dict:
        _, addr = parseaddr(email_address)
        if not addr or "@" not in addr:
            raise MailBridgeError(f"Invalid recipient address: {email_address}")
        addr = addr.strip().lower()
        with self._policy_connect() as conn:
            cur = conn.execute("DELETE FROM authorized_recipients WHERE email=?", (addr,))
        return {"ok": True, "revoked": addr, "removed": cur.rowcount > 0}

    def list_authorized_recipients(self) -> dict:
        with self._policy_connect() as conn:
            rows = conn.execute(
                "SELECT email, note, created_at FROM authorized_recipients ORDER BY email"
            ).fetchall()
        return {"ok": True, "recipients": [dict(r) for r in rows], "count": len(rows)}

    def recipient_authorization_status(self, recipients: Iterable[str]) -> dict:
        explicit = self._explicit_authorized_addresses()
        authorized_domains = self._authorized_domains()
        historical = None
        out = []
        for value in recipients:
            _, addr = parseaddr(value)
            if not addr or "@" not in addr:
                raise MailBridgeError(f"Invalid recipient address: {value}")
            addr = addr.strip()
            lc = addr.lower()
            domain = lc.rsplit("@", 1)[1]
            by_domain = any(
                domain == allowed or domain.endswith("." + allowed)
                for allowed in authorized_domains
            )
            by_exact = lc in explicit
            by_history = False
            if self.settings.allow_previous_sent_recipients:
                if historical is None:
                    historical = self._historical_sent_addresses()
                by_history = lc in historical
            out.append(
                {
                    "address": addr,
                    "authorized_for_automated_send": by_domain or by_exact or by_history,
                    "by_static_domain": by_domain,
                    "by_exact_authorization": by_exact,
                    "by_previous_sent": by_history,
                }
            )
        return {"ok": True, "results": out}

    def _validate_recipients(self, recipients: Iterable[str]) -> list[str]:
        cleaned: list[str] = []
        historical = None
        explicit = self._explicit_authorized_addresses()
        authorized_domains = self._authorized_domains()
        for value in recipients:
            _, addr = parseaddr(value)
            if not addr or "@" not in addr:
                raise MailBridgeError(f"Invalid recipient address: {value}")
            addr = addr.strip()
            lc = addr.lower()
            domain = lc.rsplit("@", 1)[1]
            by_domain = any(
                domain == allowed or domain.endswith("." + allowed)
                for allowed in authorized_domains
            )
            by_exact = lc in explicit
            by_history = False
            if not by_domain and not by_exact and self.settings.allow_previous_sent_recipients:
                if historical is None:
                    historical = self._historical_sent_addresses()
                by_history = lc in historical
            if (
                authorized_domains
                or self.settings.allow_previous_sent_recipients
                or explicit
            ) and not (by_domain or by_exact or by_history):
                raise MailBridgeError(
                    f"Recipient {addr} is not authorized for automated sending. "
                    "Authorize the exact address, add its domain, use a previous recipient, "
                    "or create a draft for manual review/send."
                )
            cleaned.append(addr)
        if not cleaned:
            raise MailBridgeError("At least one recipient is required")
        return list(dict.fromkeys(cleaned))

    def _clean_unlisted_recipients(self, recipients: Iterable[str]) -> list[str]:
        cleaned = []
        for value in recipients:
            _, addr = parseaddr(value)
            if not addr or "@" not in addr:
                raise MailBridgeError(f"Invalid recipient address: {value}")
            cleaned.append(addr.strip())
        return list(dict.fromkeys(cleaned))

    def _fetch_message_for_attachments(self, mailbox: str, uid: str) -> Message:
        with self._imap() as conn:
            self._select(conn, mailbox, readonly=True)
            typ, size_data = conn.uid("FETCH", uid, "(RFC822.SIZE)")
            if typ != "OK" or not size_data:
                raise MailBridgeError(f"Could not fetch size for UID {uid}")
            size = None
            for item in size_data:
                if isinstance(item, bytes):
                    match = re.search(rb"RFC822\.SIZE\s+(\d+)", item)
                    if match:
                        size = int(match.group(1))
                        break
            if size is not None and size > self.max_attachment_message_bytes:
                raise MailBridgeError(
                    f"Message is too large to fetch for attachments ({size} bytes; "
                    f"limit {self.max_attachment_message_bytes})"
                )
            typ, raw_data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not raw_data:
                raise MailBridgeError(f"Could not fetch UID {uid}")
            raw = None
            for item in raw_data:
                if isinstance(item, tuple) and isinstance(item[1], bytes):
                    raw = item[1]
                    break
            if raw is None:
                raise MailBridgeError(f"Could not parse UID {uid}")
        return BytesParser(policy=policy.default).parsebytes(raw)

    def _attachment_parts(self, msg: Message) -> list[tuple[int, Message]]:
        out = []
        idx = 0
        for part in msg.walk():
            filename = part.get_filename()
            disposition = part.get_content_disposition()
            if filename or disposition == "attachment":
                out.append((idx, part))
                idx += 1
        return out

    def list_email_attachments(self, mailbox: str, uid: str) -> dict:
        msg = self._fetch_message_for_attachments(mailbox, uid)
        items = []
        for idx, part in self._attachment_parts(msg):
            payload = part.get_payload(decode=True) or b""
            items.append(
                {
                    "index": idx,
                    "filename": part.get_filename() or f"attachment-{idx}",
                    "content_type": part.get_content_type(),
                    "disposition": part.get_content_disposition() or "",
                    "size": len(payload),
                }
            )
        return {
            "ok": True,
            "mailbox": mailbox,
            "uid": uid,
            "message_id": msg.get("Message-ID", ""),
            "subject": str(msg.get("Subject", "")),
            "attachments": items,
            "count": len(items),
        }

    def _find_attachment(
        self,
        mailbox: str,
        uid: str,
        *,
        filename: str | None = None,
        index: int | None = None,
    ) -> tuple[Message, int, Message, bytes]:
        msg = self._fetch_message_for_attachments(mailbox, uid)
        parts = self._attachment_parts(msg)
        target = None
        if index is not None:
            for idx, part in parts:
                if idx == index:
                    target = (idx, part)
                    break
        elif filename:
            wanted = filename.strip().casefold()
            for idx, part in parts:
                if (part.get_filename() or "").casefold() == wanted:
                    target = (idx, part)
                    break
        else:
            if len(parts) == 1:
                target = parts[0]
            else:
                raise MailBridgeError("Specify filename or index when the message has multiple attachments")
        if target is None:
            raise MailBridgeError("Attachment not found")
        idx, part = target
        blob = part.get_payload(decode=True) or b""
        if len(blob) > self.max_attachment_bytes:
            raise MailBridgeError(
                f"Attachment is too large ({len(blob)} bytes; limit {self.max_attachment_bytes})"
            )
        return msg, idx, part, blob

    def get_email_attachment(
        self,
        mailbox: str,
        uid: str,
        filename: str | None = None,
        index: int | None = None,
        include_base64: bool = True,
    ) -> dict:
        msg, idx, part, blob = self._find_attachment(
            mailbox, uid, filename=filename, index=index
        )
        result = {
            "ok": True,
            "mailbox": mailbox,
            "uid": uid,
            "index": idx,
            "filename": part.get_filename() or f"attachment-{idx}",
            "content_type": part.get_content_type(),
            "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "message_id": msg.get("Message-ID", ""),
        }
        if include_base64:
            result["content_base64"] = base64.b64encode(blob).decode("ascii")
        return result

    def read_email_attachment(
        self,
        mailbox: str,
        uid: str,
        filename: str | None = None,
        index: int | None = None,
        max_chars: int | None = None,
    ) -> dict:
        _, idx, part, blob = self._find_attachment(
            mailbox, uid, filename=filename, index=index
        )
        ctype = part.get_content_type().lower()
        fname = part.get_filename() or f"attachment-{idx}"
        limit = self.attachment_read_max_chars if max_chars is None else max(
            1000, min(int(max_chars), 500000)
        )
        text = ""
        extractor = None

        if ctype.startswith("text/") or ctype in {
            "application/json", "application/xml", "application/javascript",
            "text/csv",
        }:
            charset = part.get_content_charset() or "utf-8"
            text = blob.decode(charset, errors="replace")
            if ctype == "text/html":
                text = _strip_html_local(text)
            extractor = "text"
        elif ctype == "application/pdf" or fname.lower().endswith(".pdf"):
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(blob))
                text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
                extractor = "pypdf"
            except Exception as exc:
                raise MailBridgeError(f"PDF text extraction failed: {type(exc).__name__}") from exc
        elif (
            ctype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or fname.lower().endswith(".docx")
        ):
            try:
                with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                    xml = zf.read("word/document.xml")
                root = ET.fromstring(xml)
                chunks = []
                for elem in root.iter():
                    if elem.tag.endswith("}t") and elem.text:
                        chunks.append(elem.text)
                    elif elem.tag.endswith("}p"):
                        chunks.append("\n")
                text = "".join(chunks)
                extractor = "docx_xml"
            except Exception as exc:
                raise MailBridgeError(f"DOCX text extraction failed: {type(exc).__name__}") from exc
        else:
            return {
                "ok": True,
                "mailbox": mailbox,
                "uid": uid,
                "index": idx,
                "filename": fname,
                "content_type": ctype,
                "size": len(blob),
                "extractable": False,
                "message": "Binary attachment is downloadable with get_email_attachment but has no text extractor in this build.",
            }

        truncated = len(text) > limit
        return {
            "ok": True,
            "mailbox": mailbox,
            "uid": uid,
            "index": idx,
            "filename": fname,
            "content_type": ctype,
            "size": len(blob),
            "extractable": True,
            "extractor": extractor,
            "text": text[:limit],
            "text_truncated": truncated,
            "max_chars": limit,
        }

    def _decode_attachment_specs(self, attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not attachments:
            return []
        out = []
        total = 0
        for spec in attachments:
            if not isinstance(spec, dict):
                raise MailBridgeError("Each attachment must be an object")

            if spec.get("content_base64"):
                filename = Path(str(spec.get("filename") or "attachment.bin")).name
                raw_b64 = str(spec["content_base64"]).strip()
                if raw_b64.startswith("data:") and ";base64," in raw_b64:
                    raw_b64 = raw_b64.split(";base64,", 1)[1]
                try:
                    blob = base64.b64decode(raw_b64, validate=True)
                except Exception as exc:
                    raise MailBridgeError(f"Invalid base64 for attachment {filename}") from exc
                ctype = str(spec.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
            elif spec.get("source_mailbox") and spec.get("source_uid"):
                source = self.get_email_attachment(
                    str(spec["source_mailbox"]),
                    str(spec["source_uid"]),
                    filename=spec.get("filename"),
                    index=spec.get("index"),
                    include_base64=False,
                )
                _, _, part, blob = self._find_attachment(
                    str(spec["source_mailbox"]),
                    str(spec["source_uid"]),
                    filename=spec.get("filename"),
                    index=spec.get("index"),
                )
                filename = Path(source["filename"]).name
                ctype = str(spec.get("content_type") or source["content_type"])
            else:
                raise MailBridgeError(
                    "Attachment requires content_base64 OR source_mailbox + source_uid "
                    "(with filename/index when needed)"
                )

            total += len(blob)
            if total > self.max_attachment_bytes:
                raise MailBridgeError(
                    f"Total attachment size exceeds limit {self.max_attachment_bytes} bytes"
                )
            if "/" not in ctype:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            out.append(
                {
                    "filename": filename,
                    "blob": blob,
                    "content_type": ctype,
                    "maintype": maintype,
                    "subtype": subtype,
                    "size": len(blob),
                }
            )
        return out

    def _build_message(
        self,
        *,
        to: list[str],
        subject: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        body_amp: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        allow_unlisted: bool = False,
        include_bcc_header: bool = False,
        in_reply_to: str = "",
        references: str = "",
    ) -> tuple[EmailMessage, list[str], list[dict[str, Any]]]:
        cleaner = self._clean_unlisted_recipients if allow_unlisted else self._validate_recipients
        to_clean = cleaner(to)
        cc_clean = cleaner(cc or []) if cc else []
        bcc_clean = cleaner(bcc or []) if bcc else []
        if not body and not body_html:
            raise MailBridgeError("body or body_html is required as a fallback")
        if body_amp and not bool(getattr(self.settings, "amp_enabled", False)):
            raise MailBridgeError(
                f"AMP is disabled for sender account {getattr(self.settings, 'account_id', '') or self.settings.email_address}"
            )
        if body_amp:
            check = validate_amp_document(body_amp)
            if not check.get("ok"):
                raise MailBridgeError("Invalid AMP email preflight: " + "; ".join(check.get("issues", [])))

        msg = EmailMessage()
        msg["From"] = self.settings.email_address
        msg["To"] = ", ".join(to_clean)
        if cc_clean:
            msg["Cc"] = ", ".join(cc_clean)
        if include_bcc_header and bcc_clean:
            msg["Bcc"] = ", ".join(bcc_clean)
        msg["Subject"] = subject.strip()
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        plain = body if body else _strip_html_local(body_html or "")
        html = body_html if body_html is not None else _plain_to_html(plain)
        msg.set_content(plain)
        # AMP for Email must appear before the HTML fallback inside multipart/alternative.
        if body_amp:
            msg.add_alternative(body_amp, subtype="x-amp-html")
        msg.add_alternative(html, subtype="html")

        decoded = self._decode_attachment_specs(attachments)
        for item in decoded:
            msg.add_attachment(
                item["blob"],
                maintype=item["maintype"],
                subtype=item["subtype"],
                filename=item["filename"],
            )

        recipients = list(dict.fromkeys(to_clean + cc_clean + bcc_clean))
        meta = [
            {
                "filename": x["filename"],
                "content_type": x["content_type"],
                "size": x["size"],
            }
            for x in decoded
        ]
        return msg, recipients, meta

    def _resolve_track_opens(self, track_opens: bool | None) -> bool:
        """None follows the per-account default; explicit True/False always wins."""
        if track_opens is None:
            return bool(getattr(self.settings, "tracking_default", False))
        return bool(track_opens)

    def _send_individualized(
        self,
        *,
        to: list[str],
        subject: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        body_amp: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        track_opens: bool,
        campaign_id: str | None = None,
        in_reply_to: str = "",
        references: str = "",
    ) -> dict:
        """
        Shared per-recipient delivery engine for tracked/AMP sends and replies.

        Each recipient gets a distinct SMTP envelope/token while every copy preserves the
        original visible To/Cc headers. Bcc recipients are validated and individualized but
        never exposed in message headers.
        """
        amp_used = bool(body_amp)

        # Validate recipients and authorization once, before mutating analytics state.
        to_clean = self._validate_recipients(to)
        cc_clean = self._validate_recipients(cc or []) if cc else []
        bcc_clean = self._validate_recipients(bcc or []) if bcc else []

        recipient_roles: list[tuple[str, str]] = []
        seen: set[str] = set()
        for role, addresses in (("to", to_clean), ("cc", cc_clean), ("bcc", bcc_clean)):
            for address in addresses:
                key = address.lower()
                if key not in seen:
                    seen.add(key)
                    recipient_roles.append((address, role))

        analytics = analytics_store()

        # Fail before create_campaign/create_delivery so a missing public URL cannot leave
        # orphan analytics rows behind.
        if track_opens or amp_used:
            analytics.validate_public_base_url()

        campaign = analytics.create_campaign(
            account_id=getattr(self.settings, "account_id", "") or self.settings.email_address,
            sender=self.settings.email_address,
            subject=subject.strip(),
            track_opens=track_opens,
            amp_used=amp_used,
            campaign_id=campaign_id,
        )

        base_html = body_html if body_html is not None else _plain_to_html(body)
        delivery_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        attachment_meta: list[dict[str, Any]] = []

        for recipient, role in recipient_roles:
            delivery = analytics.create_delivery(
                campaign_id=campaign["id"],
                account_id=getattr(self.settings, "account_id", "") or self.settings.email_address,
                recipient=recipient,
                recipient_role=role,
            )
            rendered_html, rendered_amp = analytics.render_for_recipient(
                body_html=base_html,
                body_amp=body_amp,
                delivery=delivery,
                track_opens=track_opens,
            )
            try:
                # Header semantics are identical on every individualized copy.
                # Bcc is intentionally NOT passed to _build_message, so it stays invisible.
                msg, _, meta = self._build_message(
                    to=to_clean,
                    cc=cc_clean,
                    subject=subject,
                    body=body,
                    body_html=rendered_html,
                    body_amp=rendered_amp,
                    attachments=attachments,
                    allow_unlisted=False,
                    in_reply_to=in_reply_to,
                    references=references,
                )
                # Only the current delivery recipient is used as SMTP envelope destination.
                result = self._send_message(msg, [recipient])
                analytics.mark_sent(delivery["id"], str(result.get("message_id", "")))
                attachment_meta = meta
                delivery_results.append({
                    "delivery_id": delivery["id"],
                    "recipient": recipient,
                    "role": role,
                    "message_id": result.get("message_id", ""),
                    "sent_copy_saved": result.get("sent_copy_saved", False),
                })
            except Exception as exc:
                errors.append({
                    "delivery_id": delivery["id"],
                    "recipient": recipient,
                    "role": role,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        return {
            "sent": bool(delivery_results) and not errors,
            "partial": bool(delivery_results) and bool(errors),
            "from": self.settings.email_address,
            "subject": subject.strip(),
            "campaign_id": campaign["id"],
            "individualized": True,
            "visible_recipient_headers_preserved": True,
            "tracked": bool(track_opens),
            "amp": amp_used,
            "amp_registered": bool(getattr(self.settings, "amp_registered", False)),
            "deliveries": delivery_results,
            "errors": errors,
            "attachments": attachment_meta,
            "tracking_note": (
                "Open events are image loads and may be affected by mail proxies, scanners, "
                "image blocking or prefetching."
            ) if track_opens else "",
        }

    def send_email(
        self,
        *,
        to: list[str],
        subject: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        body_amp: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        track_opens: bool | None = None,
        campaign_id: str | None = None,
    ) -> dict:
        """
        Send email.

        Normal plain/HTML sends remain a single SMTP message. Tracking or AMP uses the
        shared individualized delivery engine so recipient-specific tokens stay separate.
        """
        amp_used = bool(body_amp)
        if amp_used and not bool(getattr(self.settings, "amp_enabled", False)):
            raise MailBridgeError(
                f"AMP is not enabled for account "
                f"{getattr(self.settings, 'account_id', '') or self.settings.email_address}"
            )
        if amp_used:
            check = validate_amp_document(body_amp or "")
            if not check.get("ok"):
                raise MailBridgeError(
                    "Invalid AMP email preflight: " + "; ".join(check.get("issues", []))
                )

        track_opens = self._resolve_track_opens(track_opens)

        # Keep the ordinary non-tracked path as one SMTP group delivery.
        if not track_opens and not amp_used:
            msg, recipients, meta = self._build_message(
                to=to, subject=subject, body=body, cc=cc, bcc=bcc,
                body_html=body_html, attachments=attachments, allow_unlisted=False
            )
            result = self._send_message(msg, recipients)
            result.update({
                "html": True,
                "amp": False,
                "tracked": False,
                "individualized": False,
                "visible_recipient_headers_preserved": True,
                "attachments": meta,
            })
            return result

        return self._send_individualized(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            body_html=body_html,
            body_amp=body_amp,
            attachments=attachments,
            track_opens=track_opens,
            campaign_id=campaign_id,
        )

    def reply_email(
        self,
        *,
        mailbox: str,
        uid: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        track_opens: bool | None = None,
        campaign_id: str | None = None,
    ) -> dict:
        """
        Send a threaded reply.

        track_opens=None follows the account tracking_default. Explicit True/False overrides
        the account default for this reply. Tracked replies use per-recipient envelopes while
        preserving visible To/Cc and In-Reply-To/References on every copy.
        """
        original = self.get_email(mailbox, uid)
        from_addresses = original.get("from_addresses") or []
        if not from_addresses:
            raise MailBridgeError("Original email has no valid From address")

        subject = original.get("subject", "") or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        message_id = original.get("message_id", "") or ""
        refs = original.get("references", "").strip()
        references = (refs + " " + message_id).strip() if message_id else refs
        reply_to = [from_addresses[0]]
        track_opens = self._resolve_track_opens(track_opens)

        # Preserve historical behavior when tracking is disabled: one normal group message.
        if not track_opens:
            msg, recipients, meta = self._build_message(
                to=reply_to,
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
                body_html=body_html,
                attachments=attachments,
                allow_unlisted=False,
                in_reply_to=message_id,
                references=references,
            )
            result = self._send_message(msg, recipients)
            result.update({
                "html": True,
                "amp": False,
                "tracked": False,
                "individualized": False,
                "visible_recipient_headers_preserved": True,
                "attachments": meta,
                "in_reply_to": message_id,
                "references": references,
                "reply_to": {"mailbox": mailbox, "uid": uid, "message_id": message_id},
            })
            return result

        result = self._send_individualized(
            to=reply_to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            body_html=body_html,
            body_amp=None,
            attachments=attachments,
            track_opens=True,
            campaign_id=campaign_id,
            in_reply_to=message_id,
            references=references,
        )
        result.update({
            "in_reply_to": message_id,
            "references": references,
            "reply_to": {"mailbox": mailbox, "uid": uid, "message_id": message_id},
        })
        return result

    def _save_draft(self, msg: EmailMessage) -> dict:
        if "Date" not in msg:
            from email.utils import format_datetime
            msg["Date"] = format_datetime(datetime.now().astimezone())
        if "Message-ID" not in msg:
            from email.utils import make_msgid
            domain = self.settings.email_address.rsplit("@", 1)[-1]
            msg["Message-ID"] = make_msgid(domain=domain)

        with self._imap() as conn:
            typ, _ = conn.append(
                self.draft_mailbox,
                r"(\Seen \Draft)",
                imaplib.Time2Internaldate(datetime.now().timestamp()),
                msg.as_bytes(policy=policy.SMTP),
            )
            if typ != "OK":
                raise MailBridgeError("Could not save draft via IMAP APPEND")
        return {
            "draft_saved": True,
            "mailbox": self.draft_mailbox,
            "from": self.settings.email_address,
            "to": [a for _, a in getaddresses([msg.get("To", "")]) if a],
            "cc": [a for _, a in getaddresses([msg.get("Cc", "")]) if a],
            "bcc": [a for _, a in getaddresses([msg.get("Bcc", "")]) if a],
            "subject": str(msg.get("Subject", "")),
            "message_id": str(msg.get("Message-ID", "")),
        }

    def create_draft(
        self,
        *,
        to: list[str],
        subject: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        body_amp: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict:
        msg, recipients, meta = self._build_message(
            to=to, subject=subject, body=body, cc=cc, bcc=bcc,
            body_html=body_html, body_amp=body_amp, attachments=attachments,
            allow_unlisted=True, include_bcc_header=True,
        )
        result = self._save_draft(msg)
        result.update(
            {
                "html": True,
                "attachments": meta,
                "recipient_authorization": self.recipient_authorization_status(recipients)["results"],
            }
        )
        return result

    def create_reply_draft(
        self,
        *,
        mailbox: str,
        uid: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict:
        original = self.get_email(mailbox, uid)
        from_addresses = original.get("from_addresses") or []
        if not from_addresses:
            raise MailBridgeError("Original email has no valid From address")
        subject = original.get("subject", "") or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        message_id = original.get("message_id", "") or ""
        refs = original.get("references", "").strip()
        references = (refs + " " + message_id).strip() if message_id else refs
        msg, recipients, meta = self._build_message(
            to=[from_addresses[0]], subject=subject, body=body, cc=cc, bcc=bcc,
            body_html=body_html, attachments=attachments,
            allow_unlisted=True, include_bcc_header=True,
            in_reply_to=message_id, references=references,
        )
        result = self._save_draft(msg)
        result.update(
            {
                "html": True,
                "attachments": meta,
                "recipient_authorization": self.recipient_authorization_status(recipients)["results"],
                "reply_to": {"mailbox": mailbox, "uid": uid, "message_id": message_id},
            }
        )
        return result

    def move_email(self, mailbox: str, uid: str, destination_mailbox: str) -> dict:
        with self._imap() as conn:
            self._select(conn, mailbox, readonly=False)
            caps = {
                (x.decode() if isinstance(x, bytes) else str(x)).upper()
                for x in getattr(conn, "capabilities", ())
            }
            if "MOVE" in caps:
                typ, _ = conn.uid("MOVE", uid, destination_mailbox)
                if typ != "OK":
                    raise MailBridgeError("IMAP MOVE failed")
                method = "MOVE"
            else:
                typ, _ = conn.uid("COPY", uid, destination_mailbox)
                if typ != "OK":
                    raise MailBridgeError("IMAP COPY failed")
                typ, _ = conn.uid("STORE", uid, "+FLAGS.SILENT", r"(\Deleted)")
                if typ != "OK":
                    raise MailBridgeError("Copied message but could not mark source deleted")
                conn.expunge()
                method = "COPY+DELETE"
        return {
            "ok": True,
            "moved": True,
            "source_mailbox": mailbox,
            "destination_mailbox": destination_mailbox,
            "uid": uid,
            "method": method,
        }

    def set_seen(self, mailbox: str, uid: str, seen: bool) -> dict:
        with self._imap() as conn:
            self._select(conn, mailbox, readonly=False)
            op = "+FLAGS.SILENT" if seen else "-FLAGS.SILENT"
            typ, _ = conn.uid("STORE", uid, op, r"(\Seen)")
            if typ != "OK":
                raise MailBridgeError("Could not update \\Seen flag")
        return {"ok": True, "mailbox": mailbox, "uid": uid, "seen": seen}

    def mark_not_spam(self, mailbox: str, uid: str) -> dict:
        # Moving out of Junk is the most portable IMAP operation. Best-effort removal
        # of common junk keywords is attempted first, but not required for success.
        with self._imap() as conn:
            self._select(conn, mailbox, readonly=False)
            for flags in (r"(\Junk)", r"($Junk)", r"(Junk)", r"(Spam)"):
                try:
                    conn.uid("STORE", uid, "-FLAGS.SILENT", flags)
                except Exception:
                    pass
        result = self.move_email(mailbox, uid, self.inbox_mailbox)
        result["marked_not_spam"] = True
        return result

    def mark_as_spam(self, mailbox: str, uid: str) -> dict:
        # Best-effort add common junk keywords, then move to the configured Junk mailbox.
        with self._imap() as conn:
            self._select(conn, mailbox, readonly=False)
            for flags in (r"($Junk)", r"(Junk)", r"(Spam)"):
                try:
                    conn.uid("STORE", uid, "+FLAGS.SILENT", flags)
                    break
                except Exception:
                    continue
        result = self.move_email(mailbox, uid, self.junk_mailbox)
        result["marked_as_spam"] = True
        return result
