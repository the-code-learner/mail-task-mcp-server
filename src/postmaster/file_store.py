from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class FileStoreError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        tag = str(raw).strip().lower()
        if not tag or tag in seen:
            continue
        if len(tag) > 64:
            raise FileStoreError("file tags must be at most 64 characters")
        seen.add(tag)
        out.append(tag)
        if len(out) >= 32:
            break
    return out


def _safe_filename(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise FileStoreError("filename is required")
    if len(value) > 255:
        raise FileStoreError("filename must be at most 255 characters")
    if "\x00" in value or "/" in value or "\\" in value or value in {".", ".."}:
        raise FileStoreError("filename must be a plain file name without path components")
    return value


class FileStore:
    """Persistent small-file store with SQLite metadata and content-addressed blobs."""

    HARD_MAX_BYTES = 10 * 1024 * 1024
    HARD_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
    HARD_MAX_FILES = 10000

    def __init__(
        self,
        db_path: str | None = None,
        root: str | None = None,
        max_bytes: int | None = None,
        max_total_bytes: int | None = None,
        max_files: int | None = None,
        text_max_chars: int | None = None,
    ) -> None:
        self.db_path = db_path or os.getenv("FILE_STORE_DB_PATH", "/data/files.db")
        self.root = Path(root or os.getenv("FILE_STORE_ROOT", "/data/files"))
        configured_max = max_bytes if max_bytes is not None else int(os.getenv("FILE_STORE_MAX_BYTES", str(1024 * 1024)))
        configured_total = max_total_bytes if max_total_bytes is not None else int(os.getenv("FILE_STORE_MAX_TOTAL_BYTES", str(100 * 1024 * 1024)))
        configured_files = max_files if max_files is not None else int(os.getenv("FILE_STORE_MAX_FILES", "1000"))
        configured_text = text_max_chars if text_max_chars is not None else int(os.getenv("FILE_STORE_TEXT_MAX_CHARS", "200000"))
        self.max_bytes = max(1, min(int(configured_max), self.HARD_MAX_BYTES))
        self.max_total_bytes = max(self.max_bytes, min(int(configured_total), self.HARD_MAX_TOTAL_BYTES))
        self.max_files = max(1, min(int(configured_files), self.HARD_MAX_FILES))
        self.text_max_chars = max(1000, min(int(configured_text), 2_000_000))
        self.blob_root = self.root / "blobs"
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS stored_files (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stored_files_scope
                    ON stored_files(owner_id, project_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_stored_files_sha
                    ON stored_files(sha256);
                """
            )

    def _snapshot(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        try:
            tags = json.loads(d.pop("tags_json", "[]"))
        except Exception:
            tags = []
        d["tags"] = [str(x) for x in tags if str(x).strip()]
        return d

    def _blob_path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
            raise FileStoreError("invalid blob digest")
        return self.blob_root / sha256[:2] / sha256[2:]

    def _enforce_capacity(self, conn: sqlite3.Connection, incoming_size: int) -> None:
        count, total = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM stored_files"
        ).fetchone()
        if int(count) >= self.max_files:
            raise FileStoreError(f"file store limit reached ({self.max_files} files)")
        if int(total) + int(incoming_size) > self.max_total_bytes:
            raise FileStoreError(f"file store total-size limit exceeded ({self.max_total_bytes} bytes)")

    def save_bytes(
        self,
        *,
        owner_id: str,
        filename: str,
        data: bytes,
        project_id: str | None = None,
        media_type: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        owner_id = str(owner_id or "").strip()
        if not owner_id:
            raise FileStoreError("owner_id is required")
        project_id = str(project_id).strip() if project_id else None
        filename = _safe_filename(filename)
        payload = bytes(data)
        if len(payload) > self.max_bytes:
            raise FileStoreError(f"file exceeds FILE_STORE_MAX_BYTES ({self.max_bytes} bytes)")
        digest = hashlib.sha256(payload).hexdigest()
        guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        media_type = str(media_type or guessed).strip() or "application/octet-stream"
        if len(media_type) > 200 or any(ch in media_type for ch in "\r\n"):
            raise FileStoreError("invalid media_type")
        description = str(description or "").strip()
        if len(description) > 4000:
            raise FileStoreError("description must be at most 4000 characters")
        clean_tags = _clean_tags(tags)
        fid = str(file_id or uuid.uuid4())
        now = _now()
        blob = self._blob_path(digest)

        with self._lock, self._conn() as conn:
            self._enforce_capacity(conn, len(payload))
            if conn.execute("SELECT 1 FROM stored_files WHERE id=?", (fid,)).fetchone():
                raise FileStoreError(f"file id already exists: {fid}")
            blob.parent.mkdir(parents=True, exist_ok=True)
            if not blob.exists():
                tmp = blob.with_name(blob.name + f".tmp-{uuid.uuid4().hex}")
                tmp.write_bytes(payload)
                try:
                    os.chmod(tmp, 0o600)
                except Exception:
                    pass
                os.replace(tmp, blob)
            conn.execute(
                """
                INSERT INTO stored_files(
                    id, owner_id, project_id, filename, media_type, size_bytes, sha256,
                    description, tags_json, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    fid, owner_id, project_id, filename, media_type, len(payload), digest,
                    description, json.dumps(clean_tags, ensure_ascii=False), now, now,
                ),
            )
        return self.get_info(fid)

    def save_base64(self, *, content_base64: str, **kwargs: Any) -> dict[str, Any]:
        raw = str(content_base64 or "").strip()
        if len(raw) > ((self.max_bytes + 2) // 3) * 4 + 16:
            raise FileStoreError("base64 payload exceeds configured file-size limit")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise FileStoreError("content_base64 is not valid base64") from exc
        return self.save_bytes(data=data, **kwargs)

    def save_text(self, *, content: str, media_type: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.save_bytes(
            data=str(content).encode("utf-8"),
            media_type=media_type or "text/plain; charset=utf-8",
            **kwargs,
        )

    def get_info(self, file_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM stored_files WHERE id=?", (str(file_id),)).fetchone()
        if not row:
            raise FileStoreError("stored file not found")
        return self._snapshot(row)

    def list_files(
        self,
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
        include_global: bool = True,
        tag: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        args: list[Any] = []
        if owner_id:
            where.append("owner_id=?")
            args.append(str(owner_id))
        if project_id is not None:
            if include_global:
                where.append("(project_id=? OR project_id IS NULL)")
                args.append(str(project_id))
            else:
                where.append("project_id=?")
                args.append(str(project_id))
        sql = "SELECT * FROM stored_files" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        args.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        with self._conn() as conn:
            items = [self._snapshot(row) for row in conn.execute(sql, args).fetchall()]
        if tag:
            wanted = str(tag).strip().lower()
            items = [item for item in items if wanted in item.get("tags", [])]
        return items

    def _read_bytes(self, file_id: str) -> tuple[dict[str, Any], bytes]:
        info = self.get_info(file_id)
        path = self._blob_path(str(info["sha256"]))
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise FileStoreError("stored blob is missing from disk") from exc
        if len(data) != int(info["size_bytes"]) or hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise FileStoreError("stored blob failed integrity verification")
        return info, data

    def read_base64(self, file_id: str) -> dict[str, Any]:
        info, data = self._read_bytes(file_id)
        return {"ok": True, "file": info, "content_base64": base64.b64encode(data).decode("ascii")}

    def read_text(self, file_id: str, *, max_chars: int | None = None) -> dict[str, Any]:
        info, data = self._read_bytes(file_id)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileStoreError("file is not valid UTF-8 text; use get_file_base64 instead") from exc
        limit = self.text_max_chars if max_chars is None else max(1, min(int(max_chars), self.text_max_chars))
        truncated = len(text) > limit
        return {
            "ok": True,
            "file": info,
            "text": text[:limit],
            "truncated": truncated,
            "returned_chars": min(len(text), limit),
            "total_chars": len(text),
        }

    def update_metadata(
        self,
        file_id: str,
        *,
        filename: str | None = None,
        media_type: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        current = self.get_info(file_id)
        new_filename = _safe_filename(filename) if filename is not None else current["filename"]
        new_media = str(media_type).strip() if media_type is not None else current["media_type"]
        if not new_media or len(new_media) > 200 or any(ch in new_media for ch in "\r\n"):
            raise FileStoreError("invalid media_type")
        new_description = str(description).strip() if description is not None else current["description"]
        if len(new_description) > 4000:
            raise FileStoreError("description must be at most 4000 characters")
        new_tags = _clean_tags(tags) if tags is not None else current["tags"]
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE stored_files SET filename=?,media_type=?,description=?,tags_json=?,updated_at=? WHERE id=?",
                (new_filename, new_media, new_description, json.dumps(new_tags, ensure_ascii=False), _now(), str(file_id)),
            )
        return self.get_info(file_id)

    def delete(self, file_id: str) -> dict[str, Any]:
        info = self.get_info(file_id)
        digest = str(info["sha256"])
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM stored_files WHERE id=?", (str(file_id),))
            refs = int(conn.execute("SELECT COUNT(*) FROM stored_files WHERE sha256=?", (digest,)).fetchone()[0])
        blob_deleted = False
        if refs == 0:
            path = self._blob_path(digest)
            try:
                path.unlink()
                blob_deleted = True
            except FileNotFoundError:
                pass
            try:
                path.parent.rmdir()
            except OSError:
                pass
        return {"ok": True, "deleted": str(file_id), "blob_deleted": blob_deleted}

    def raw_bytes(self, file_id: str) -> tuple[dict[str, Any], bytes]:
        """WebGUI-only helper; callers must still enforce external authentication."""
        return self._read_bytes(file_id)

    def status(self) -> dict[str, Any]:
        with self._conn() as conn:
            count, total, unique_blobs = conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(size_bytes),0),COUNT(DISTINCT sha256) FROM stored_files"
            ).fetchone()
        return {
            "ok": True,
            "db_path": self.db_path,
            "root": str(self.root),
            "files": int(count),
            "logical_bytes": int(total),
            "unique_blobs": int(unique_blobs),
            "max_bytes_per_file": self.max_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_files": self.max_files,
            "text_max_chars": self.text_max_chars,
        }
