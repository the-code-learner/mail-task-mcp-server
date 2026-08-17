from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable


class KnowledgeError(RuntimeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _clean_tags(tags: Iterable[str] | str | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        raw = re.split(r"[,\n]", tags)
    else:
        raw = list(tags)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = str(item).strip().lower()
        if not tag or tag in seen:
            continue
        if len(tag) > 80:
            raise KnowledgeError("Tags must be at most 80 characters")
        seen.add(tag)
        out.append(tag)
    return out[:64]


class KnowledgeStore:
    VALID_KINDS = {"memory", "skill"}

    def __init__(self, db_path: str | None = None, chunk_chars: int | None = None, chunk_overlap: int | None = None):
        self.db_path = db_path or os.getenv("CONTEXT_DB_PATH", "/data/knowledge.db")
        self.chunk_chars = max(300, int(chunk_chars or os.getenv("CONTEXT_CHUNK_CHARS", "1200")))
        self.chunk_overlap = max(0, min(self.chunk_chars // 2, int(chunk_overlap or os.getenv("CONTEXT_CHUNK_OVERLAP", "160"))))
        self._lock = threading.RLock()
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT,
                    kind TEXT NOT NULL CHECK(kind IN ('memory','skill')),
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    priority REAL NOT NULL DEFAULT 0.5,
                    always_include INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_items_scope
                  ON knowledge_items(owner_id, project_id, kind, enabled);
                CREATE INDEX IF NOT EXISTS idx_knowledge_items_priority
                  ON knowledge_items(owner_id, project_id, priority DESC, updated_at DESC);

                CREATE TABLE IF NOT EXISTS knowledge_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'mcp',
                    UNIQUE(item_id, revision)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_revisions_item
                  ON knowledge_revisions(item_id, revision DESC);

                CREATE TABLE IF NOT EXISTS knowledge_tags (
                    tag TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_item_tags (
                    item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
                    tag TEXT NOT NULL REFERENCES knowledge_tags(tag) ON DELETE CASCADE,
                    PRIMARY KEY(item_id, tag)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_item_tags_tag ON knowledge_item_tags(tag, item_id);

                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    char_start INTEGER NOT NULL,
                    char_end INTEGER NOT NULL,
                    embedding BLOB,
                    embedding_dims INTEGER,
                    embedding_model TEXT,
                    embedded_at TEXT,
                    UNIQUE(item_id, chunk_index)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_item ON knowledge_chunks(item_id, chunk_index);
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding ON knowledge_chunks(embedding_model, embedding_dims);

                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    chunk_id UNINDEXED,
                    item_id UNINDEXED,
                    title,
                    content,
                    tags,
                    tokenize='unicode61 remove_diacritics 2'
                );

                CREATE TABLE IF NOT EXISTS knowledge_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT,
                    owner_id TEXT,
                    project_id TEXT,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'mcp',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_audit_item ON knowledge_audit(item_id, created_at DESC);
                """
            )

    @staticmethod
    def _validate_kind(kind: str) -> str:
        k = (kind or "").strip().lower()
        if k not in KnowledgeStore.VALID_KINDS:
            raise KnowledgeError("kind must be 'memory' or 'skill'")
        return k

    @staticmethod
    def _validate_scope(owner_id: str, project_id: str | None) -> tuple[str, str | None]:
        owner = (owner_id or "").strip()
        if not owner:
            raise KnowledgeError("owner_id is required")
        project = (project_id or "").strip() or None
        if len(owner) > 120 or (project and len(project) > 160):
            raise KnowledgeError("owner_id/project_id too long")
        return owner, project

    @staticmethod
    def _validate_priority(priority: float | int | str) -> float:
        try:
            value = float(priority)
        except Exception as exc:
            raise KnowledgeError("priority must be a number between 0 and 1") from exc
        if not 0.0 <= value <= 1.0:
            raise KnowledgeError("priority must be between 0 and 1")
        return value

    @staticmethod
    def _snapshot(row: sqlite3.Row | dict[str, Any], tags: list[str]) -> dict[str, Any]:
        get = row.get if isinstance(row, dict) else lambda key, default=None: row[key] if key in row.keys() else default
        return {
            "id": get("id"), "owner_id": get("owner_id"), "project_id": get("project_id"),
            "kind": get("kind"), "title": get("title"), "content": get("content"),
            "priority": float(get("priority", 0.5)), "always_include": bool(get("always_include", 0)),
            "enabled": bool(get("enabled", 1)), "metadata": _json_load(get("metadata_json", "{}"), {}),
            "revision": int(get("revision", 1)), "created_at": get("created_at"), "updated_at": get("updated_at"),
            "tags": tags,
        }

    def _tags_for(self, conn: sqlite3.Connection, item_id: str) -> list[str]:
        return [r[0] for r in conn.execute(
            "SELECT tag FROM knowledge_item_tags WHERE item_id=? ORDER BY tag", (item_id,)
        ).fetchall()]

    def _set_tags(self, conn: sqlite3.Connection, item_id: str, tags: list[str]) -> None:
        conn.execute("DELETE FROM knowledge_item_tags WHERE item_id=?", (item_id,))
        now = utcnow()
        for tag in tags:
            conn.execute("INSERT OR IGNORE INTO knowledge_tags(tag, created_at) VALUES(?,?)", (tag, now))
            conn.execute("INSERT OR IGNORE INTO knowledge_item_tags(item_id, tag) VALUES(?,?)", (item_id, tag))

    def _chunk_text(self, title: str, content: str) -> list[tuple[str, int, int]]:
        text = content.strip()
        if not text:
            return [(title.strip(), 0, 0)]
        if len(text) <= self.chunk_chars:
            return [(text, 0, len(text))]
        chunks: list[tuple[str, int, int]] = []
        start = 0
        n = len(text)
        while start < n:
            target = min(n, start + self.chunk_chars)
            end = target
            if target < n:
                floor = start + int(self.chunk_chars * 0.60)
                candidates = [text.rfind("\n\n", floor, target), text.rfind("\n", floor, target), text.rfind(". ", floor, target), text.rfind(" ", floor, target)]
                best = max(candidates)
                if best > floor:
                    end = best + (2 if text[best:best+2] == ". " else 0)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append((chunk, start, end))
            if end >= n:
                break
            start = max(start + 1, end - self.chunk_overlap)
        return chunks or [(text, 0, n)]

    def _rebuild_chunks(self, conn: sqlite3.Connection, item_id: str) -> None:
        row = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            return
        tags = self._tags_for(conn, item_id)
        conn.execute("DELETE FROM knowledge_fts WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM knowledge_chunks WHERE item_id=?", (item_id,))
        for idx, (chunk, start, end) in enumerate(self._chunk_text(str(row["title"]), str(row["content"]))):
            cid = f"{item_id}:{idx}"
            conn.execute(
                "INSERT INTO knowledge_chunks(id,item_id,chunk_index,content,char_start,char_end) VALUES(?,?,?,?,?,?)",
                (cid, item_id, idx, chunk, start, end),
            )
            conn.execute(
                "INSERT INTO knowledge_fts(chunk_id,item_id,title,content,tags) VALUES(?,?,?,?,?)",
                (cid, item_id, str(row["title"]), chunk, " ".join(tags)),
            )

    def _audit(self, conn: sqlite3.Connection, *, item_id: str | None, owner_id: str | None, project_id: str | None,
               action: str, actor: str, details: dict[str, Any] | None = None) -> None:
        conn.execute(
            "INSERT INTO knowledge_audit(item_id,owner_id,project_id,action,actor,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (item_id, owner_id, project_id, action, actor or "mcp", json.dumps(details or {}, ensure_ascii=False), utcnow()),
        )

    def _save_revision(self, conn: sqlite3.Connection, item_id: str, actor: str) -> None:
        row = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise KnowledgeError("Knowledge item not found")
        tags = self._tags_for(conn, item_id)
        snap = self._snapshot(row, tags)
        conn.execute(
            "INSERT OR REPLACE INTO knowledge_revisions(item_id,revision,snapshot_json,created_at,actor) VALUES(?,?,?,?,?)",
            (item_id, int(row["revision"]), json.dumps(snap, ensure_ascii=False), utcnow(), actor or "mcp"),
        )

    def create_item(self, *, kind: str, owner_id: str, project_id: str | None, title: str, content: str,
                    priority: float = 0.5, always_include: bool = False, enabled: bool = True,
                    tags: Iterable[str] | str | None = None, metadata: dict[str, Any] | None = None,
                    actor: str = "mcp", item_id: str | None = None) -> dict[str, Any]:
        kind = self._validate_kind(kind)
        owner_id, project_id = self._validate_scope(owner_id, project_id)
        title = (title or "").strip()
        content = (content or "").strip()
        if not title or not content:
            raise KnowledgeError("title and content are required")
        if len(title) > 300:
            raise KnowledgeError("title must be at most 300 characters")
        priority = self._validate_priority(priority)
        tags_clean = _clean_tags(tags)
        now = utcnow()
        iid = (item_id or str(uuid.uuid4())).strip()
        with self._lock, self._conn() as conn:
            if conn.execute("SELECT 1 FROM knowledge_items WHERE id=?", (iid,)).fetchone():
                raise KnowledgeError(f"Knowledge item already exists: {iid}")
            conn.execute(
                """INSERT INTO knowledge_items
                (id,owner_id,project_id,kind,title,content,priority,always_include,enabled,metadata_json,revision,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (iid, owner_id, project_id, kind, title, content, priority, int(bool(always_include)), int(bool(enabled)),
                 json.dumps(metadata or {}, ensure_ascii=False), 1, now, now),
            )
            self._set_tags(conn, iid, tags_clean)
            self._rebuild_chunks(conn, iid)
            self._save_revision(conn, iid, actor)
            self._audit(conn, item_id=iid, owner_id=owner_id, project_id=project_id, action="create", actor=actor,
                        details={"kind": kind})
        return self.get_item(iid)

    def get_item(self, item_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise KnowledgeError("Knowledge item not found")
            out = self._snapshot(row, self._tags_for(conn, item_id))
            out["chunk_count"] = int(conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE item_id=?", (item_id,)).fetchone()[0])
            out["embedded_chunk_count"] = int(conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE item_id=? AND embedding IS NOT NULL", (item_id,)).fetchone()[0])
            return out

    def update_item(self, item_id: str, *, title: str | None = None, content: str | None = None,
                    priority: float | None = None, always_include: bool | None = None, enabled: bool | None = None,
                    tags: Iterable[str] | str | None = None, metadata: dict[str, Any] | None = None,
                    owner_id: str | None = None, project_id: str | None = None, set_project: bool = False,
                    actor: str = "mcp") -> dict[str, Any]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise KnowledgeError("Knowledge item not found")
            values = dict(row)
            new_title = (title.strip() if title is not None else str(row["title"]))
            new_content = (content.strip() if content is not None else str(row["content"]))
            if not new_title or not new_content:
                raise KnowledgeError("title and content cannot be empty")
            if len(new_title) > 300:
                raise KnowledgeError("title must be at most 300 characters")
            new_priority = self._validate_priority(priority if priority is not None else row["priority"])
            new_owner = (owner_id or str(row["owner_id"])).strip()
            new_project = ((project_id or "").strip() or None) if set_project else row["project_id"]
            new_owner, new_project = self._validate_scope(new_owner, new_project)
            current_tags = self._tags_for(conn, item_id)
            new_tags = _clean_tags(tags) if tags is not None else current_tags
            new_metadata = metadata if metadata is not None else _json_load(row["metadata_json"], {})
            new_revision = int(row["revision"]) + 1
            conn.execute(
                """UPDATE knowledge_items SET owner_id=?,project_id=?,title=?,content=?,priority=?,always_include=?,enabled=?,
                metadata_json=?,revision=?,updated_at=? WHERE id=?""",
                (new_owner, new_project, new_title, new_content, new_priority,
                 int(bool(always_include)) if always_include is not None else int(row["always_include"]),
                 int(bool(enabled)) if enabled is not None else int(row["enabled"]),
                 json.dumps(new_metadata or {}, ensure_ascii=False), new_revision, utcnow(), item_id),
            )
            self._set_tags(conn, item_id, new_tags)
            self._rebuild_chunks(conn, item_id)
            self._save_revision(conn, item_id, actor)
            self._audit(conn, item_id=item_id, owner_id=new_owner, project_id=new_project, action="update", actor=actor,
                        details={"from_revision": int(row["revision"]), "to_revision": new_revision})
        return self.get_item(item_id)

    def delete_item(self, item_id: str, *, actor: str = "mcp") -> dict[str, Any]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise KnowledgeError("Knowledge item not found")
            self._audit(conn, item_id=item_id, owner_id=row["owner_id"], project_id=row["project_id"],
                        action="delete", actor=actor, details={"kind": row["kind"], "title": row["title"]})
            conn.execute("DELETE FROM knowledge_fts WHERE item_id=?", (item_id,))
            conn.execute("DELETE FROM knowledge_items WHERE id=?", (item_id,))
        return {"ok": True, "deleted": item_id}

    def list_items(self, *, kind: str | None = None, owner_id: str | None = None, project_id: str | None = None,
                   include_global: bool = True, enabled_only: bool = False, tag: str | None = None,
                   limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        where: list[str] = []
        args: list[Any] = []
        if kind:
            where.append("i.kind=?"); args.append(self._validate_kind(kind))
        if owner_id:
            where.append("i.owner_id=?"); args.append(owner_id)
        if project_id is not None:
            if include_global:
                where.append("(i.project_id=? OR i.project_id IS NULL)"); args.append(project_id)
            else:
                where.append("i.project_id=?"); args.append(project_id)
        if enabled_only:
            where.append("i.enabled=1")
        if tag:
            where.append("EXISTS (SELECT 1 FROM knowledge_item_tags it WHERE it.item_id=i.id AND it.tag=?)")
            args.append(tag.strip().lower())
        sql = "SELECT i.* FROM knowledge_items i" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY i.always_include DESC,i.priority DESC,i.updated_at DESC LIMIT ? OFFSET ?"
        args.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
            return [self._snapshot(r, self._tags_for(conn, str(r["id"]))) for r in rows]

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[\w@.+:/-]+", (query or "").strip(), flags=re.UNICODE)
        tokens = [t for t in tokens if t][:24]
        if not tokens:
            return ""
        return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)

    def lexical_search(self, query: str, *, owner_id: str | None = None, project_id: str | None = None,
                       include_global: bool = True, kinds: list[str] | None = None, limit: int = 50) -> list[dict[str, Any]]:
        fts = self._fts_query(query)
        if not fts:
            return []
        where = ["knowledge_fts MATCH ?", "i.enabled=1"]
        args: list[Any] = [fts]
        if owner_id:
            where.append("i.owner_id=?"); args.append(owner_id)
        if project_id is not None:
            if include_global:
                where.append("(i.project_id=? OR i.project_id IS NULL)"); args.append(project_id)
            else:
                where.append("i.project_id=?"); args.append(project_id)
        if kinds:
            clean = [self._validate_kind(k) for k in kinds]
            where.append("i.kind IN (%s)" % ",".join("?" for _ in clean)); args.extend(clean)
        sql = f"""
            SELECT f.chunk_id,f.item_id,f.content AS chunk_content,bm25(knowledge_fts, 2.5, 1.0, 1.2) AS lexical_raw,
                   i.owner_id,i.project_id,i.kind,i.title,i.content,i.priority,i.always_include,i.updated_at
            FROM knowledge_fts f JOIN knowledge_items i ON i.id=f.item_id
            WHERE {' AND '.join(where)}
            ORDER BY lexical_raw ASC LIMIT ?
        """
        args.append(max(1, min(int(limit), 500)))
        with self._conn() as conn:
            try:
                rows = conn.execute(sql, args).fetchall()
            except sqlite3.OperationalError as exc:
                raise KnowledgeError(f"FTS5 query failed: {exc}") from exc
            out = []
            for rank, r in enumerate(rows, 1):
                d = dict(r)
                d["lexical_rank"] = rank
                d["tags"] = self._tags_for(conn, str(r["item_id"]))
                out.append(d)
            return out

    def always_items(self, *, owner_id: str, project_id: str | None = None, kinds: list[str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where = ["owner_id=?", "enabled=1", "always_include=1"]
        args: list[Any] = [owner_id]
        if project_id is not None:
            where.append("(project_id=? OR project_id IS NULL)"); args.append(project_id)
        else:
            where.append("project_id IS NULL")
        if kinds:
            clean = [self._validate_kind(k) for k in kinds]
            where.append("kind IN (%s)" % ",".join("?" for _ in clean)); args.extend(clean)
        args.append(max(1, min(int(limit), 1000)))
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM knowledge_items WHERE " + " AND ".join(where) + " ORDER BY priority DESC,updated_at DESC LIMIT ?", args).fetchall()
            return [self._snapshot(r, self._tags_for(conn, str(r["id"]))) for r in rows]

    def chunks_for_embedding(self, *, only_missing_for_model: str | None = None, item_id: str | None = None,
                             owner_id: str | None = None, project_id: str | None = None, limit: int = 100000) -> list[dict[str, Any]]:
        where = ["i.enabled=1"]
        args: list[Any] = []
        if only_missing_for_model:
            where.append("(c.embedding IS NULL OR c.embedding_model<>?)"); args.append(only_missing_for_model)
        if item_id:
            where.append("c.item_id=?"); args.append(item_id)
        if owner_id:
            where.append("i.owner_id=?"); args.append(owner_id)
        if project_id is not None:
            where.append("(i.project_id=? OR i.project_id IS NULL)"); args.append(project_id)
        args.append(max(1, min(int(limit), 200000)))
        sql = f"""SELECT c.id AS chunk_id,c.item_id,c.chunk_index,c.content,c.embedding,c.embedding_dims,c.embedding_model,
                         i.owner_id,i.project_id,i.kind,i.title,i.priority,i.always_include,i.updated_at
                  FROM knowledge_chunks c JOIN knowledge_items i ON i.id=c.item_id
                  WHERE {' AND '.join(where)} ORDER BY i.updated_at DESC,c.chunk_index ASC LIMIT ?"""
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def save_embedding(self, chunk_id: str, blob: bytes, dims: int, model_id: str) -> None:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE knowledge_chunks SET embedding=?,embedding_dims=?,embedding_model=?,embedded_at=? WHERE id=?",
                (sqlite3.Binary(blob), int(dims), model_id, utcnow(), chunk_id),
            )
            if cur.rowcount != 1:
                raise KnowledgeError(f"Chunk not found: {chunk_id}")

    def clear_embeddings(self, model_id: str | None = None) -> int:
        with self._lock, self._conn() as conn:
            if model_id:
                cur = conn.execute("UPDATE knowledge_chunks SET embedding=NULL,embedding_dims=NULL,embedding_model=NULL,embedded_at=NULL WHERE embedding_model=?", (model_id,))
            else:
                cur = conn.execute("UPDATE knowledge_chunks SET embedding=NULL,embedding_dims=NULL,embedding_model=NULL,embedded_at=NULL")
            return int(cur.rowcount)

    def revisions(self, item_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT revision,snapshot_json,created_at,actor FROM knowledge_revisions WHERE item_id=? ORDER BY revision DESC LIMIT ?",
                (item_id, max(1, min(int(limit), 500))),
            ).fetchall()
            return [{"revision": int(r["revision"]), "snapshot": _json_load(r["snapshot_json"], {}), "created_at": r["created_at"], "actor": r["actor"]} for r in rows]

    def restore_revision(self, item_id: str, revision: int, *, actor: str = "mcp") -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT snapshot_json FROM knowledge_revisions WHERE item_id=? AND revision=?", (item_id, int(revision))).fetchone()
            if not row:
                raise KnowledgeError("Revision not found")
            snap = _json_load(row["snapshot_json"], {})
        return self.update_item(
            item_id, title=snap.get("title"), content=snap.get("content"), priority=snap.get("priority", 0.5),
            always_include=bool(snap.get("always_include")), enabled=bool(snap.get("enabled", True)),
            tags=snap.get("tags", []), metadata=snap.get("metadata", {}), owner_id=snap.get("owner_id"),
            project_id=snap.get("project_id"), set_project=True, actor=actor,
        )

    def audit(self, item_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if item_id:
                rows = conn.execute("SELECT * FROM knowledge_audit WHERE item_id=? ORDER BY id DESC LIMIT ?", (item_id, max(1, min(int(limit), 1000)))).fetchall()
            else:
                rows = conn.execute("SELECT * FROM knowledge_audit ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 1000)),)).fetchall()
            out = []
            for r in rows:
                d = dict(r); d["details"] = _json_load(d.pop("details_json", "{}"), {})
                out.append(d)
            return out

    def export_bundle(self, *, owner_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        items = self.list_items(owner_id=owner_id, project_id=project_id, include_global=False if project_id is not None else True, limit=100000)
        return {
            "format": "postmaster-knowledge-v1",
            "exported_at": utcnow(),
            "items": items,
        }

    def import_bundle(self, bundle: dict[str, Any], *, owner_id_override: str | None = None,
                      project_id_override: str | None = None, replace_existing: bool = False,
                      actor: str = "import") -> dict[str, Any]:
        if not isinstance(bundle, dict) or bundle.get("format") != "postmaster-knowledge-v1":
            raise KnowledgeError("Unsupported knowledge bundle format")
        items = bundle.get("items")
        if not isinstance(items, list):
            raise KnowledgeError("Bundle items must be a list")
        created = updated = skipped = 0
        errors: list[dict[str, str]] = []
        for raw in items:
            try:
                if not isinstance(raw, dict):
                    raise KnowledgeError("item must be an object")
                iid = str(raw.get("id") or uuid.uuid4())
                try:
                    self.get_item(iid); exists = True
                except KnowledgeError:
                    exists = False
                owner = owner_id_override or str(raw.get("owner_id") or "")
                project = project_id_override if project_id_override is not None else raw.get("project_id")
                if exists and not replace_existing:
                    skipped += 1; continue
                if exists:
                    self.update_item(iid, title=str(raw.get("title") or ""), content=str(raw.get("content") or ""),
                                     priority=raw.get("priority", 0.5), always_include=bool(raw.get("always_include")),
                                     enabled=bool(raw.get("enabled", True)), tags=raw.get("tags", []), metadata=raw.get("metadata", {}),
                                     owner_id=owner, project_id=project, set_project=True, actor=actor)
                    updated += 1
                else:
                    self.create_item(kind=str(raw.get("kind") or "memory"), owner_id=owner, project_id=project,
                                     title=str(raw.get("title") or ""), content=str(raw.get("content") or ""),
                                     priority=raw.get("priority", 0.5), always_include=bool(raw.get("always_include")),
                                     enabled=bool(raw.get("enabled", True)), tags=raw.get("tags", []), metadata=raw.get("metadata", {}),
                                     actor=actor, item_id=iid)
                    created += 1
            except Exception as exc:
                errors.append({"id": str(raw.get("id", "?")) if isinstance(raw, dict) else "?", "error": str(exc)})
        return {"ok": not errors, "created": created, "updated": updated, "skipped": skipped, "errors": errors[:50]}

    def status(self) -> dict[str, Any]:
        with self._conn() as conn:
            counts = {r["kind"]: int(r["n"]) for r in conn.execute("SELECT kind,COUNT(*) n FROM knowledge_items GROUP BY kind").fetchall()}
            chunks = int(conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0])
            embedded = int(conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE embedding IS NOT NULL").fetchone()[0])
            revisions = int(conn.execute("SELECT COUNT(*) FROM knowledge_revisions").fetchone()[0])
            audit = int(conn.execute("SELECT COUNT(*) FROM knowledge_audit").fetchone()[0])
            tags = int(conn.execute("SELECT COUNT(*) FROM knowledge_tags").fetchone()[0])
        return {
            "ok": True, "db_path": self.db_path, "fts5": True,
            "memories": counts.get("memory", 0), "skills": counts.get("skill", 0),
            "chunks": chunks, "embedded_chunks": embedded, "missing_embeddings": max(0, chunks - embedded),
            "revisions": revisions, "audit_events": audit, "tags": tags,
            "chunk_chars": self.chunk_chars, "chunk_overlap": self.chunk_overlap,
        }
