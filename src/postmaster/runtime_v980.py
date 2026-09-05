from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations

from .structured_data_v980 import StructuredDataError, StructuredDataService

MCP_COMMAND_COUNT_V969 = 97
MCP_STRUCTURED_DATA_COMMANDS_V980 = 21
MCP_COMMAND_COUNT_V980 = MCP_COMMAND_COUNT_V969 + MCP_STRUCTURED_DATA_COMMANDS_V980


def _annotations(*, read_only: bool, destructive: bool = False, idempotent: bool = False):
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=False,
    )


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except StructuredDataError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def install_runtime_v980(
    base: Any,
    core: Any,
    previous_runtime_status: Any,
    *,
    db_path: str | None = None,
) -> StructuredDataService:
    """Install v9.8.0 project-scoped structured data without changing mail semantics."""

    def resolve_project(owner_id: str, project_id: str):
        result = base.scheduler().list_projects(owner_id=owner_id)
        rows = result if isinstance(result, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if (
                str(row.get("id") or "") == str(project_id)
                and str(row.get("owner_id") or "") == str(owner_id)
                and bool(row.get("active", True))
            ):
                return row
        return None

    service = StructuredDataService(db_path, project_resolver=resolve_project)

    def runtime_status():
        status = previous_runtime_status()
        if not isinstance(status, dict):
            status = {"ok": True}
        status.update(
            {
                "version_capability": "9.8.0",
                "mcp_command_count_expected": MCP_COMMAND_COUNT_V980,
                "mcp_command_count_delta_from_v969": MCP_STRUCTURED_DATA_COMMANDS_V980,
                "structured_data": {
                    "enabled": True,
                    "backend": service.backend_name,
                    "project_scoped": True,
                    "physical_namespace_hidden": True,
                    "raw_sql": "validated-read-only",
                    "audit": True,
                    "overrides": True,
                    "memory_links": True,
                    "migrations": True,
                    "webgui_control_plane": True,
                },
            }
        )
        return status

    def db_status(owner_id: str, project_id: str):
        """Read-only. Return structured-data capability and inventory for one exact project."""
        return _safe(service.status, owner_id, project_id)

    def db_describe_project(owner_id: str, project_id: str):
        """Read-only. List structured tables and metadata for one exact project scope."""
        return _safe(service.describe_project, owner_id, project_id)

    def db_describe_table(owner_id: str, project_id: str, table: str):
        """Read-only. Describe one logical project table; physical storage names are never exposed."""
        return _safe(service.describe_table, owner_id, project_id, table)

    def db_create_table(
        owner_id: str,
        project_id: str,
        table: str,
        columns: list[dict[str, Any]],
        description: str = "",
        source_of_truth: str = "operational",
        agent_instructions: str = "",
        actor: str = "assistant",
        reason: str = "",
        idempotency_key: str | None = None,
    ):
        """WRITE ACTION. Create one project-scoped table from a validated declarative schema."""
        return _safe(
            service.create_table,
            owner_id,
            project_id,
            table,
            columns,
            description=description,
            source_of_truth=source_of_truth,
            agent_instructions=agent_instructions,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def db_alter_table(
        owner_id: str,
        project_id: str,
        table: str,
        action: str,
        column: dict[str, Any] | None = None,
        description: str | None = None,
        source_of_truth: str | None = None,
        agent_instructions: str | None = None,
        actor: str = "assistant",
        reason: str = "",
        idempotency_key: str | None = None,
    ):
        """WRITE ACTION. Safe additive ALTER (add_column/metadata); destructive DDL is refused."""
        return _safe(
            service.alter_table,
            owner_id,
            project_id,
            table,
            action=action,
            column=column,
            description=description,
            source_of_truth=source_of_truth,
            agent_instructions=agent_instructions,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def db_query(
        owner_id: str,
        project_id: str,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        sort: list[dict[str, Any] | str] | None = None,
        joins: list[dict[str, Any]] | None = None,
        limit: int = 100,
        offset: int = 0,
        effective: bool = True,
    ):
        """Read-only. Structured project query with filters/sort/pagination and optional joins."""
        if joins:
            if not columns:
                return {"ok": False, "error": "joins require explicit qualified columns"}
            return _safe(
                service.query_join,
                owner_id,
                project_id,
                table,
                joins=joins,
                columns=columns,
                filters=filters,
                limit=limit,
                offset=offset,
            )
        return _safe(
            service.query,
            owner_id,
            project_id,
            table,
            columns=columns,
            filters=filters,
            sort=sort,
            limit=limit,
            offset=offset,
            effective=effective,
        )

    def db_query_sql_readonly(
        owner_id: str,
        project_id: str,
        sql: str,
        params: list[Any] | None = None,
        limit: int = 1000,
    ):
        """Read-only. Validated SELECT/WITH SQL restricted to logical tables in one project."""
        return _safe(
            service.query_sql_readonly,
            owner_id,
            project_id,
            sql,
            params=params,
            limit=limit,
        )

    def db_insert(
        owner_id: str,
        project_id: str,
        table: str,
        values: dict[str, Any],
        actor: str = "assistant",
        reason: str = "",
        memory_id: str | None = None,
        idempotency_key: str | None = None,
    ):
        """WRITE ACTION. Insert one validated row and append project audit/provenance."""
        return _safe(
            service.insert,
            owner_id,
            project_id,
            table,
            values,
            actor=actor,
            reason=reason,
            memory_id=memory_id,
            idempotency_key=idempotency_key,
        )

    def db_import(
        owner_id: str,
        project_id: str,
        table: str,
        content: str,
        format: str = "json",
        conflict_columns: list[str] | None = None,
        actor: str = "assistant",
        reason: str = "import",
        idempotency_key: str | None = None,
    ):
        """WRITE ACTION. Import CSV/JSON/JSONL into one project table."""
        return _safe(
            service.import_rows,
            owner_id,
            project_id,
            table,
            content,
            format=format,
            conflict_columns=conflict_columns,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def db_upsert(
        owner_id: str,
        project_id: str,
        table: str,
        rows: list[dict[str, Any]],
        conflict_columns: list[str],
        actor: str = "assistant",
        reason: str = "",
        memory_id: str | None = None,
        idempotency_key: str | None = None,
    ):
        """WRITE ACTION. Atomic bulk upsert with explicit conflict columns and audit."""
        return _safe(
            service.upsert,
            owner_id,
            project_id,
            table,
            rows,
            conflict_columns=conflict_columns,
            actor=actor,
            reason=reason,
            memory_id=memory_id,
            idempotency_key=idempotency_key,
        )

    def db_update(
        owner_id: str,
        project_id: str,
        table: str,
        filters: dict[str, Any],
        values: dict[str, Any],
        actor: str = "assistant",
        reason: str = "",
        memory_id: str | None = None,
        idempotency_key: str | None = None,
    ):
        """WRITE ACTION. Update only rows matching explicit structured filters."""
        return _safe(
            service.update,
            owner_id,
            project_id,
            table,
            filters,
            values,
            actor=actor,
            reason=reason,
            memory_id=memory_id,
            idempotency_key=idempotency_key,
        )

    def db_delete(
        owner_id: str,
        project_id: str,
        table: str,
        filters: dict[str, Any],
        confirm: bool = False,
        actor: str = "assistant",
        reason: str = "",
        memory_id: str | None = None,
        idempotency_key: str | None = None,
    ):
        """DESTRUCTIVE WRITE. Delete filtered rows only after fresh explicit confirm=true."""
        return _safe(
            service.delete,
            owner_id,
            project_id,
            table,
            filters,
            confirm=confirm,
            actor=actor,
            reason=reason,
            memory_id=memory_id,
            idempotency_key=idempotency_key,
        )

    def db_create_index(
        owner_id: str,
        project_id: str,
        table: str,
        index_name: str,
        columns: list[str],
        unique: bool = False,
        actor: str = "assistant",
        reason: str = "",
    ):
        """WRITE ACTION. Create a validated project-table index."""
        return _safe(
            service.create_index,
            owner_id,
            project_id,
            table,
            index_name,
            columns,
            unique=unique,
            actor=actor,
            reason=reason,
        )

    def db_create_view(
        owner_id: str,
        project_id: str,
        name: str,
        query: dict[str, Any],
        description: str = "",
        actor: str = "assistant",
    ):
        """WRITE ACTION. Save a project-scoped structured query view."""
        return _safe(
            service.create_view,
            owner_id,
            project_id,
            name,
            query,
            description=description,
            actor=actor,
        )

    def db_create_migration(
        owner_id: str,
        project_id: str,
        title: str,
        operations: list[dict[str, Any]],
        description: str = "",
        apply: bool = True,
        confirm_destructive: bool = False,
        actor: str = "assistant",
    ):
        """WRITE ACTION. Record/apply safe additive migrations; destructive plans require review."""
        return _safe(
            service.create_migration,
            owner_id,
            project_id,
            title,
            operations,
            description=description,
            apply=apply,
            confirm_destructive=confirm_destructive,
            actor=actor,
        )

    def db_list_migrations(owner_id: str, project_id: str, limit: int = 100):
        """Read-only. List project migration history and rollback plans."""
        return _safe(service.list_migrations, owner_id, project_id, limit=limit)

    def db_rollback_migration(
        owner_id: str,
        project_id: str,
        migration_id: str,
        confirm: bool = False,
        actor: str = "assistant",
    ):
        """DESTRUCTIVE WRITE. Preview first; rollback only after explicit confirm=true."""
        return _safe(
            service.rollback_migration,
            owner_id,
            project_id,
            migration_id,
            confirm=confirm,
            actor=actor,
        )

    def db_audit_log(
        owner_id: str,
        project_id: str,
        table: str | None = None,
        row_key: str | None = None,
        operation: str | None = None,
        limit: int = 200,
    ):
        """Read-only. Return project-scoped structured-data provenance/audit events."""
        return _safe(
            service.audit_log,
            owner_id,
            project_id,
            table=table,
            row_key=row_key,
            operation=operation,
            limit=limit,
        )

    def db_export(
        owner_id: str,
        project_id: str,
        table: str,
        format: str = "json",
        effective: bool = True,
        limit: int = 10000,
    ):
        """Read-only. Export one project table as JSON, JSONL or CSV."""
        return _safe(
            service.export,
            owner_id,
            project_id,
            table=table,
            format=format,
            effective=effective,
            limit=limit,
        )

    def db_set_override(
        owner_id: str,
        project_id: str,
        table: str,
        row_key: str,
        field_name: str,
        value: Any,
        priority: int = 100,
        reason: str = "",
        effective_from: str | None = None,
        expires_at: str | None = None,
        enabled: bool = True,
        actor: str = "assistant",
    ):
        """WRITE ACTION. Add an auditable precedence override without erasing raw values."""
        return _safe(
            service.set_override,
            owner_id,
            project_id,
            table,
            row_key,
            field_name,
            value,
            priority=priority,
            reason=reason,
            effective_from=effective_from,
            expires_at=expires_at,
            enabled=enabled,
            actor=actor,
        )

    def db_link_memory(
        owner_id: str,
        project_id: str,
        table: str,
        memory_id: str,
        row_key: str | None = None,
        relation: str = "rationale",
        actor: str = "assistant",
    ):
        """WRITE ACTION. Link project structured facts to persistent memory/rationale."""
        return _safe(
            service.link_memory,
            owner_id,
            project_id,
            table,
            memory_id,
            row_key=row_key,
            relation=relation,
            actor=actor,
        )

    tools = (
        ("db_status", db_status, _annotations(read_only=True, idempotent=True)),
        ("db_describe_project", db_describe_project, _annotations(read_only=True, idempotent=True)),
        ("db_describe_table", db_describe_table, _annotations(read_only=True, idempotent=True)),
        ("db_create_table", db_create_table, _annotations(read_only=False)),
        ("db_alter_table", db_alter_table, _annotations(read_only=False)),
        ("db_query", db_query, _annotations(read_only=True, idempotent=True)),
        ("db_query_sql_readonly", db_query_sql_readonly, _annotations(read_only=True, idempotent=True)),
        ("db_insert", db_insert, _annotations(read_only=False)),
        ("db_import", db_import, _annotations(read_only=False)),
        ("db_upsert", db_upsert, _annotations(read_only=False)),
        ("db_update", db_update, _annotations(read_only=False)),
        ("db_delete", db_delete, _annotations(read_only=False, destructive=True)),
        ("db_create_index", db_create_index, _annotations(read_only=False)),
        ("db_create_view", db_create_view, _annotations(read_only=False)),
        ("db_create_migration", db_create_migration, _annotations(read_only=False)),
        ("db_list_migrations", db_list_migrations, _annotations(read_only=True, idempotent=True)),
        ("db_rollback_migration", db_rollback_migration, _annotations(read_only=False, destructive=True)),
        ("db_audit_log", db_audit_log, _annotations(read_only=True, idempotent=True)),
        ("db_export", db_export, _annotations(read_only=True, idempotent=True)),
        ("db_set_override", db_set_override, _annotations(read_only=False)),
        ("db_link_memory", db_link_memory, _annotations(read_only=False)),
    )

    core.mcp.remove_tool("runtime_status")
    core.mcp.add_tool(
        runtime_status,
        name="runtime_status",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    for name, fn, annotations in tools:
        core.mcp.add_tool(fn, name=name, annotations=annotations)

    base.structured_data_service = lambda: service
    core.structured_data_service = lambda: service
    base.runtime_status = runtime_status
    core.runtime_status = runtime_status
    for name, fn, _ in tools:
        setattr(base, name, fn)
        setattr(core, name, fn)
    return service


__all__ = [
    "MCP_COMMAND_COUNT_V980",
    "MCP_STRUCTURED_DATA_COMMANDS_V980",
    "install_runtime_v980",
]
