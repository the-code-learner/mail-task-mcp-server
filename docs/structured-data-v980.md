# Postmaster MCP v9.8.0 — Project-Scoped Structured Data

## Overview

v9.8.0 adds a structured operational-data layer alongside Postmaster's persistent semantic memory. The two stores intentionally have different responsibilities: memories and skills remain the place for contextual knowledge, rationale and reusable instructions, while structured data is the authoritative layer for relational operational facts that benefit from schema, query, audit and migration semantics.

The V1 backend is self-contained SQLite. It reuses the application's durable data volume and therefore does not require a deployment-YAML change. The service contract is backend-oriented rather than SQLite-oriented so a future PostgreSQL implementation can preserve the same project-scoped MCP and WebGUI surface.

## Scope and isolation

Every structured-data operation is resolved inside an exact `owner_id + project_id` scope. Agents and WebGUI callers use logical table names only. Physical SQLite names are derived internally and are never returned by status, describe or query APIs.

The service validates the project scope server-side before resolving a table. Two projects may use the same logical table name without sharing schema or rows. Cross-project access is not an administrative escape hatch: there is no unrestricted database-admin MCP tool.

## Shared service architecture

`StructuredDataService` is the domain service used by both the MCP tool surface and the WebGUI control plane. This keeps isolation, validation, audit, idempotency, override resolution and migration safeguards identical across human and agent workflows.

The composed runtime installs the service after the existing v9.6.9/v9.7.x layers. Mail delivery, tracking, Safe Email, recipient policy and historical release behaviors are not reimplemented by this feature.

## MCP surface

v9.8.0 adds exactly 21 MCP commands, taking the expected composed runtime count from 97 to 118:

- `db_status`
- `db_describe_project`
- `db_describe_table`
- `db_create_table`
- `db_alter_table`
- `db_query`
- `db_query_sql_readonly`
- `db_insert`
- `db_import`
- `db_upsert`
- `db_update`
- `db_delete`
- `db_create_index`
- `db_create_view`
- `db_create_migration`
- `db_list_migrations`
- `db_rollback_migration`
- `db_audit_log`
- `db_export`
- `db_set_override`
- `db_link_memory`

`runtime_status` reports capability version `9.8.0`, expected MCP count `118`, the `+21` delta and structured-data capability flags.

## CRUD, idempotency and provenance

Validated create/insert/update/upsert/import operations operate only on declared logical columns. Mutation APIs support idempotency keys where replay protection is relevant. A replay returns the stored result rather than performing the mutation again.

Mutations append project-scoped audit events that retain operation, actor, source, reason, table/row identity and before/after payloads where applicable. Structured rows may also be linked to persistent Postmaster memories so operational facts can retain a durable rationale without merging semantic memory into the relational store.

## Overrides: raw vs effective state

Overrides provide an auditable precedence layer without destroying the original row value. Normal effective queries expose the selected override while retaining the original value in override metadata. Callers can request raw rows when they need the stored source value.

This distinction makes human corrections, temporary policy values and precedence decisions reversible and inspectable.

## Read-only SQL

`db_query_sql_readonly` accepts only validated `SELECT` and read-only `WITH` queries. Logical table references are resolved to physical project-local tables by the service. Multiple statements, system schemas and mutating/destructive operations such as `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`, `DROP`, dangerous `PRAGMA`, `ATTACH`, `VACUUM` and related operations are rejected.

The SQL surface is therefore an advanced read interface, not an unrestricted SQLite console.

## Import and export

The V1 service supports JSON, JSONL and CSV import/export. Imports use the same schema validation, project boundary, provenance and optional idempotency/upsert behavior as ordinary mutations. Exports can return raw or effective values as appropriate and never expose physical database namespaces.

## Migration safety

Migrations are project-scoped and auditable. Safe additive operations, currently including supported column additions and index creation, may be applied immediately and are recorded with rollback metadata.

Data-bearing destructive schema changes remain review-only in v9.8.0. A destructive migration can be planned and surfaced for approval, but generic destructive DDL is not automatically executed even when a caller supplies a confirmation flag. Rollbacks are similarly refused when they would implicitly drop a data-bearing column.

Row deletion follows a separate preview/confirm lifecycle. A preview executes the real matching query, reports the matching-row count plus sample row IDs, and performs no deletion. Deletion occurs only on a subsequent call with `confirm=true` and explicit filters.

## WebGUI control plane

The Structured Data WebGUI is a human control plane over the same service. It includes:

- exact project selection and project overview;
- table browser and schema explorer;
- create-table and additive-column workflows;
- row insertion plus JSON/JSONL/CSV import/export;
- effective-state overrides and memory links;
- migration planning/history;
- approval inbox for destructive schema plans;
- activity, audit and provenance views.

The UI exposes logical project resources only. It does not expose a raw database-admin console or physical SQLite paths/names.

## SQLite V1 and PostgreSQL evolution

SQLite is the v9.8.0 source-release backend because it is self-contained, uses Python's standard library and can live on the already-approved durable volume. No new dependency is required.

A future PostgreSQL backend should preserve the same domain contract: server-enforced project scoping, logical namespaces, shared MCP/WebGUI behavior, audit/provenance, idempotency, override semantics, migration policy and read-only scoped advanced SQL. Backend administration must remain outside the agent surface.

## Source release versus production activation

v9.8.0 is designed so the source release can ship independently from production activation. Publishing the GitHub source/tag/release does **not** authorize a Portainer stack change or a production runtime update.

`postmaster-mcp.yml` is preserve-by-default. The v9.8.0 source feature requires no YAML delta and no new Python requirement. Any later production activation or deployment-YAML edit is a separate owner-approved operation under the Versioned Single-YAML Portainer Deployment policy.

## Preserved mail/security invariants

The structured-data layer is additive and does not alter outbound mail semantics. Existing guarantees remain in force, including outbound idempotency, no resend after `sent=true`, Sent-clean behavior, private BCC/no BCC rediscovery, Safe Email zero-network defaults and historical detracking/privacy boundaries.
