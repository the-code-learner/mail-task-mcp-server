from __future__ import annotations

from contextlib import closing

import hashlib
import io
import json
import math
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.parser import BytesParser
from email.policy import default
from html import unescape
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional at import time in unit environments
    PdfReader = None  # type: ignore[assignment]


class EmailSearchError(ValueError):
    pass


_TEXT_TYPES = {
    "text/plain",
    "text/csv",
    "text/markdown",
    "text/x-markdown",
    "application/json",
    "application/xml",
    "text/xml",
    "application/yaml",
    "application/x-yaml",
}
_TOKEN_RE = re.compile(r"[\w@.+-]{2,}", re.UNICODE)
_TAG_RE = re.compile(r"<[^>]+>")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _clean_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</li\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _decode_part(part: Message, payload: bytes) -> str:
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def extract_attachment_text(message_or_bytes: Message | bytes, *, max_total_chars: int = 200_000, max_part_chars: int = 80_000) -> list[dict[str, Any]]:
    """Extract local text from supported MIME attachments; never resolve external URLs."""
    message = BytesParser(policy=default).parsebytes(message_or_bytes) if isinstance(message_or_bytes, (bytes, bytearray)) else message_or_bytes
    results: list[dict[str, Any]] = []
    remaining = max(0, int(max_total_chars))
    for index, part in enumerate(message.walk() if message.is_multipart() else [message]):
        filename = str(part.get_filename() or "")
        disposition = str(part.get_content_disposition() or "")
        if not filename and disposition != "attachment":
            continue
        ctype = str(part.get_content_type() or "").lower()
        payload = part.get_payload(decode=True) or b""
        if not payload or remaining <= 0:
            continue
        text = ""
        error = None
        try:
            if ctype in _TEXT_TYPES or filename.lower().endswith((".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml")):
                text = _decode_part(part, payload)
            elif ctype == "text/html" or filename.lower().endswith((".html", ".htm")):
                text = _clean_html(_decode_part(part, payload))
            elif ctype == "application/pdf" or filename.lower().endswith(".pdf"):
                if PdfReader is None:
                    error = "pypdf unavailable"
                else:
                    reader = PdfReader(io.BytesIO(payload))
                    chunks: list[str] = []
                    used = 0
                    for page in reader.pages:
                        value = page.extract_text() or ""
                        chunks.append(value)
                        used += len(value)
                        if used >= max_part_chars:
                            break
                    text = "\n".join(chunks)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        if text:
            text = text[: min(max_part_chars, remaining)]
            remaining -= len(text)
        if text or error:
            results.append(
                {
                    "part_index": index,
                    "filename": filename or None,
                    "content_type": ctype,
                    "text": text,
                    "chars": len(text),
                    "error": error,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "network_access": False,
                }
            )
    return results


def _vector_blob(vector: Sequence[float]) -> bytes:
    arr = np.asarray(vector, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        raise EmailSearchError("Semantic embedding is empty")
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return arr.tobytes(order="C")


def _blob_vector(blob: bytes, dimensions: int) -> np.ndarray:
    arr = np.frombuffer(blob, dtype=np.float32)
    if arr.size != dimensions:
        raise EmailSearchError("Stored email embedding dimension mismatch")
    return arr


def _fts_query(query: str) -> str:
    tokens = [t for t in _TOKEN_RE.findall(query or "") if t.strip(".-+")]
    if not tokens:
        return ""
    # Quote every token to avoid exposing FTS syntax through MCP input.
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens[:32])


def _search_text(doc: Mapping[str, Any]) -> str:
    attachments = doc.get("attachments_text") or ""
    return "\n".join(
        str(doc.get(key) or "")
        for key in ("from_address", "to_address", "cc", "subject", "body_text", "snippet")
    ) + ("\n" + str(attachments) if attachments else "")


class HybridEmailIndex:
    """Persistent project-local email index: SQLite FTS5 + optional Model2Vec-compatible embeddings."""

    def __init__(
        self,
        db_path: str | Path = "/data/email-search-v990.db",
        *,
        embed_one: Callable[[str], Sequence[float]] | None = None,
        model_id: str = "postmaster-context-model",
    ):
        self.db_path = str(db_path)
        self.embed_one = embed_one
        self.model_id = str(model_id)
        self._lock = threading.RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS email_search_docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    mailbox TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    message_id TEXT,
                    date_utc TEXT,
                    from_address TEXT,
                    to_address TEXT,
                    cc TEXT,
                    subject TEXT,
                    snippet TEXT,
                    body_text TEXT,
                    attachments_text TEXT,
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    content_hash TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    UNIQUE(account_id, mailbox, uid)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS email_search_fts USING fts5(
                    from_address, to_address, cc, subject, snippet, body_text, attachments_text,
                    content='email_search_docs', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TABLE IF NOT EXISTS email_search_embeddings (
                    doc_id INTEGER PRIMARY KEY REFERENCES email_search_docs(id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    content_hash TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS email_search_docs_ai AFTER INSERT ON email_search_docs BEGIN
                    INSERT INTO email_search_fts(rowid,from_address,to_address,cc,subject,snippet,body_text,attachments_text)
                    VALUES(new.id,new.from_address,new.to_address,new.cc,new.subject,new.snippet,new.body_text,new.attachments_text);
                END;
                CREATE TRIGGER IF NOT EXISTS email_search_docs_ad AFTER DELETE ON email_search_docs BEGIN
                    INSERT INTO email_search_fts(email_search_fts,rowid,from_address,to_address,cc,subject,snippet,body_text,attachments_text)
                    VALUES('delete',old.id,old.from_address,old.to_address,old.cc,old.subject,old.snippet,old.body_text,old.attachments_text);
                END;
                CREATE TRIGGER IF NOT EXISTS email_search_docs_au AFTER UPDATE ON email_search_docs BEGIN
                    INSERT INTO email_search_fts(email_search_fts,rowid,from_address,to_address,cc,subject,snippet,body_text,attachments_text)
                    VALUES('delete',old.id,old.from_address,old.to_address,old.cc,old.subject,old.snippet,old.body_text,old.attachments_text);
                    INSERT INTO email_search_fts(rowid,from_address,to_address,cc,subject,snippet,body_text,attachments_text)
                    VALUES(new.id,new.from_address,new.to_address,new.cc,new.subject,new.snippet,new.body_text,new.attachments_text);
                END;
                CREATE INDEX IF NOT EXISTS idx_email_search_scope ON email_search_docs(account_id, mailbox, date_utc);
                """
            )

    def upsert(self, doc: Mapping[str, Any], *, embed: bool = True) -> dict[str, Any]:
        account_id = str(doc.get("account_id") or "").strip()
        mailbox = str(doc.get("mailbox") or "INBOX").strip()
        uid = str(doc.get("uid") or "").strip()
        if not account_id or not mailbox or not uid:
            raise EmailSearchError("Email index requires account_id, mailbox and uid")
        normalized = {
            "message_id": str(doc.get("message_id") or ""),
            "date_utc": str(doc.get("date_utc") or doc.get("date") or ""),
            "from_address": str(doc.get("from_address") or doc.get("from") or ""),
            "to_address": str(doc.get("to_address") or doc.get("to") or ""),
            "cc": str(doc.get("cc") or ""),
            "subject": str(doc.get("subject") or ""),
            "snippet": str(doc.get("snippet") or ""),
            "body_text": str(doc.get("body_text") or doc.get("body") or ""),
            "attachments_text": str(doc.get("attachments_text") or ""),
        }
        attachment_meta = doc.get("attachments") or []
        content = _search_text(normalized)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = _now_iso()
        with self._lock, closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT id,content_hash FROM email_search_docs WHERE account_id=? AND mailbox=? AND uid=?",
                (account_id, mailbox, uid),
            ).fetchone()
            if existing and str(existing["content_hash"]) == content_hash:
                doc_id = int(existing["id"])
                changed = False
            else:
                conn.execute(
                    """
                    INSERT INTO email_search_docs(
                        account_id,mailbox,uid,message_id,date_utc,from_address,to_address,cc,subject,snippet,body_text,
                        attachments_text,attachments_json,content_hash,indexed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(account_id,mailbox,uid) DO UPDATE SET
                        message_id=excluded.message_id,date_utc=excluded.date_utc,from_address=excluded.from_address,
                        to_address=excluded.to_address,cc=excluded.cc,subject=excluded.subject,snippet=excluded.snippet,
                        body_text=excluded.body_text,attachments_text=excluded.attachments_text,
                        attachments_json=excluded.attachments_json,content_hash=excluded.content_hash,indexed_at=excluded.indexed_at
                    """,
                    (
                        account_id, mailbox, uid, normalized["message_id"], normalized["date_utc"], normalized["from_address"],
                        normalized["to_address"], normalized["cc"], normalized["subject"], normalized["snippet"],
                        normalized["body_text"], normalized["attachments_text"],
                        json.dumps(attachment_meta, ensure_ascii=False, sort_keys=True), content_hash, now,
                    ),
                )
                row = conn.execute(
                    "SELECT id FROM email_search_docs WHERE account_id=? AND mailbox=? AND uid=?",
                    (account_id, mailbox, uid),
                ).fetchone()
                doc_id = int(row["id"])
                conn.execute("DELETE FROM email_search_embeddings WHERE doc_id=?", (doc_id,))
                changed = True
            semantic = False
            if embed and self.embed_one is not None:
                current = conn.execute(
                    "SELECT model_id,content_hash FROM email_search_embeddings WHERE doc_id=?", (doc_id,)
                ).fetchone()
                if not current or str(current["model_id"]) != self.model_id or str(current["content_hash"]) != content_hash:
                    vector = np.asarray(self.embed_one(content), dtype=np.float32).reshape(-1)
                    blob = _vector_blob(vector)
                    conn.execute(
                        "INSERT OR REPLACE INTO email_search_embeddings(doc_id,model_id,dimensions,vector,content_hash,indexed_at) VALUES(?,?,?,?,?,?)",
                        (doc_id, self.model_id, int(vector.size), blob, content_hash, now),
                    )
                semantic = True
            conn.commit()
        return {"ok": True, "doc_id": doc_id, "changed": changed, "semantic_indexed": semantic, "content_hash": content_hash}

    def search(
        self,
        query: str,
        *,
        account_id: str,
        mailbox: str | None = None,
        since_days: int | None = 90,
        limit: int = 20,
        semantic_weight: float = 0.60,
        lexical_weight: float = 0.40,
        semantic_scan_limit: int = 5000,
    ) -> dict[str, Any]:
        q = str(query or "").strip()
        if not q:
            raise EmailSearchError("Hybrid email search query is required")
        limit = max(1, min(int(limit), 100))
        scope_sql = ["d.account_id=?"]
        params: list[Any] = [str(account_id)]
        if mailbox:
            scope_sql.append("d.mailbox=?")
            params.append(str(mailbox))
        if since_days is not None and int(since_days) >= 0:
            cutoff = datetime.now(UTC) - timedelta(days=int(since_days))
            scope_sql.append("(d.date_utc='' OR d.date_utc IS NULL OR d.date_utc>=?)")
            params.append(cutoff.isoformat())
        scope = " AND ".join(scope_sql)
        lex: dict[int, tuple[int, float]] = {}
        fts = _fts_query(q)
        with closing(self._connect()) as conn:
            if fts:
                rows = conn.execute(
                    f"""
                    SELECT d.id, bm25(email_search_fts, 1.2,1.0,0.8,2.5,0.8,1.5,1.8) AS score
                    FROM email_search_fts JOIN email_search_docs d ON d.id=email_search_fts.rowid
                    WHERE email_search_fts MATCH ? AND {scope}
                    ORDER BY score ASC LIMIT ?
                    """,
                    [fts, *params, max(limit * 8, 100)],
                ).fetchall()
                for rank, row in enumerate(rows, 1):
                    lex[int(row["id"])] = (rank, float(row["score"]))

            sem: dict[int, tuple[int, float]] = {}
            semantic_available = False
            if self.embed_one is not None:
                qv = np.asarray(self.embed_one(q), dtype=np.float32).reshape(-1)
                qnorm = float(np.linalg.norm(qv))
                if qnorm > 0:
                    qv = qv / qnorm
                rows = conn.execute(
                    f"""
                    SELECT d.id,e.dimensions,e.vector FROM email_search_docs d
                    JOIN email_search_embeddings e ON e.doc_id=d.id
                    WHERE e.model_id=? AND {scope}
                    ORDER BY d.date_utc DESC LIMIT ?
                    """,
                    [self.model_id, *params, max(1, min(int(semantic_scan_limit), 20000))],
                ).fetchall()
                scored: list[tuple[int, float]] = []
                for row in rows:
                    if int(row["dimensions"]) != int(qv.size):
                        continue
                    dv = _blob_vector(row["vector"], int(row["dimensions"]))
                    scored.append((int(row["id"]), float(np.dot(qv, dv))))
                scored.sort(key=lambda item: item[1], reverse=True)
                sem = {doc_id: (rank, score) for rank, (doc_id, score) in enumerate(scored, 1)}
                semantic_available = bool(scored)

            ids = set(lex) | set(sem)
            fused: list[tuple[int, float]] = []
            k = 60.0
            for doc_id in ids:
                score = 0.0
                if doc_id in lex:
                    score += float(lexical_weight) / (k + lex[doc_id][0])
                if doc_id in sem:
                    score += float(semantic_weight) / (k + sem[doc_id][0])
                fused.append((doc_id, score))
            fused.sort(key=lambda item: item[1], reverse=True)
            selected = fused[:limit]
            if not selected:
                return {
                    "ok": True, "query": q, "results": [], "count": 0,
                    "semantic_active": semantic_available, "lexical_active": bool(fts),
                    "index_only": True,
                }
            placeholders = ",".join("?" for _ in selected)
            docs = conn.execute(
                f"SELECT * FROM email_search_docs WHERE id IN ({placeholders})", [doc_id for doc_id, _ in selected]
            ).fetchall()
            by_id = {int(row["id"]): row for row in docs}
        results: list[dict[str, Any]] = []
        for doc_id, fused_score in selected:
            row = by_id.get(doc_id)
            if row is None:
                continue
            results.append(
                {
                    "account_id": row["account_id"], "mailbox": row["mailbox"], "uid": row["uid"],
                    "message_id": row["message_id"], "date_utc": row["date_utc"], "from": row["from_address"],
                    "to": row["to_address"], "cc": row["cc"], "subject": row["subject"], "snippet": row["snippet"],
                    "body_indexed": bool(row["body_text"]), "attachment_text_indexed": bool(row["attachments_text"]),
                    "attachments": json.loads(row["attachments_json"] or "[]"), "score": fused_score,
                    "lexical_rank": lex.get(doc_id, (None, None))[0], "semantic_rank": sem.get(doc_id, (None, None))[0],
                    "semantic_similarity": sem.get(doc_id, (None, None))[1],
                }
            )
        return {
            "ok": True, "query": q, "results": results, "count": len(results),
            "semantic_active": semantic_available, "lexical_active": bool(fts), "index_only": True,
        }

    def status(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            docs = int(conn.execute("SELECT COUNT(*) FROM email_search_docs").fetchone()[0])
            vectors = int(conn.execute("SELECT COUNT(*) FROM email_search_embeddings WHERE model_id=?", (self.model_id,)).fetchone()[0])
            attachment_docs = int(conn.execute("SELECT COUNT(*) FROM email_search_docs WHERE attachments_text<>''").fetchone()[0])
        return {
            "ok": True, "db_path": self.db_path, "documents": docs, "semantic_documents": vectors,
            "attachment_text_documents": attachment_docs, "fts5": True, "model_id": self.model_id,
            "external_resource_fetch": False,
        }


def combine_attachment_text(extracted: Iterable[Mapping[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    chunks: list[str] = []
    metadata: list[dict[str, Any]] = []
    for row in extracted:
        text = str(row.get("text") or "")
        filename = str(row.get("filename") or "")
        if text:
            chunks.append(f"[{filename or 'attachment'}]\n{text}")
        metadata.append({
            "filename": filename or None,
            "content_type": row.get("content_type"),
            "chars": int(row.get("chars") or len(text)),
            "sha256": row.get("sha256"),
            "extraction_error": row.get("error"),
        })
    return "\n\n".join(chunks), metadata
