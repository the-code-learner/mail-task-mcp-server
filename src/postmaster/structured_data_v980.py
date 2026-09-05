from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class StructuredDataError(RuntimeError):
    pass


class StructuredDataScopeError(StructuredDataError):
    pass


class StructuredDataApprovalRequired(StructuredDataError):
    pass


_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_SQL_FORBIDDEN_RE = re.compile(
    r"\b(pragma|attach|detach|vacuum|reindex|analyze|insert|update|delete|replace|"
    r"create|alter|drop|truncate|grant|revoke|load_extension)\b",
    re.IGNORECASE,
)
_SQL_TABLE_RE = re.compile(
    r"\b(?:from|join)\s+(?P<name>[A-Za-z][A-Za-z0-9_]{0,62})\b",
    re.IGNORECASE,
)
_ALLOWED_TYPES = {
    "text": "TEXT",
    "string": "TEXT",
    "integer": "INTEGER",
    "int": "INTEGER",
    "real": "REAL",
    "float": "REAL",
    "number": "REAL",
    "boolean": "INTEGER",
    "bool": "INTEGER",
    "json": "TEXT",
    "datetime": "TEXT",
    "date": "TEXT",
    "blob": "BLOB",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_json(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _ident(value: str, *, kind: str = "identifier") -> str:
    clean = str(value or "").strip()
    if not _IDENT_RE.fullmatch(clean):
        raise StructuredDataError(
            f"{kind} must start with a letter and contain only letters, numbers or underscores "
            "(max 63 characters)"
        )
    if clean.lower().startswith(("sqlite_", "sd_", "pm_")):
        raise StructuredDataError(f"{kind} uses a reserved prefix")
    return clean


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class _ClosingConnection(sqlite3.Connection):
    """sqlite3 context manager that also closes the connection on scope exit."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class StructuredDataService:
    """Project-scoped operational facts behind one server-enforced domain contract.

    v9.8.0 deliberately defaults to the existing durable /data volume so the capability can ship
    without mutating the approved deployment YAML. Logical names are the only names accepted by
    callers; physical table names are opaque and derived from owner + project + logical table.
    """

    backend_name = "sqlite"
    capability_version = "9.8.0"

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        project_resolver: Callable[[str, str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        configured = str(
            path
            or os.getenv("POSTMASTER_STRUCTURED_DATA_DB")
            or "/data/structured-data-v980.db"
        )
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._project_resolver = project_resolver
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def _tx(self):
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sd_tables(
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    logical_name TEXT NOT NULL,
                    physical_name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    source_of_truth TEXT NOT NULL DEFAULT 'operational',
                    agent_instructions TEXT NOT NULL DEFAULT '',
                    primary_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id, project_id, logical_name)
                );
                CREATE INDEX IF NOT EXISTS sd_tables_scope
                    ON sd_tables(owner_id, project_id, logical_name);

                CREATE TABLE IF NOT EXISTS sd_columns(
                    table_id TEXT NOT NULL REFERENCES sd_tables(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 0,
                    unique_flag INTEGER NOT NULL DEFAULT 0,
                    primary_key INTEGER NOT NULL DEFAULT 0,
                    default_json TEXT,
                    description TEXT NOT NULL DEFAULT '',
                    allowed_values_json TEXT,
                    ordinal INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(table_id, name)
                );

                CREATE TABLE IF NOT EXISTS sd_migrations(
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    operations_json TEXT NOT NULL,
                    rollback_json TEXT NOT NULL DEFAULT '[]',
                    destructive INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    applied_at TEXT,
                    rolled_back_at TEXT,
                    UNIQUE(owner_id, project_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS sd_migrations_scope
                    ON sd_migrations(owner_id, project_id, sequence DESC);

                CREATE TABLE IF NOT EXISTS sd_audit(
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    table_name TEXT,
                    row_key TEXT,
                    operation TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    actor TEXT NOT NULL,
                    source TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    memory_id TEXT,
                    migration_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sd_audit_scope
                    ON sd_audit(owner_id, project_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS sd_idempotency(
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(owner_id, project_id, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS sd_overrides(
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    row_key TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    value_json TEXT,
                    priority INTEGER NOT NULL DEFAULT 100,
                    reason TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    effective_from TEXT,
                    expires_at TEXT,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sd_overrides_scope
                    ON sd_overrides(owner_id, project_id, table_name, row_key, enabled, priority DESC);

                CREATE TABLE IF NOT EXISTS sd_memory_links(
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    row_key TEXT,
                    memory_id TEXT NOT NULL,
                    relation TEXT NOT NULL DEFAULT 'rationale',
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sd_memory_links_scope
                    ON sd_memory_links(owner_id, project_id, table_name, row_key);

                CREATE TABLE IF NOT EXISTS sd_views(
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    query_json TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id, project_id, name)
                );
                """
            )

    def _scope(self, owner_id: str, project_id: str) -> tuple[str, str]:
        owner = str(owner_id or "").strip()
        project = str(project_id or "").strip()
        if not owner or not project:
            raise StructuredDataScopeError("owner_id and project_id are required")
        if self._project_resolver is not None:
            resolved = self._project_resolver(owner, project)
            if not resolved:
                raise StructuredDataScopeError("unknown or inactive project scope")
            if str(resolved.get("owner_id") or "") != owner or str(resolved.get("id") or "") != project:
                raise StructuredDataScopeError("project scope mismatch")
        return owner, project

    @staticmethod
    def _physical_name(owner: str, project: str, logical: str) -> str:
        digest = hashlib.sha256(f"{owner}\0{project}\0{logical}".encode("utf-8")).hexdigest()[:28]
        return "pm_" + digest

    def _table(
        self,
        conn: sqlite3.Connection,
        owner: str,
        project: str,
        logical: str,
    ) -> sqlite3.Row:
        name = _ident(logical, kind="table name")
        row = conn.execute(
            "SELECT * FROM sd_tables WHERE owner_id=? AND project_id=? AND logical_name=?",
            (owner, project, name),
        ).fetchone()
        if not row:
            raise StructuredDataError(f"unknown table in this project: {name}")
        return row

    @staticmethod
    def _columns(conn: sqlite3.Connection, table_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM sd_columns WHERE table_id=? ORDER BY ordinal,name",
            (table_id,),
        ).fetchall()

    def _column_map(self, conn: sqlite3.Connection, table_id: str) -> dict[str, sqlite3.Row]:
        return {str(row["name"]): row for row in self._columns(conn, table_id)}

    def _audit(
        self,
        conn: sqlite3.Connection,
        *,
        owner: str,
        project: str,
        operation: str,
        actor: str,
        source: str,
        table_name: str | None = None,
        row_key: str | None = None,
        before: Any = None,
        after: Any = None,
        reason: str = "",
        memory_id: str | None = None,
        migration_id: str | None = None,
    ) -> str:
        audit_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO sd_audit(
                id,owner_id,project_id,table_name,row_key,operation,before_json,after_json,
                actor,source,reason,memory_id,migration_id,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                audit_id,
                owner,
                project,
                table_name,
                row_key,
                operation,
                None if before is None else _json(before),
                None if after is None else _json(after),
                str(actor or "assistant"),
                str(source or "mcp"),
                str(reason or ""),
                memory_id,
                migration_id,
                _now(),
            ),
        )
        return audit_id

    def _idempotent_get(
        self,
        conn: sqlite3.Connection,
        owner: str,
        project: str,
        key: str | None,
        operation: str,
    ) -> dict[str, Any] | None:
        if not key:
            return None
        row = conn.execute(
            """
            SELECT operation,result_json FROM sd_idempotency
            WHERE owner_id=? AND project_id=? AND idempotency_key=?
            """,
            (owner, project, str(key)),
        ).fetchone()
        if not row:
            return None
        if str(row["operation"]) != operation:
            raise StructuredDataError("idempotency key was already used for a different operation")
        result = _decode_json(row["result_json"], {})
        if isinstance(result, dict):
            result["idempotent_replay"] = True
        return result

    @staticmethod
    def _idempotent_put(
        conn: sqlite3.Connection,
        owner: str,
        project: str,
        key: str | None,
        operation: str,
        result: Mapping[str, Any],
    ) -> None:
        if not key:
            return
        conn.execute(
            """
            INSERT INTO sd_idempotency(
                owner_id,project_id,idempotency_key,operation,result_json,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (owner, project, str(key), operation, _json(dict(result)), _now()),
        )

    def status(self, owner_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        where = ""
        params: list[Any] = []
        if owner_id is not None or project_id is not None:
            owner, project = self._scope(str(owner_id or ""), str(project_id or ""))
            where = " WHERE owner_id=? AND project_id=?"
            params = [owner, project]
        with self._connect() as conn:
            tables = int(conn.execute("SELECT COUNT(*) FROM sd_tables" + where, params).fetchone()[0])
            migrations = int(conn.execute("SELECT COUNT(*) FROM sd_migrations" + where, params).fetchone()[0])
            audits = int(conn.execute("SELECT COUNT(*) FROM sd_audit" + where, params).fetchone()[0])
        return {
            "ok": True,
            "capability_version": self.capability_version,
            "backend": self.backend_name,
            "project_scoped": True,
            "physical_namespace_hidden": True,
            "raw_sql": "validated-read-only",
            "destructive_schema_requires_confirmation": True,
            "tables": tables,
            "migrations": migrations,
            "audit_events": audits,
        }

    def describe_project(self, owner_id: str, project_id: str) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        tables: list[dict[str, Any]] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT t.*,
                       (SELECT COUNT(*) FROM sd_columns c WHERE c.table_id=t.id) AS column_count
                FROM sd_tables t
                WHERE t.owner_id=? AND t.project_id=?
                ORDER BY t.logical_name
                """,
                (owner, project),
            ).fetchall()
            for row in rows:
                row_count = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {_q(str(row['physical_name']))}"
                    ).fetchone()[0]
                )
                tables.append(
                    {
                        "name": row["logical_name"],
                        "description": row["description"],
                        "source_of_truth": row["source_of_truth"],
                        "primary_key": row["primary_key"],
                        "column_count": int(row["column_count"]),
                        "row_count": row_count,
                        "updated_at": row["updated_at"],
                    }
                )
            migration_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM sd_migrations WHERE owner_id=? AND project_id=?",
                    (owner, project),
                ).fetchone()[0]
            )
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "tables": tables,
            "migration_count": migration_count,
        }

    def describe_table(self, owner_id: str, project_id: str, table: str) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        with self._connect() as conn:
            meta = self._table(conn, owner, project, table)
            columns = self._columns(conn, str(meta["id"]))
            indexes = conn.execute(
                f"PRAGMA index_list({_q(str(meta['physical_name']))})"
            ).fetchall()
            links = conn.execute(
                """
                SELECT id,row_key,memory_id,relation,actor,created_at
                FROM sd_memory_links
                WHERE owner_id=? AND project_id=? AND table_name=?
                ORDER BY created_at DESC LIMIT 100
                """,
                (owner, project, str(meta["logical_name"])),
            ).fetchall()
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "table": str(meta["logical_name"]),
            "description": str(meta["description"]),
            "source_of_truth": str(meta["source_of_truth"]),
            "agent_instructions": str(meta["agent_instructions"]),
            "primary_key": meta["primary_key"],
            "columns": [
                {
                    "name": row["name"],
                    "data_type": row["data_type"],
                    "required": bool(row["required"]),
                    "unique": bool(row["unique_flag"]),
                    "primary_key": bool(row["primary_key"]),
                    "default": _decode_json(row["default_json"]),
                    "description": row["description"],
                    "allowed_values": _decode_json(row["allowed_values_json"], []),
                    "ordinal": int(row["ordinal"]),
                }
                for row in columns
            ],
            "indexes": [
                {"name": row["name"], "unique": bool(row["unique"])}
                for row in indexes
                if not str(row["name"]).startswith("sqlite_")
            ],
            "memory_links": [dict(row) for row in links],
        }

    @staticmethod
    def _column_sql(
        spec: Mapping[str, Any],
        *,
        for_alter: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        name = _ident(str(spec.get("name") or ""), kind="column name")
        data_type = str(spec.get("type") or spec.get("data_type") or "text").lower()
        if data_type not in _ALLOWED_TYPES:
            raise StructuredDataError(f"unsupported data type: {data_type}")
        required = bool(spec.get("required", False))
        unique = bool(spec.get("unique", False))
        primary = bool(spec.get("primary_key", False))
        default = spec.get("default")
        allowed_values = spec.get("allowed_values") or []
        if allowed_values and not isinstance(allowed_values, list):
            raise StructuredDataError("allowed_values must be a list")
        if for_alter and (primary or unique):
            raise StructuredDataError(
                "add_column does not add primary/unique constraints; create an index separately"
            )
        parts = [_q(name), _ALLOWED_TYPES[data_type]]
        if required and default is None and for_alter:
            raise StructuredDataError("a required added column needs a default")
        if required:
            parts.append("NOT NULL")
        if unique:
            parts.append("UNIQUE")
        if primary:
            parts.append("PRIMARY KEY")
        if default is not None:
            parts.append("DEFAULT " + StructuredDataService._literal(default))
        normalized = {
            "name": name,
            "type": data_type,
            "required": required,
            "unique": unique,
            "primary_key": primary,
            "default": default,
            "description": str(spec.get("description") or ""),
            "allowed_values": list(allowed_values),
        }
        return " ".join(parts), normalized

    @staticmethod
    def _literal(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    def create_table(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        columns: Sequence[Mapping[str, Any]],
        *,
        description: str = "",
        source_of_truth: str = "operational",
        agent_instructions: str = "",
        actor: str = "assistant",
        source: str = "mcp",
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        if not columns:
            raise StructuredDataError("at least one column is required")
        operation = f"create_table:{logical}"
        with self._tx() as conn:
            replay = self._idempotent_get(conn, owner, project, idempotency_key, operation)
            if replay is not None:
                return replay
            exists = conn.execute(
                "SELECT 1 FROM sd_tables WHERE owner_id=? AND project_id=? AND logical_name=?",
                (owner, project, logical),
            ).fetchone()
            if exists:
                raise StructuredDataError(f"table already exists in this project: {logical}")
            column_sql: list[str] = []
            normalized: list[dict[str, Any]] = []
            seen: set[str] = set()
            primary_names: list[str] = []
            for spec in columns:
                sql, item = self._column_sql(spec)
                if item["name"] in seen:
                    raise StructuredDataError(f"duplicate column: {item['name']}")
                seen.add(item["name"])
                if item["primary_key"]:
                    primary_names.append(item["name"])
                column_sql.append(sql)
                normalized.append(item)
            if len(primary_names) > 1:
                raise StructuredDataError("v9.8.0 supports one declared primary key column")
            physical = self._physical_name(owner, project, logical)
            conn.execute(
                f"CREATE TABLE {_q(physical)} "
                f"({','.join(column_sql)}, {_q('__pm_row_id')} TEXT NOT NULL UNIQUE)"
            )
            table_id = str(uuid.uuid4())
            now = _now()
            conn.execute(
                """
                INSERT INTO sd_tables(
                    id,owner_id,project_id,logical_name,physical_name,description,
                    source_of_truth,agent_instructions,primary_key,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    table_id,
                    owner,
                    project,
                    logical,
                    physical,
                    str(description or ""),
                    str(source_of_truth or "operational"),
                    str(agent_instructions or ""),
                    primary_names[0] if primary_names else None,
                    now,
                    now,
                ),
            )
            for ordinal, item in enumerate(normalized):
                conn.execute(
                    """
                    INSERT INTO sd_columns(
                        table_id,name,data_type,required,unique_flag,primary_key,default_json,
                        description,allowed_values_json,ordinal,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        table_id,
                        item["name"],
                        item["type"],
                        1 if item["required"] else 0,
                        1 if item["unique"] else 0,
                        1 if item["primary_key"] else 0,
                        None if item["default"] is None else _json(item["default"]),
                        item["description"],
                        _json(item["allowed_values"]) if item["allowed_values"] else None,
                        ordinal,
                        now,
                    ),
                )
            result = {
                "ok": True,
                "owner_id": owner,
                "project_id": project,
                "table": logical,
                "columns": normalized,
            }
            self._audit(
                conn,
                owner=owner,
                project=project,
                table_name=logical,
                operation="table.create",
                actor=actor,
                source=source,
                after=result,
                reason=reason,
            )
            self._idempotent_put(conn, owner, project, idempotency_key, operation, result)
            return result

    def alter_table(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        *,
        action: str,
        column: Mapping[str, Any] | None = None,
        description: str | None = None,
        source_of_truth: str | None = None,
        agent_instructions: str | None = None,
        actor: str = "assistant",
        source: str = "mcp",
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        normalized_action = str(action or "").lower()
        operation = f"alter_table:{logical}:{normalized_action}"
        with self._tx() as conn:
            replay = self._idempotent_get(conn, owner, project, idempotency_key, operation)
            if replay is not None:
                return replay
            meta = self._table(conn, owner, project, logical)
            if normalized_action == "add_column":
                if not column:
                    raise StructuredDataError("column is required")
                sql, item = self._column_sql(column, for_alter=True)
                cmap = self._column_map(conn, str(meta["id"]))
                if item["name"] in cmap:
                    raise StructuredDataError(f"column already exists: {item['name']}")
                conn.execute(f"ALTER TABLE {_q(str(meta['physical_name']))} ADD COLUMN {sql}")
                ordinal = max([int(row["ordinal"]) for row in cmap.values()] or [-1]) + 1
                conn.execute(
                    """
                    INSERT INTO sd_columns(
                        table_id,name,data_type,required,unique_flag,primary_key,default_json,
                        description,allowed_values_json,ordinal,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        meta["id"],
                        item["name"],
                        item["type"],
                        1 if item["required"] else 0,
                        0,
                        0,
                        None if item["default"] is None else _json(item["default"]),
                        item["description"],
                        _json(item["allowed_values"]) if item["allowed_values"] else None,
                        ordinal,
                        _now(),
                    ),
                )
                detail: dict[str, Any] = {"column": item}
            elif normalized_action == "update_metadata":
                assignments: list[str] = []
                params: list[Any] = []
                for key, value in (
                    ("description", description),
                    ("source_of_truth", source_of_truth),
                    ("agent_instructions", agent_instructions),
                ):
                    if value is not None:
                        assignments.append(key + "=?")
                        params.append(str(value))
                if not assignments:
                    raise StructuredDataError("no metadata changes supplied")
                assignments.append("updated_at=?")
                params.append(_now())
                params.extend([owner, project, logical])
                conn.execute(
                    "UPDATE sd_tables SET " + ",".join(assignments) +
                    " WHERE owner_id=? AND project_id=? AND logical_name=?",
                    params,
                )
                detail = {
                    "description": description,
                    "source_of_truth": source_of_truth,
                    "agent_instructions": agent_instructions,
                }
            else:
                raise StructuredDataError(
                    "v9.8.0 alter_table supports add_column or update_metadata; destructive DDL requires a migration review"
                )
            result = {
                "ok": True,
                "owner_id": owner,
                "project_id": project,
                "table": logical,
                "action": normalized_action,
                **detail,
            }
            self._audit(
                conn,
                owner=owner,
                project=project,
                table_name=logical,
                operation="table.alter",
                actor=actor,
                source=source,
                after=result,
                reason=reason,
            )
            self._idempotent_put(conn, owner, project, idempotency_key, operation, result)
            return result

    @staticmethod
    def _storage_value(column: sqlite3.Row | None, value: Any) -> Any:
        if value is None or column is None:
            return value
        kind = str(column["data_type"])
        if kind == "json":
            return _json(value)
        if kind in {"boolean", "bool"}:
            return 1 if bool(value) else 0
        if kind in {"integer", "int"}:
            return int(value)
        if kind in {"real", "float", "number"}:
            return float(value)
        if kind == "blob" and isinstance(value, str):
            return value.encode("utf-8")
        return value

    @staticmethod
    def _output_value(column: sqlite3.Row | None, value: Any) -> Any:
        if value is None or column is None:
            return value
        kind = str(column["data_type"])
        if kind == "json":
            return _decode_json(value, value)
        if kind in {"boolean", "bool"}:
            return bool(value)
        return value

    def _row_to_output(
        self,
        row: sqlite3.Row,
        columns: Mapping[str, sqlite3.Row],
    ) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key in row.keys():
            if key == "__pm_row_id":
                output["_row_id"] = row[key]
            else:
                output[key] = self._output_value(columns.get(key), row[key])
        return output

    def insert(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        values: Mapping[str, Any],
        *,
        actor: str = "assistant",
        source: str = "mcp",
        reason: str = "",
        memory_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        operation = f"insert:{logical}"
        with self._tx() as conn:
            replay = self._idempotent_get(conn, owner, project, idempotency_key, operation)
            if replay is not None:
                return replay
            meta = self._table(conn, owner, project, logical)
            columns = self._column_map(conn, str(meta["id"]))
            unknown = set(values) - set(columns)
            if unknown:
                raise StructuredDataError(f"unknown columns: {sorted(unknown)}")
            payload: dict[str, Any] = {}
            for name, column in columns.items():
                if name in values:
                    value = values[name]
                elif column["default_json"] is not None:
                    value = _decode_json(column["default_json"])
                elif bool(column["required"]):
                    raise StructuredDataError(f"missing required column: {name}")
                else:
                    continue
                allowed = _decode_json(column["allowed_values_json"], []) or []
                if allowed and value not in allowed:
                    raise StructuredDataError(f"value for {name} is outside allowed_values")
                payload[name] = self._storage_value(column, value)
            row_id = str(uuid.uuid4())
            names = list(payload) + ["__pm_row_id"]
            conn.execute(
                f"INSERT INTO {_q(str(meta['physical_name']))} "
                f"({','.join(_q(name) for name in names)}) "
                f"VALUES({','.join('?' for _ in names)})",
                [payload[name] for name in payload] + [row_id],
            )
            result = {
                "ok": True,
                "owner_id": owner,
                "project_id": project,
                "table": logical,
                "row_id": row_id,
                "row": dict(values),
            }
            self._audit(
                conn,
                owner=owner,
                project=project,
                table_name=logical,
                row_key=row_id,
                operation="row.insert",
                actor=actor,
                source=source,
                after=dict(values),
                reason=reason,
                memory_id=memory_id,
            )
            self._idempotent_put(conn, owner, project, idempotency_key, operation, result)
            return result

    def _where(
        self,
        columns: Mapping[str, sqlite3.Row],
        filters: Mapping[str, Any] | None,
        *,
        prefix: str | None = None,
    ) -> tuple[str, list[Any]]:
        if not filters:
            return "", []
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in filters.items():
            name = str(key)
            if name == "_row_id":
                sql_name = "__pm_row_id"
                column = None
            else:
                if name not in columns:
                    raise StructuredDataError(f"unknown filter column: {name}")
                sql_name = name
                column = columns[name]
            lhs = (f"{_q(prefix)}." if prefix else "") + _q(sql_name)
            if isinstance(value, Mapping):
                for op, operand in value.items():
                    normalized = str(op).lower()
                    if normalized in {"eq", "="}:
                        clauses.append(lhs + "=?")
                        params.append(self._storage_value(column, operand))
                    elif normalized in {"ne", "!=", "<>"}:
                        clauses.append(lhs + "<>?")
                        params.append(self._storage_value(column, operand))
                    elif normalized in {"gt", ">"}:
                        clauses.append(lhs + ">?")
                        params.append(self._storage_value(column, operand))
                    elif normalized in {"gte", ">="}:
                        clauses.append(lhs + ">=?")
                        params.append(self._storage_value(column, operand))
                    elif normalized in {"lt", "<"}:
                        clauses.append(lhs + "<?")
                        params.append(self._storage_value(column, operand))
                    elif normalized in {"lte", "<="}:
                        clauses.append(lhs + "<=?")
                        params.append(self._storage_value(column, operand))
                    elif normalized == "like":
                        clauses.append(lhs + " LIKE ?")
                        params.append(str(operand))
                    elif normalized == "in":
                        sequence = list(operand or [])
                        if not sequence:
                            clauses.append("0=1")
                        else:
                            clauses.append(lhs + " IN (" + ",".join("?" for _ in sequence) + ")")
                            params.extend(self._storage_value(column, item) for item in sequence)
                    elif normalized == "is_null":
                        clauses.append(lhs + (" IS NULL" if operand else " IS NOT NULL"))
                    else:
                        raise StructuredDataError(f"unsupported filter operator: {op}")
            elif value is None:
                clauses.append(lhs + " IS NULL")
            else:
                clauses.append(lhs + "=?")
                params.append(self._storage_value(column, value))
        return " WHERE " + " AND ".join(clauses), params

    def _apply_overrides(
        self,
        conn: sqlite3.Connection,
        owner: str,
        project: str,
        table: str,
        rows: list[dict[str, Any]],
    ) -> None:
        by_key = {str(row.get("_row_id") or ""): row for row in rows}
        if not by_key:
            return
        now = _now()
        placeholders = ",".join("?" for _ in by_key)
        override_rows = conn.execute(
            f"""
            SELECT * FROM sd_overrides
            WHERE owner_id=? AND project_id=? AND table_name=? AND enabled=1
              AND row_key IN ({placeholders})
              AND (effective_from IS NULL OR effective_from<=?)
              AND (expires_at IS NULL OR expires_at>?)
            ORDER BY row_key,field_name,priority DESC,created_at DESC
            """,
            [owner, project, table, *by_key.keys(), now, now],
        ).fetchall()
        selected: set[tuple[str, str]] = set()
        for override in override_rows:
            key = (str(override["row_key"]), str(override["field_name"]))
            if key in selected or key[0] not in by_key:
                continue
            selected.add(key)
            row = by_key[key[0]]
            raw_value = row.get(key[1])
            effective_value = _decode_json(override["value_json"])
            row[key[1]] = effective_value
            row.setdefault("_overrides", []).append(
                {
                    "id": override["id"],
                    "field": key[1],
                    "value": effective_value,
                    "raw_value": raw_value,
                    "priority": int(override["priority"]),
                    "reason": override["reason"],
                }
            )

    def query(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        *,
        columns: Sequence[str] | None = None,
        filters: Mapping[str, Any] | None = None,
        sort: Sequence[Mapping[str, Any] | str] | None = None,
        limit: int = 100,
        offset: int = 0,
        effective: bool = True,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        limit_i = max(1, min(int(limit), 10000))
        offset_i = max(0, int(offset))
        with self._connect() as conn:
            meta = self._table(conn, owner, project, logical)
            cmap = self._column_map(conn, str(meta["id"]))
            selected = list(columns or cmap.keys())
            for name in selected:
                if name != "_row_id" and name not in cmap:
                    raise StructuredDataError(f"unknown query column: {name}")
            sql_columns = [
                _q("__pm_row_id") if name == "_row_id" else _q(name)
                for name in selected
            ]
            if "_row_id" not in selected:
                sql_columns.append(_q("__pm_row_id"))
            where_sql, params = self._where(cmap, filters)
            order_parts: list[str] = []
            for item in sort or []:
                if isinstance(item, str):
                    name = item.lstrip("-")
                    direction = "DESC" if item.startswith("-") else "ASC"
                else:
                    name = str(item.get("column") or item.get("field") or "")
                    direction = "DESC" if str(item.get("direction") or "asc").lower() == "desc" else "ASC"
                if name == "_row_id":
                    sql_name = "__pm_row_id"
                elif name in cmap:
                    sql_name = name
                else:
                    raise StructuredDataError(f"unknown sort column: {name}")
                order_parts.append(_q(sql_name) + " " + direction)
            order_sql = " ORDER BY " + ",".join(order_parts) if order_parts else ""
            sql = (
                f"SELECT {','.join(sql_columns)} FROM {_q(str(meta['physical_name']))}"
                + where_sql
                + order_sql
                + " LIMIT ? OFFSET ?"
            )
            rows = [
                self._row_to_output(row, cmap)
                for row in conn.execute(sql, [*params, limit_i, offset_i]).fetchall()
            ]
            if effective:
                self._apply_overrides(conn, owner, project, logical, rows)
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "table": logical,
            "rows": rows,
            "count": len(rows),
            "limit": limit_i,
            "offset": offset_i,
            "effective": bool(effective),
        }

    @staticmethod
    def _qualified(
        value: str,
        cmaps: Mapping[str, Mapping[str, sqlite3.Row]],
    ) -> tuple[str, str]:
        if "." not in value:
            raise StructuredDataError(f"qualified column required (table.column): {value}")
        table, column = value.split(".", 1)
        if table not in cmaps or column not in cmaps[table]:
            raise StructuredDataError(f"unknown qualified column: {value}")
        return table, column

    def query_join(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        *,
        joins: Sequence[Mapping[str, Any]],
        columns: Sequence[str],
        filters: Mapping[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        base_name = _ident(table, kind="table name")
        if not joins:
            raise StructuredDataError("at least one join is required")
        limit_i = max(1, min(int(limit), 1000))
        offset_i = max(0, int(offset))
        with self._connect() as conn:
            metas: dict[str, sqlite3.Row] = {base_name: self._table(conn, owner, project, base_name)}
            for join in joins:
                name = _ident(str(join.get("table") or ""), kind="join table")
                if name in metas:
                    raise StructuredDataError(f"duplicate join table: {name}")
                metas[name] = self._table(conn, owner, project, name)
            cmaps = {name: self._column_map(conn, str(meta["id"])) for name, meta in metas.items()}
            aliases: list[tuple[str, str, str]] = []
            select_parts: list[str] = []
            for index, value in enumerate(columns):
                table_name, column_name = self._qualified(str(value), cmaps)
                alias = f"c{index}"
                aliases.append((alias, table_name, column_name))
                select_parts.append(f"{_q(table_name)}.{_q(column_name)} AS {_q(alias)}")
            from_sql = f" FROM {_q(str(metas[base_name]['physical_name']))} AS {_q(base_name)}"
            join_sql: list[str] = []
            for join in joins:
                join_name = str(join.get("table") or "")
                left = str(join.get("left") or "")
                right = str(join.get("right") or "")
                left_table, left_column = self._qualified(left, cmaps)
                right_table, right_column = self._qualified(right, cmaps)
                if join_name not in {left_table, right_table}:
                    raise StructuredDataError("each join condition must reference the joined table")
                join_sql.append(
                    f" JOIN {_q(str(metas[join_name]['physical_name']))} AS {_q(join_name)}"
                    f" ON {_q(left_table)}.{_q(left_column)}={_q(right_table)}.{_q(right_column)}"
                )
            filter_clauses: list[str] = []
            filter_params: list[Any] = []
            for key, value in (filters or {}).items():
                table_name, column_name = self._qualified(str(key), cmaps)
                filter_clauses.append(f"{_q(table_name)}.{_q(column_name)}=?")
                filter_params.append(self._storage_value(cmaps[table_name][column_name], value))
            where_sql = " WHERE " + " AND ".join(filter_clauses) if filter_clauses else ""
            sql = (
                "SELECT " + ",".join(select_parts) + from_sql + "".join(join_sql) + where_sql + " LIMIT ? OFFSET ?"
            )
            fetched = conn.execute(sql, [*filter_params, limit_i, offset_i]).fetchall()
            output: list[dict[str, Any]] = []
            for row in fetched:
                item: dict[str, Any] = {}
                for alias, table_name, column_name in aliases:
                    item[f"{table_name}.{column_name}"] = self._output_value(
                        cmaps[table_name][column_name], row[alias]
                    )
                output.append(item)
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "table": base_name,
            "rows": output,
            "count": len(output),
            "limit": limit_i,
            "offset": offset_i,
            "join_count": len(joins),
        }

    def query_sql_readonly(
        self,
        owner_id: str,
        project_id: str,
        sql: str,
        *,
        params: Sequence[Any] | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        raw = str(sql or "").strip()
        if not raw:
            raise StructuredDataError("SQL is required")
        if ";" in raw.rstrip(";"):
            raise StructuredDataError("multiple SQL statements are not allowed")
        raw = raw.rstrip(";").strip()
        if not re.match(r"^(select|with)\b", raw, re.IGNORECASE):
            raise StructuredDataError("raw SQL is read-only: SELECT/WITH only")
        if _SQL_FORBIDDEN_RE.search(raw):
            raise StructuredDataError("raw SQL contains a forbidden operation")
        if re.search(r"\bsqlite_", raw, re.IGNORECASE):
            raise StructuredDataError("system schemas are not accessible")
        referenced = [match.group("name") for match in _SQL_TABLE_RE.finditer(raw)]
        if not referenced:
            raise StructuredDataError("SQL must reference at least one project table")
        cte_aliases = {
            match.group("name").casefold()
            for match in re.finditer(
                r"(?:\bwith\b|,)\s*(?P<name>[A-Za-z][A-Za-z0-9_]{0,62})\s+as\s*\(",
                raw,
                re.IGNORECASE,
            )
        }
        with self._connect() as conn:
            replacements: dict[str, str] = {}
            for logical in referenced:
                if logical.casefold() in cte_aliases:
                    continue
                valid = _ident(logical, kind="table name")
                meta = self._table(conn, owner, project, valid)
                replacements[valid.casefold()] = str(meta["physical_name"])
            if not replacements:
                raise StructuredDataError("SQL must reference at least one project table")

            def replace_table(match: re.Match[str]) -> str:
                logical = match.group("name")
                physical = replacements.get(logical.casefold())
                if physical is None:
                    return match.group(0)
                prefix = match.group(0)[: match.start("name") - match.start()]
                return prefix + _q(physical)

            rewritten = _SQL_TABLE_RE.sub(replace_table, raw)
            limit_i = max(1, min(int(limit), 1000))
            wrapped = f"SELECT * FROM ({rewritten}) AS {_q('pm_readonly')} LIMIT {limit_i}"
            rows = [dict(row) for row in conn.execute(wrapped, list(params or [])).fetchall()]
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "rows": rows,
            "count": len(rows),
            "limit": limit_i,
            "read_only": True,
        }

    def _matching_rows(
        self,
        conn: sqlite3.Connection,
        meta: sqlite3.Row,
        cmap: Mapping[str, sqlite3.Row],
        filters: Mapping[str, Any],
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if not filters:
            raise StructuredDataError("mutations require explicit filters")
        where_sql, params = self._where(cmap, filters)
        rows = conn.execute(
            f"SELECT * FROM {_q(str(meta['physical_name']))}{where_sql} LIMIT ?",
            [*params, max(1, min(int(limit), 1000))],
        ).fetchall()
        return [self._row_to_output(row, cmap) for row in rows]

    def update(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        filters: Mapping[str, Any],
        values: Mapping[str, Any],
        *,
        actor: str = "assistant",
        source: str = "mcp",
        reason: str = "",
        memory_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        if not values:
            raise StructuredDataError("values are required")
        operation = f"update:{logical}"
        with self._tx() as conn:
            replay = self._idempotent_get(conn, owner, project, idempotency_key, operation)
            if replay is not None:
                return replay
            meta = self._table(conn, owner, project, logical)
            cmap = self._column_map(conn, str(meta["id"]))
            unknown = set(values) - set(cmap)
            if unknown:
                raise StructuredDataError(f"unknown columns: {sorted(unknown)}")
            before_rows = self._matching_rows(conn, meta, cmap, filters)
            if not before_rows:
                result = {"ok": True, "owner_id": owner, "project_id": project, "table": logical, "updated": 0}
                self._idempotent_put(conn, owner, project, idempotency_key, operation, result)
                return result
            assignments = ",".join(f"{_q(name)}=?" for name in values)
            where_sql, params = self._where(cmap, filters)
            stored = [self._storage_value(cmap[name], values[name]) for name in values]
            cur = conn.execute(
                f"UPDATE {_q(str(meta['physical_name']))} SET {assignments}{where_sql}",
                [*stored, *params],
            )
            for row in before_rows:
                self._audit(
                    conn,
                    owner=owner,
                    project=project,
                    table_name=logical,
                    row_key=str(row.get("_row_id") or ""),
                    operation="row.update",
                    actor=actor,
                    source=source,
                    before=row,
                    after={**row, **dict(values)},
                    reason=reason,
                    memory_id=memory_id,
                )
            result = {
                "ok": True,
                "owner_id": owner,
                "project_id": project,
                "table": logical,
                "updated": int(cur.rowcount),
            }
            self._idempotent_put(conn, owner, project, idempotency_key, operation, result)
            return result

    def delete(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        filters: Mapping[str, Any],
        *,
        confirm: bool = False,
        actor: str = "assistant",
        source: str = "mcp",
        reason: str = "",
        memory_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        operation = f"delete:{logical}"
        with self._tx() as conn:
            replay = self._idempotent_get(conn, owner, project, idempotency_key, operation)
            if replay is not None:
                return replay
            meta = self._table(conn, owner, project, logical)
            cmap = self._column_map(conn, str(meta["id"]))
            rows = self._matching_rows(conn, meta, cmap, filters)
            preview = {
                "owner_id": owner,
                "project_id": project,
                "table": logical,
                "filters": dict(filters),
                "matching_rows": len(rows),
                "sample_row_ids": [str(row.get("_row_id") or "") for row in rows[:20]],
            }
            if not confirm:
                return {
                    "ok": False,
                    "approval_required": True,
                    "destructive": True,
                    "preview": preview,
                    "next_step": "Obtain explicit approval, then call again with confirm=true.",
                }
            where_sql, params = self._where(cmap, filters)
            cur = conn.execute(
                f"DELETE FROM {_q(str(meta['physical_name']))}{where_sql}",
                params,
            )
            for row in rows:
                self._audit(
                    conn,
                    owner=owner,
                    project=project,
                    table_name=logical,
                    row_key=str(row.get("_row_id") or ""),
                    operation="row.delete",
                    actor=actor,
                    source=source,
                    before=row,
                    reason=reason,
                    memory_id=memory_id,
                )
            result = {
                "ok": True,
                "owner_id": owner,
                "project_id": project,
                "table": logical,
                "deleted": int(cur.rowcount),
            }
            self._idempotent_put(conn, owner, project, idempotency_key, operation, result)
            return result

    def upsert(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        conflict_columns: Sequence[str],
        actor: str = "assistant",
        source: str = "mcp",
        reason: str = "",
        memory_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        if not rows:
            raise StructuredDataError("rows are required")
        operation = f"upsert:{logical}"
        with self._tx() as conn:
            replay = self._idempotent_get(conn, owner, project, idempotency_key, operation)
            if replay is not None:
                return replay
            meta = self._table(conn, owner, project, logical)
            cmap = self._column_map(conn, str(meta["id"]))
            conflicts = [_ident(str(name), kind="conflict column") for name in conflict_columns]
            if not conflicts or any(name not in cmap for name in conflicts):
                raise StructuredDataError("conflict_columns must name existing columns")
            inserted = 0
            updated = 0
            for row in rows:
                unknown = set(row) - set(cmap)
                if unknown:
                    raise StructuredDataError(f"unknown columns: {sorted(unknown)}")
                if any(name not in row for name in conflicts):
                    raise StructuredDataError("every upsert row must contain all conflict columns")
                conflict_filter = {name: row[name] for name in conflicts}
                where_sql, where_params = self._where(cmap, conflict_filter)
                existing = conn.execute(
                    f"SELECT * FROM {_q(str(meta['physical_name']))}{where_sql} LIMIT 1",
                    where_params,
                ).fetchone()
                if existing:
                    before = self._row_to_output(existing, cmap)
                    assignments = [name for name in row if name not in conflicts]
                    if assignments:
                        conn.execute(
                            f"UPDATE {_q(str(meta['physical_name']))} SET "
                            + ",".join(f"{_q(name)}=?" for name in assignments)
                            + where_sql,
                            [
                                *[self._storage_value(cmap[name], row[name]) for name in assignments],
                                *where_params,
                            ],
                        )
                    updated += 1
                    self._audit(
                        conn,
                        owner=owner,
                        project=project,
                        table_name=logical,
                        row_key=str(before.get("_row_id") or ""),
                        operation="row.upsert.update",
                        actor=actor,
                        source=source,
                        before=before,
                        after={**before, **dict(row)},
                        reason=reason,
                        memory_id=memory_id,
                    )
                else:
                    payload: dict[str, Any] = {}
                    for name, column in cmap.items():
                        if name in row:
                            payload[name] = self._storage_value(column, row[name])
                        elif column["default_json"] is not None:
                            payload[name] = self._storage_value(column, _decode_json(column["default_json"]))
                        elif bool(column["required"]):
                            raise StructuredDataError(f"missing required column: {name}")
                    row_id = str(uuid.uuid4())
                    names = list(payload) + ["__pm_row_id"]
                    conn.execute(
                        f"INSERT INTO {_q(str(meta['physical_name']))} "
                        f"({','.join(_q(name) for name in names)}) VALUES({','.join('?' for _ in names)})",
                        [payload[name] for name in payload] + [row_id],
                    )
                    inserted += 1
                    self._audit(
                        conn,
                        owner=owner,
                        project=project,
                        table_name=logical,
                        row_key=row_id,
                        operation="row.upsert.insert",
                        actor=actor,
                        source=source,
                        after=dict(row),
                        reason=reason,
                        memory_id=memory_id,
                    )
            result = {
                "ok": True,
                "owner_id": owner,
                "project_id": project,
                "table": logical,
                "processed": len(rows),
                "inserted": inserted,
                "updated": updated,
            }
            self._idempotent_put(conn, owner, project, idempotency_key, operation, result)
            return result

    def create_index(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        index_name: str,
        columns: Sequence[str],
        *,
        unique: bool = False,
        actor: str = "assistant",
        reason: str = "",
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        logical_index = _ident(index_name, kind="index name")
        with self._tx() as conn:
            meta = self._table(conn, owner, project, logical)
            cmap = self._column_map(conn, str(meta["id"]))
            selected = [_ident(str(name), kind="index column") for name in columns]
            if not selected or any(name not in cmap for name in selected):
                raise StructuredDataError("index columns must name existing columns")
            physical_index = "pmi_" + hashlib.sha256(
                f"{owner}\0{project}\0{logical}\0{logical_index}".encode("utf-8")
            ).hexdigest()[:24]
            conn.execute(
                f"CREATE {'UNIQUE ' if unique else ''}INDEX {_q(physical_index)} "
                f"ON {_q(str(meta['physical_name']))} ({','.join(_q(name) for name in selected)})"
            )
            result = {
                "ok": True,
                "owner_id": owner,
                "project_id": project,
                "table": logical,
                "index": logical_index,
                "columns": selected,
                "unique": bool(unique),
            }
            self._audit(
                conn,
                owner=owner,
                project=project,
                table_name=logical,
                operation="index.create",
                actor=actor,
                source="mcp",
                after=result,
                reason=reason,
            )
            return result

    def create_view(
        self,
        owner_id: str,
        project_id: str,
        name: str,
        query: Mapping[str, Any],
        *,
        description: str = "",
        actor: str = "assistant",
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(name, kind="view name")
        if not isinstance(query, Mapping) or not query.get("table"):
            raise StructuredDataError("saved view query requires at least a table")
        self.describe_table(owner, project, str(query["table"]))
        now = _now()
        view_id = str(uuid.uuid4())
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO sd_views(id,owner_id,project_id,name,query_json,description,actor,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(owner_id,project_id,name) DO UPDATE SET
                    query_json=excluded.query_json,description=excluded.description,
                    actor=excluded.actor,updated_at=excluded.updated_at
                """,
                (view_id, owner, project, logical, _json(dict(query)), str(description or ""), actor, now, now),
            )
            self._audit(
                conn,
                owner=owner,
                project=project,
                operation="view.save",
                actor=actor,
                source="mcp",
                after={"name": logical, "query": dict(query)},
            )
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "view": logical,
            "query": dict(query),
        }

    def create_migration(
        self,
        owner_id: str,
        project_id: str,
        title: str,
        operations: Sequence[Mapping[str, Any]],
        *,
        description: str = "",
        apply: bool = True,
        confirm_destructive: bool = False,
        actor: str = "assistant",
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        if not operations:
            raise StructuredDataError("migration operations are required")
        destructive_actions = {"drop_table", "drop_column", "rename_column", "change_type"}
        destructive = any(str(op.get("action") or "").lower() in destructive_actions for op in operations)
        migration_id = str(uuid.uuid4())
        rollback_ops: list[dict[str, Any]] = []
        with self._tx() as conn:
            sequence = int(
                conn.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM sd_migrations WHERE owner_id=? AND project_id=?",
                    (owner, project),
                ).fetchone()[0]
            )
            status = "planned" if destructive or not apply else "applying"
            conn.execute(
                """
                INSERT INTO sd_migrations(
                    id,owner_id,project_id,sequence,title,description,operations_json,rollback_json,
                    destructive,status,actor,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    migration_id,
                    owner,
                    project,
                    sequence,
                    str(title or f"Migration {sequence}"),
                    str(description or ""),
                    _json(list(operations)),
                    "[]",
                    1 if destructive else 0,
                    status,
                    str(actor or "assistant"),
                    _now(),
                ),
            )
            if destructive:
                if apply and confirm_destructive:
                    # The confirmation is recorded, but generic DDL still refuses irreversible work.
                    status = "review"
                    conn.execute("UPDATE sd_migrations SET status='review' WHERE id=?", (migration_id,))
                self._audit(
                    conn,
                    owner=owner,
                    project=project,
                    operation="migration.plan",
                    actor=actor,
                    source="mcp",
                    after={"migration_id": migration_id, "operations": list(operations), "destructive": True},
                    migration_id=migration_id,
                )
                return {
                    "ok": True,
                    "owner_id": owner,
                    "project_id": project,
                    "migration_id": migration_id,
                    "sequence": sequence,
                    "status": status,
                    "destructive": True,
                    "approval_required": True,
                    "operations": list(operations),
                    "rollback_operations": [],
                    "note": "Data-bearing destructive DDL is review-only in v9.8.0.",
                }
            if not apply:
                self._audit(
                    conn,
                    owner=owner,
                    project=project,
                    operation="migration.plan",
                    actor=actor,
                    source="mcp",
                    after={"migration_id": migration_id, "operations": list(operations), "destructive": False},
                    migration_id=migration_id,
                )
                return {
                    "ok": True,
                    "owner_id": owner,
                    "project_id": project,
                    "migration_id": migration_id,
                    "sequence": sequence,
                    "status": "planned",
                    "destructive": False,
                    "operations": list(operations),
                    "rollback_operations": [],
                }
            for op in operations:
                action = str(op.get("action") or "").lower()
                if action == "add_column":
                    table_name = _ident(str(op.get("table") or ""), kind="table name")
                    meta = self._table(conn, owner, project, table_name)
                    sql, normalized = self._column_sql(op.get("column") or {}, for_alter=True)
                    cmap = self._column_map(conn, str(meta["id"]))
                    if normalized["name"] in cmap:
                        raise StructuredDataError(f"column already exists: {normalized['name']}")
                    conn.execute(f"ALTER TABLE {_q(str(meta['physical_name']))} ADD COLUMN {sql}")
                    ordinal = max([int(row["ordinal"]) for row in cmap.values()] or [-1]) + 1
                    conn.execute(
                        """
                        INSERT INTO sd_columns(
                            table_id,name,data_type,required,unique_flag,primary_key,default_json,
                            description,allowed_values_json,ordinal,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            meta["id"],
                            normalized["name"],
                            normalized["type"],
                            1 if normalized["required"] else 0,
                            0,
                            0,
                            None if normalized["default"] is None else _json(normalized["default"]),
                            normalized["description"],
                            _json(normalized["allowed_values"]) if normalized["allowed_values"] else None,
                            ordinal,
                            _now(),
                        ),
                    )
                    rollback_ops.append(
                        {"action": "drop_column", "table": table_name, "column": normalized["name"]}
                    )
                elif action == "create_index":
                    table_name = _ident(str(op.get("table") or ""), kind="table name")
                    logical_index = _ident(str(op.get("index_name") or ""), kind="index name")
                    meta = self._table(conn, owner, project, table_name)
                    cmap = self._column_map(conn, str(meta["id"]))
                    selected = [str(name) for name in op.get("columns") or []]
                    if not selected or any(name not in cmap for name in selected):
                        raise StructuredDataError("migration index columns are invalid")
                    physical_index = "pmi_" + hashlib.sha256(
                        f"{owner}\0{project}\0{table_name}\0{logical_index}".encode("utf-8")
                    ).hexdigest()[:24]
                    conn.execute(
                        f"CREATE {'UNIQUE ' if op.get('unique') else ''}INDEX {_q(physical_index)} "
                        f"ON {_q(str(meta['physical_name']))} ({','.join(_q(name) for name in selected)})"
                    )
                    rollback_ops.append({"action": "drop_index", "physical_index": physical_index})
                else:
                    raise StructuredDataError(f"unsupported safe migration action: {action}")
            conn.execute(
                "UPDATE sd_migrations SET rollback_json=?,status='applied',applied_at=? WHERE id=?",
                (_json(rollback_ops), _now(), migration_id),
            )
            self._audit(
                conn,
                owner=owner,
                project=project,
                operation="migration.apply",
                actor=actor,
                source="mcp",
                after={"migration_id": migration_id, "sequence": sequence, "operations": list(operations)},
                migration_id=migration_id,
            )
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "migration_id": migration_id,
            "sequence": sequence,
            "status": "applied",
            "destructive": False,
            "operations": list(operations),
            "rollback_operations": rollback_ops,
        }

    def list_migrations(
        self,
        owner_id: str,
        project_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sd_migrations WHERE owner_id=? AND project_id=?
                ORDER BY sequence DESC LIMIT ?
                """,
                (owner, project, max(1, min(int(limit), 500))),
            ).fetchall()
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "migrations": [
                {
                    **dict(row),
                    "operations": _decode_json(row["operations_json"], []),
                    "rollback_operations": _decode_json(row["rollback_json"], []),
                    "destructive": bool(row["destructive"]),
                }
                for row in rows
            ],
        }

    def rollback_migration(
        self,
        owner_id: str,
        project_id: str,
        migration_id: str,
        *,
        confirm: bool = False,
        actor: str = "assistant",
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM sd_migrations WHERE id=? AND owner_id=? AND project_id=?",
                (str(migration_id), owner, project),
            ).fetchone()
            if not row:
                raise StructuredDataError("unknown migration in this project")
            rollback_ops = _decode_json(row["rollback_json"], []) or []
            preview = {
                "migration_id": row["id"],
                "sequence": int(row["sequence"]),
                "title": row["title"],
                "status": row["status"],
                "rollback_operations": rollback_ops,
            }
            if not confirm:
                return {
                    "ok": False,
                    "approval_required": True,
                    "destructive": True,
                    "preview": preview,
                    "next_step": "Obtain explicit approval, then call again with confirm=true.",
                }
            if row["status"] != "applied":
                raise StructuredDataError("only applied migrations can be rolled back")
            for op in reversed(rollback_ops):
                action = str(op.get("action") or "")
                if action == "drop_index":
                    conn.execute(f"DROP INDEX IF EXISTS {_q(str(op['physical_index']))}")
                elif action == "drop_column":
                    raise StructuredDataError(
                        "rollback would drop a data-bearing column; v9.8.0 refuses automatic destructive column rollback"
                    )
                else:
                    raise StructuredDataError(f"unsupported rollback action: {action}")
            conn.execute(
                "UPDATE sd_migrations SET status='rolled_back',rolled_back_at=? WHERE id=?",
                (_now(), row["id"]),
            )
            self._audit(
                conn,
                owner=owner,
                project=project,
                operation="migration.rollback",
                actor=actor,
                source="mcp",
                before=preview,
                after={"status": "rolled_back"},
                migration_id=str(row["id"]),
            )
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "migration_id": row["id"],
            "status": "rolled_back",
        }

    def set_override(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        row_key: str,
        field_name: str,
        value: Any,
        *,
        priority: int = 100,
        reason: str = "",
        effective_from: str | None = None,
        expires_at: str | None = None,
        enabled: bool = True,
        actor: str = "human",
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        field = _ident(field_name, kind="field name")
        with self._tx() as conn:
            meta = self._table(conn, owner, project, logical)
            cmap = self._column_map(conn, str(meta["id"]))
            if field not in cmap:
                raise StructuredDataError(f"unknown override field: {field}")
            exists = conn.execute(
                f"SELECT 1 FROM {_q(str(meta['physical_name']))} WHERE {_q('__pm_row_id')}=?",
                (str(row_key),),
            ).fetchone()
            if not exists:
                raise StructuredDataError("unknown row_key in this project table")
            override_id = str(uuid.uuid4())
            now = _now()
            conn.execute(
                """
                INSERT INTO sd_overrides(
                    id,owner_id,project_id,table_name,row_key,field_name,value_json,priority,
                    reason,enabled,effective_from,expires_at,actor,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    override_id,
                    owner,
                    project,
                    logical,
                    str(row_key),
                    field,
                    _json(value),
                    int(priority),
                    str(reason or ""),
                    1 if enabled else 0,
                    effective_from,
                    expires_at,
                    str(actor or "human"),
                    now,
                    now,
                ),
            )
            self._audit(
                conn,
                owner=owner,
                project=project,
                table_name=logical,
                row_key=str(row_key),
                operation="override.set",
                actor=actor,
                source="webgui" if str(actor).startswith("human") else "mcp",
                after={
                    "override_id": override_id,
                    "field": field,
                    "value": value,
                    "priority": int(priority),
                    "reason": reason,
                },
                reason=reason,
            )
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "override_id": override_id,
            "table": logical,
            "row_key": str(row_key),
            "field": field,
            "value": value,
        }

    def link_memory(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        memory_id: str,
        *,
        row_key: str | None = None,
        relation: str = "rationale",
        actor: str = "assistant",
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        logical = _ident(table, kind="table name")
        memory = str(memory_id or "").strip()
        if not memory:
            raise StructuredDataError("memory_id is required")
        with self._tx() as conn:
            meta = self._table(conn, owner, project, logical)
            if row_key:
                exists = conn.execute(
                    f"SELECT 1 FROM {_q(str(meta['physical_name']))} WHERE {_q('__pm_row_id')}=?",
                    (str(row_key),),
                ).fetchone()
                if not exists:
                    raise StructuredDataError("unknown row_key in this project table")
            link_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO sd_memory_links(
                    id,owner_id,project_id,table_name,row_key,memory_id,relation,actor,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    link_id,
                    owner,
                    project,
                    logical,
                    str(row_key) if row_key else None,
                    memory,
                    str(relation or "rationale"),
                    str(actor or "assistant"),
                    _now(),
                ),
            )
            self._audit(
                conn,
                owner=owner,
                project=project,
                table_name=logical,
                row_key=str(row_key) if row_key else None,
                operation="memory.link",
                actor=actor,
                source="mcp",
                memory_id=memory,
                after={"link_id": link_id, "relation": relation},
            )
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "link_id": link_id,
            "table": logical,
            "row_key": row_key,
            "memory_id": memory,
            "relation": relation,
        }

    def audit_log(
        self,
        owner_id: str,
        project_id: str,
        *,
        table: str | None = None,
        row_key: str | None = None,
        operation: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        owner, project = self._scope(owner_id, project_id)
        clauses = ["owner_id=?", "project_id=?"]
        params: list[Any] = [owner, project]
        if table:
            clauses.append("table_name=?")
            params.append(_ident(table, kind="table name"))
        if row_key:
            clauses.append("row_key=?")
            params.append(str(row_key))
        if operation:
            clauses.append("operation=?")
            params.append(str(operation))
        params.append(max(1, min(int(limit), 1000)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sd_audit WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "events": [
                {
                    **dict(row),
                    "before": _decode_json(row["before_json"]),
                    "after": _decode_json(row["after_json"]),
                }
                for row in rows
            ],
        }

    def export(
        self,
        owner_id: str,
        project_id: str,
        *,
        table: str,
        format: str = "json",
        effective: bool = True,
        limit: int = 10000,
    ) -> dict[str, Any]:
        fmt = str(format or "json").lower()
        if fmt not in {"json", "jsonl", "csv"}:
            raise StructuredDataError("format must be json, jsonl or csv")
        result = self.query(
            owner_id,
            project_id,
            table,
            limit=max(1, min(int(limit), 10000)),
            effective=effective,
        )
        clean_rows = [
            {key: value for key, value in row.items() if key != "_overrides"}
            for row in result["rows"]
        ]
        if fmt == "json":
            content = json.dumps(clean_rows, ensure_ascii=False, indent=2)
            media_type = "application/json"
        elif fmt == "jsonl":
            content = "\n".join(_json(row) for row in clean_rows) + ("\n" if clean_rows else "")
            media_type = "application/x-ndjson"
        else:
            output = io.StringIO()
            fields: list[str] = []
            for row in clean_rows:
                for key in row:
                    if key not in fields:
                        fields.append(key)
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for row in clean_rows:
                writer.writerow(
                    {
                        key: _json(value) if isinstance(value, (dict, list)) else value
                        for key, value in row.items()
                    }
                )
            content = output.getvalue()
            media_type = "text/csv"
        return {
            "ok": True,
            "owner_id": result["owner_id"],
            "project_id": result["project_id"],
            "table": result["table"],
            "format": fmt,
            "media_type": media_type,
            "row_count": len(clean_rows),
            "content": content,
        }

    def import_rows(
        self,
        owner_id: str,
        project_id: str,
        table: str,
        content: str,
        *,
        format: str = "json",
        conflict_columns: Sequence[str] | None = None,
        actor: str = "human",
        reason: str = "import",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        fmt = str(format or "json").lower()
        if fmt == "json":
            rows = json.loads(content)
            if not isinstance(rows, list):
                raise StructuredDataError("JSON import must contain an array of objects")
        elif fmt == "jsonl":
            rows = [json.loads(line) for line in content.splitlines() if line.strip()]
        elif fmt == "csv":
            rows = list(csv.DictReader(io.StringIO(content)))
        else:
            raise StructuredDataError("format must be json, jsonl or csv")
        if any(not isinstance(row, dict) for row in rows):
            raise StructuredDataError("every imported row must be an object")
        if conflict_columns:
            result = self.upsert(
                owner_id,
                project_id,
                table,
                rows,
                conflict_columns=conflict_columns,
                actor=actor,
                source="webgui",
                reason=reason,
                idempotency_key=idempotency_key,
            )
            result["imported"] = len(rows)
            return result
        inserted: list[str] = []
        for index, row in enumerate(rows):
            result = self.insert(
                owner_id,
                project_id,
                table,
                row,
                actor=actor,
                source="webgui",
                reason=reason,
                idempotency_key=f"{idempotency_key}:{index}" if idempotency_key else None,
            )
            inserted.append(result["row_id"])
        return {
            "ok": True,
            "owner_id": owner_id,
            "project_id": project_id,
            "table": table,
            "imported": len(inserted),
            "row_ids": inserted,
        }


__all__ = [
    "StructuredDataApprovalRequired",
    "StructuredDataError",
    "StructuredDataScopeError",
    "StructuredDataService",
]
