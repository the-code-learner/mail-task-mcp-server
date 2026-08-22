from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_key(project_id: str | None) -> str:
    return (project_id or "").strip()


class KnowledgeScopeStore:
    """Many-to-many scope relation layered on legacy primary owner/project columns."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.getenv("CONTEXT_DB_PATH", "/data/knowledge.db")
        self._lock = threading.RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_item_scopes (
                    item_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL DEFAULT '',
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(item_id, owner_id, project_id),
                    FOREIGN KEY(item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS ix_knowledge_scope_owner_project
                  ON knowledge_item_scopes(owner_id, project_id, item_id);
                CREATE INDEX IF NOT EXISTS ix_knowledge_scope_item
                  ON knowledge_item_scopes(item_id, is_primary DESC);

                CREATE TABLE IF NOT EXISTS knowledge_scope_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_knowledge_scope_audit_item
                  ON knowledge_scope_audit(item_id, id DESC);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_item_scopes(
                    item_id, owner_id, project_id, is_primary, created_at
                )
                SELECT id, owner_id, COALESCE(project_id,''), 1, COALESCE(created_at, ?)
                FROM knowledge_items
                """,
                (_now(),),
            )
            conn.execute(
                """
                UPDATE knowledge_item_scopes
                SET is_primary=1
                WHERE (item_id,owner_id,project_id) IN (
                    SELECT id,owner_id,COALESCE(project_id,'') FROM knowledge_items
                )
                AND item_id NOT IN (
                    SELECT item_id FROM knowledge_item_scopes WHERE is_primary=1
                )
                """
            )
            conn.commit()

    @staticmethod
    def normalize_scope(scope: dict[str, Any]) -> tuple[str, str]:
        owner = str(scope.get("owner_id") or "").strip()
        project = _project_key(scope.get("project_id"))
        if not owner:
            raise ValueError("scope owner_id is required")
        return owner, project

    def _audit(self, conn: sqlite3.Connection, item_id: str, action: str, details: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO knowledge_scope_audit(item_id,action,details_json,created_at) VALUES(?,?,?,?)",
            (item_id, action, json.dumps(details, ensure_ascii=False, sort_keys=True), _now()),
        )

    def sync_primary(
        self,
        item_id: str,
        *,
        owner_id: str,
        project_id: str | None,
        remove_previous_primary: bool = True,
    ) -> None:
        owner = (owner_id or "").strip()
        project = _project_key(project_id)
        if not owner:
            raise ValueError("primary owner_id is required")
        with self._lock, self._connect() as conn:
            previous = conn.execute(
                "SELECT owner_id,project_id FROM knowledge_item_scopes WHERE item_id=? AND is_primary=1",
                (item_id,),
            ).fetchall()
            if remove_previous_primary:
                conn.execute(
                    "DELETE FROM knowledge_item_scopes WHERE item_id=? AND is_primary=1",
                    (item_id,),
                )
            else:
                conn.execute(
                    "UPDATE knowledge_item_scopes SET is_primary=0 WHERE item_id=?",
                    (item_id,),
                )
            conn.execute(
                """
                INSERT INTO knowledge_item_scopes(item_id,owner_id,project_id,is_primary,created_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(item_id,owner_id,project_id)
                DO UPDATE SET is_primary=1
                """,
                (item_id, owner, project, 1, _now()),
            )
            self._audit(
                conn,
                item_id,
                "primary_scope",
                {
                    "previous": [
                        {"owner_id": str(row["owner_id"]), "project_id": str(row["project_id"]) or None}
                        for row in previous
                    ],
                    "current": {"owner_id": owner, "project_id": project or None},
                },
            )
            conn.commit()

    def set_scopes(
        self,
        item_id: str,
        scopes: Iterable[dict[str, Any]],
        *,
        primary_owner_id: str,
        primary_project_id: str | None,
    ) -> list[dict[str, Any]]:
        primary = ((primary_owner_id or "").strip(), _project_key(primary_project_id))
        if not primary[0]:
            raise ValueError("primary owner_id is required")
        normalized: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for scope in scopes:
            value = self.normalize_scope(scope)
            if value not in seen:
                normalized.append(value)
                seen.add(value)
        if primary not in seen:
            normalized.insert(0, primary)
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM knowledge_item_scopes WHERE item_id=?", (item_id,))
            for owner, project in normalized:
                conn.execute(
                    """
                    INSERT INTO knowledge_item_scopes(item_id,owner_id,project_id,is_primary,created_at)
                    VALUES(?,?,?,?,?)
                    """,
                    (item_id, owner, project, int((owner, project) == primary), _now()),
                )
            self._audit(
                conn,
                item_id,
                "set_scopes",
                {
                    "scopes": [
                        {"owner_id": owner, "project_id": project or None, "is_primary": (owner, project) == primary}
                        for owner, project in normalized
                    ]
                },
            )
            conn.commit()
        return self.scopes_for(item_id)

    def scopes_for(self, item_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT owner_id,project_id,is_primary,created_at
                FROM knowledge_item_scopes
                WHERE item_id=?
                ORDER BY is_primary DESC,owner_id,project_id
                """,
                (item_id,),
            ).fetchall()
        return [
            {
                "owner_id": str(row["owner_id"]),
                "project_id": str(row["project_id"]) or None,
                "is_primary": bool(row["is_primary"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def attach(self, item: dict[str, Any]) -> dict[str, Any]:
        result = dict(item)
        item_id = str(result.get("id") or result.get("item_id") or "")
        result["scopes"] = self.scopes_for(item_id) if item_id else []
        return result

    def attach_many(self, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.attach(item) for item in items]

    def item_ids_for(
        self,
        *,
        owner_id: str | None = None,
        project_ids: list[str] | None = None,
        include_global: bool = True,
    ) -> set[str]:
        where: list[str] = []
        args: list[Any] = []
        if owner_id:
            where.append("owner_id=?")
            args.append(owner_id)
        cleaned = list(dict.fromkeys(_project_key(value) for value in (project_ids or [])))
        if cleaned:
            placeholders = ",".join("?" for _ in cleaned)
            if include_global and "" not in cleaned:
                where.append(f"(project_id IN ({placeholders}) OR project_id='')")
                args.extend(cleaned)
            else:
                where.append(f"project_id IN ({placeholders})")
                args.extend(cleaned)
        elif not include_global:
            where.append("project_id<>''")
        sql = "SELECT DISTINCT item_id FROM knowledge_item_scopes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._connect() as conn:
            return {str(row["item_id"]) for row in conn.execute(sql, args).fetchall()}

    def has_scope(
        self,
        item_id: str,
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
        include_global: bool = True,
    ) -> bool:
        where = ["item_id=?"]
        args: list[Any] = [item_id]
        if owner_id:
            where.append("owner_id=?")
            args.append(owner_id)
        if project_id is not None:
            if include_global:
                where.append("(project_id=? OR project_id='')")
            else:
                where.append("project_id=?")
            args.append(_project_key(project_id))
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM knowledge_item_scopes WHERE " + " AND ".join(where) + " LIMIT 1",
                args,
            ).fetchone()
        return row is not None

    def audit(self, item_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id,action,details_json,created_at
                FROM knowledge_scope_audit WHERE item_id=?
                ORDER BY id DESC LIMIT ?
                """,
                (item_id, max(1, min(int(limit), 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(str(item.pop("details_json")))
            except Exception:
                item["details"] = {}
            result.append(item)
        return result
