from __future__ import annotations

import json
from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route

from . import webgui_v962 as shell
from . import webgui_v962_views as views
from . import webgui_v970 as v970

VIEW = "data"
LABEL = "Structured Data"


def _url(
    *,
    project: str = "",
    table: str = "",
    flash: str = "",
    page_size: int | None = None,
    offset: int = 0,
) -> str:
    query: dict[str, str] = {"ui_view": VIEW}
    if project:
        query["project"] = project
    if table:
        query["data_table"] = table
    if page_size is not None:
        query["data_limit"] = str(page_size)
    if offset:
        query["data_offset"] = str(max(0, offset))
    if flash:
        query["flash"] = flash
    return "/?" + urlencode(query) + "#data"


def _projects(base: Any) -> list[dict[str, Any]]:
    result = base._safe_call(base.scheduler().list_projects)
    rows = result if isinstance(result, list) else []
    return [dict(row) for row in rows if isinstance(row, dict) and bool(row.get("active", True))]


def _selected_scope(base: Any, request: Request) -> tuple[str, str, list[dict[str, Any]]]:
    projects = _projects(base)
    project_id = str(request.query_params.get("project") or "").strip()
    if not project_id:
        return "", "", projects
    for row in projects:
        if str(row.get("id") or "") == project_id:
            return str(row.get("owner_id") or ""), project_id, projects
    return "", "", projects


def _project_selector(projects: list[dict[str, Any]], selected: str) -> str:
    options = ['<option value="">Choose a project…</option>']
    for row in projects:
        pid = str(row.get("id") or "")
        name = str(row.get("name") or pid)
        owner = str(row.get("owner_id") or "")
        options.append(
            f'<option value="{escape(pid, quote=True)}"'
            f'{" selected" if pid == selected else ""}>'
            f'{escape(name)} · {escape(owner)}/{escape(pid)}</option>'
        )
    return (
        '<form method="get" action="/" class="v951-toolbar">'
        '<input type="hidden" name="ui_view" value="data">'
        '<label>Project<select name="project" required>'
        + "".join(options)
        + '</select></label><button class="primary" type="submit">Open data workspace</button></form>'
    )


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _table_cards(project_id: str, tables: list[dict[str, Any]]) -> str:
    cards: list[str] = []
    for table in tables:
        name = str(table.get("name") or "")
        href = _url(project=project_id, table=name)
        cards.append(
            '<section class="card">'
            f'<div class="panel-title"><h3>{escape(name)}</h3>'
            f'<a href="{escape(href, quote=True)}" data-v960-fragment="data">'
            '<button type="button">Browse</button></a></div>'
            f'<p class="small muted">{escape(str(table.get("description") or "No description"))}</p>'
            f'<div class="small muted">source of truth: '
            f'{escape(str(table.get("source_of_truth") or "operational"))}</div>'
            '</section>'
        )
    return "".join(cards) or (
        '<section class="card wide"><span class="muted">'
        'No structured tables in this project yet.</span></section>'
    )


def _browser(
    service: Any,
    owner: str,
    project: str,
    table: str,
    *,
    page_size: int = 10,
    offset: int = 0,
) -> str:
    if not table:
        return ""
    described = service.describe_table(owner, project, table)
    queried = service.query(
        owner,
        project,
        table,
        limit=page_size + 1,
        offset=offset,
        sort=["_row_id"],
        effective=True,
    )
    columns = [dict(row) for row in described.get("columns", [])]
    fetched_rows = [dict(row) for row in queried.get("rows", [])]
    has_next = len(fetched_rows) > page_size
    rows = fetched_rows[:page_size]
    names = [str(row.get("name") or "") for row in columns]
    header = "".join(f'<th>{escape(name)}</th>' for name in ["_row_id", *names])
    body: list[str] = []
    for row in rows:
        cells = "".join(
            f'<td><code>{escape(_json_text(row.get(name)))}</code></td>'
            for name in ["_row_id", *names]
        )
        body.append(f"<tr>{cells}</tr>")
    if not body:
        body.append(
            f'<tr><td colspan="{max(1, len(names) + 1)}" class="muted">No rows</td></tr>'
        )
    schema_rows: list[str] = []
    for column in columns:
        schema_rows.append(
            '<tr>'
            f'<td><code>{escape(str(column.get("name") or ""))}</code></td>'
            f'<td>{escape(str(column.get("data_type") or ""))}</td>'
            f'<td>{"yes" if column.get("required") else "no"}</td>'
            f'<td>{"yes" if column.get("unique") else "no"}</td>'
            f'<td>{"yes" if column.get("primary_key") else "no"}</td>'
            f'<td>{escape(str(column.get("description") or ""))}</td>'
            '</tr>'
        )
    sizes = "".join(
        f'<option value="{size}"{" selected" if size == page_size else ""}>{size}</option>'
        for size in (10, 20, 50, 100)
    )
    previous_offset = max(0, offset - page_size)
    previous = (
        f'<a href="{escape(_url(project=project, table=table, page_size=page_size, offset=previous_offset), quote=True)}" '
        'data-v960-fragment="data"><button type="button" aria-label="Previous page">←</button></a>'
        if offset > 0
        else '<button type="button" aria-label="Previous page" disabled>←</button>'
    )
    next_link = (
        f'<a href="{escape(_url(project=project, table=table, page_size=page_size, offset=offset + page_size), quote=True)}" '
        'data-v960-fragment="data"><button type="button" aria-label="Next page">→</button></a>'
        if has_next
        else '<button type="button" aria-label="Next page" disabled>→</button>'
    )
    if rows:
        range_label = f"Rows {offset + 1}–{offset + len(rows)}"
    else:
        range_label = "No rows"
    return f"""
<section class="card wide">
  <div class="panel-title"><div><h2>{escape(table)}</h2>
  <p class="small muted">Effective rows · raw values remain auditable under overrides.</p></div>
  <span class="badge">{escape(range_label)}</span></div>
  <div class="v951-toolbar">
    <form method="get" action="/" class="row">
      <input type="hidden" name="ui_view" value="data">
      <input type="hidden" name="project" value="{escape(project, quote=True)}">
      <input type="hidden" name="data_table" value="{escape(table, quote=True)}">
      <input type="hidden" name="data_offset" value="0">
      <label>Rows per page
        <select name="data_limit" onchange="this.form.submit()">{sizes}</select>
      </label>
      <noscript><button type="submit">Apply</button></noscript>
    </form>
    <div class="row" aria-label="Table pagination">
      {previous}
      {next_link}
    </div>
  </div>
  <div class="scroll"><table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table></div>
</section>
<section class="card wide"><div class="panel-title"><h2>Schema explorer</h2>
<span class="badge">{len(columns)} columns</span></div>
<div class="scroll"><table><thead><tr><th>Column</th><th>Type</th><th>Required</th><th>Unique</th><th>PK</th><th>Description</th></tr></thead>
<tbody>{''.join(schema_rows)}</tbody></table></div></section>
"""


def _post_form(
    base: Any,
    action: str,
    owner: str,
    project: str,
    table: str,
    inner: str,
    button: str,
) -> str:
    return (
        f'<form method="post" action="{escape(action, quote=True)}">'
        f'<input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}">'
        f'<input type="hidden" name="owner_id" value="{escape(owner, quote=True)}">'
        f'<input type="hidden" name="project_id" value="{escape(project, quote=True)}">'
        + (
            f'<input type="hidden" name="table" value="{escape(table, quote=True)}">'
            if table
            else ""
        )
        + inner
        + f'<div style="margin-top:9px"><button class="primary" type="submit">'
        f'{escape(button)}</button></div></form>'
    )


def render_data(base: Any, request: Request) -> str:
    owner, project, projects = _selected_scope(base, request)
    flash = str(request.query_params.get("flash") or "").strip()
    flash_html = f'<div class="flash">{escape(flash)}</div>' if flash else ""
    selector = _project_selector(projects, project)
    if not project or not owner:
        return (
            '<section class="tab-panel" id="panel-data" data-panel="data">'
            f'{flash_html}<div class="v951-pagehead"><div><h2>Structured Data</h2>'
            '<p>Project-scoped operational facts, schemas, migrations and provenance.</p>'
            f'</div></div>{selector}<div class="notice"><strong>Isolation boundary:</strong> '
            'choose one exact project. The service resolves logical tables inside owner_id + '
            'project_id and never exposes physical namespaces.</div></section>'
        )

    service = base.structured_data_service()
    listed = service.list_project_tables(owner, project)
    tables = [dict(row) for row in listed.get("tables", [])]
    selected_table = str(request.query_params.get("data_table") or "").strip()
    if selected_table and selected_table not in {str(row.get("name") or "") for row in tables}:
        selected_table = ""
    try:
        requested_page_size = int(request.query_params.get("data_limit") or 10)
    except (TypeError, ValueError):
        requested_page_size = 10
    page_size = requested_page_size if requested_page_size in {10, 20, 50, 100} else 10
    try:
        offset = max(0, int(request.query_params.get("data_offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    create_table_form = _post_form(
        base,
        "/dashboard/data/table/create",
        owner,
        project,
        "",
        '<div class="row"><div class="field"><label>Table name</label>'
        '<input name="table" required pattern="[A-Za-z][A-Za-z0-9_]*"></div>'
        '<div class="field grow"><label>Description</label><input name="description"></div></div>'
        '<div class="field" style="margin-top:8px"><label>Columns JSON</label>'
        '<textarea name="columns_json" rows="8" required>[\n'
        '  {"name":"id","type":"text","primary_key":true,"required":true},\n'
        '  {"name":"name","type":"text","required":true}\n]</textarea></div>',
        "Create table",
    )

    table_forms = ""
    if selected_table:
        add_column = _post_form(
            base,
            "/dashboard/data/table/add-column",
            owner,
            project,
            selected_table,
            '<div class="row"><div class="field"><label>Column</label><input name="column_name" required></div>'
            '<div class="field"><label>Type</label><select name="data_type">'
            '<option>text</option><option>integer</option><option>real</option>'
            '<option>boolean</option><option>json</option><option>datetime</option></select></div>'
            '<label><input type="checkbox" name="required" value="1"> Required</label>'
            '<div class="field grow"><label>Description</label><input name="description"></div></div>',
            "Add column",
        )
        insert_row = _post_form(
            base,
            "/dashboard/data/row/insert",
            owner,
            project,
            selected_table,
            '<div class="field"><label>Row JSON</label><textarea name="row_json" rows="7" required>{}</textarea></div>'
            '<div class="field"><label>Reason / provenance</label>'
            '<input name="reason" placeholder="manual WebGUI edit"></div>',
            "Insert row",
        )
        import_rows = _post_form(
            base,
            "/dashboard/data/import",
            owner,
            project,
            selected_table,
            '<div class="row"><div class="field"><label>Format</label><select name="format">'
            '<option>json</option><option>jsonl</option><option>csv</option></select></div>'
            '<div class="field grow"><label>Conflict columns (comma separated, optional upsert)</label>'
            '<input name="conflict_columns"></div></div><div class="field"><label>Payload</label>'
            '<textarea name="content" rows="8" required></textarea></div>',
            "Import rows",
        )
        override = _post_form(
            base,
            "/dashboard/data/override",
            owner,
            project,
            selected_table,
            '<div class="row"><div class="field"><label>Row ID</label><input name="row_key" required></div>'
            '<div class="field"><label>Field</label><input name="field_name" required></div>'
            '<div class="field grow"><label>Value (JSON)</label><input name="value_json" required></div>'
            '<div class="field"><label>Priority</label><input type="number" name="priority" value="100"></div></div>'
            '<div class="field"><label>Reason</label><input name="reason" required></div>',
            "Set override",
        )
        memory_link = _post_form(
            base,
            "/dashboard/data/memory-link",
            owner,
            project,
            selected_table,
            '<div class="row"><div class="field"><label>Memory ID</label><input name="memory_id" required></div>'
            '<div class="field"><label>Row ID (optional)</label><input name="row_key"></div>'
            '<div class="field"><label>Relation</label><input name="relation" value="rationale"></div></div>',
            "Link memory",
        )
        table_forms = (
            f'<details class="v962-collapsible"><summary>Add column to {escape(selected_table)}</summary>'
            f'<div class="v962-collapsible-body"><section class="card">{add_column}</section></div></details>'
            f'<details class="v962-collapsible"><summary>Insert row</summary>'
            f'<div class="v962-collapsible-body"><section class="card">{insert_row}</section></div></details>'
            f'<details class="v962-collapsible"><summary>Bulk import</summary>'
            f'<div class="v962-collapsible-body"><section class="card">{import_rows}</section></div></details>'
            f'<details class="v962-collapsible"><summary>Effective-state override</summary>'
            f'<div class="v962-collapsible-body"><section class="card">{override}</section></div></details>'
            f'<details class="v962-collapsible"><summary>Link rationale memory</summary>'
            f'<div class="v962-collapsible-body"><section class="card">{memory_link}</section></div></details>'
        )

    migration_form = _post_form(
        base,
        "/dashboard/data/migration/create",
        owner,
        project,
        "",
        '<div class="row"><div class="field grow"><label>Migration title</label><input name="title" required></div>'
        '<label><input type="checkbox" name="apply" value="1" checked> Apply safe additive operations now</label></div>'
        '<div class="field"><label>Operations JSON</label><textarea name="operations_json" rows="7" required>'
        '[{"action":"create_index","table":"example","index_name":"idx_example","columns":["id"]}]'
        '</textarea></div>',
        "Create migration",
    )

    browser = _browser(
        service,
        owner,
        project,
        selected_table,
        page_size=page_size,
        offset=offset,
    )
    return f'''
<section class="tab-panel" id="panel-data" data-panel="data">
{flash_html}<div class="v951-pagehead"><div><h2>Structured Data</h2>
<p>Human control plane for project-scoped relational facts, schema and provenance.</p></div>
<span class="badge">v9.8.1</span></div>
{selector}
<div class="notice"><strong>Server-enforced scope:</strong> {escape(owner)} / {escape(project)} ·
 backend {escape(str(getattr(service, "backend_name", "sqlite")))}. Logical names resolve to hidden physical
 namespaces. Raw SQL is read-only and validated; destructive DDL is review-only.</div>
<section class="card wide"><div class="panel-title"><h2>Tables</h2>
<span class="small muted">Open a table to load rows. No table row counts are calculated here.</span></div>
<div class="v951-grid">{_table_cards(project, tables)}</div></section>
{browser}
<div class="v951-grid">
<details class="v962-collapsible"><summary>Create table</summary><div class="v962-collapsible-body">
<section class="card">{create_table_form}</section></div></details>
{table_forms}
<details class="v962-collapsible"><summary>Create migration</summary><div class="v962-collapsible-body">
<section class="card">{migration_form}</section></div></details>
</div>
</section>
'''


def _parse_json(value: Any, *, label: str) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc.msg}") from exc


def _redirect(project: str, table: str, message: str) -> RedirectResponse:
    return RedirectResponse(_url(project=project, table=table, flash=message[:240]), status_code=303)


async def _form(base: Any, request: Request) -> tuple[Any, Any, str, str, str]:
    form, error = await base._verified_form(request)
    if error:
        return form, error, "", "", ""
    owner = str(form.get("owner_id") or "").strip()
    project = str(form.get("project_id") or "").strip()
    table = str(form.get("table") or "").strip()
    return form, None, owner, project, table


async def create_table(base: Any, request: Request):
    form, error, owner, project, table = await _form(base, request)
    if error:
        return error
    try:
        result = base.structured_data_service().create_table(
            owner,
            project,
            table,
            _parse_json(form.get("columns_json"), label="columns"),
            description=str(form.get("description") or ""),
            actor="human:webgui",
            source="webgui",
        )
        message = f"Created table {result.get('table', table)}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return _redirect(project, table, message)


async def add_column(base: Any, request: Request):
    form, error, owner, project, table = await _form(base, request)
    if error:
        return error
    try:
        column = {
            "name": str(form.get("column_name") or "").strip(),
            "type": str(form.get("data_type") or "text").strip(),
            "required": bool(form.get("required")),
            "description": str(form.get("description") or ""),
        }
        base.structured_data_service().alter_table(
            owner,
            project,
            table,
            action="add_column",
            column=column,
            actor="human:webgui",
            source="webgui",
        )
        message = f"Added column {column['name']}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return _redirect(project, table, message)


async def insert_row(base: Any, request: Request):
    form, error, owner, project, table = await _form(base, request)
    if error:
        return error
    try:
        result = base.structured_data_service().insert(
            owner,
            project,
            table,
            _parse_json(form.get("row_json"), label="row"),
            actor="human:webgui",
            source="webgui",
            reason=str(form.get("reason") or "manual WebGUI edit"),
        )
        message = f"Inserted row {result.get('row_id', '')}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return _redirect(project, table, message)


async def import_rows(base: Any, request: Request):
    form, error, owner, project, table = await _form(base, request)
    if error:
        return error
    try:
        conflicts = [
            item.strip()
            for item in str(form.get("conflict_columns") or "").split(",")
            if item.strip()
        ]
        result = base.structured_data_service().import_rows(
            owner,
            project,
            table,
            str(form.get("content") or ""),
            format=str(form.get("format") or "json"),
            conflict_columns=conflicts or None,
            actor="human:webgui",
            reason="WebGUI import",
        )
        message = f"Imported {result['imported']} rows"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return _redirect(project, table, message)


async def set_override(base: Any, request: Request):
    form, error, owner, project, table = await _form(base, request)
    if error:
        return error
    try:
        base.structured_data_service().set_override(
            owner,
            project,
            table,
            str(form.get("row_key") or ""),
            str(form.get("field_name") or ""),
            _parse_json(form.get("value_json"), label="override value"),
            priority=int(form.get("priority") or 100),
            reason=str(form.get("reason") or ""),
            actor="human:webgui",
        )
        message = "Effective-state override saved"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return _redirect(project, table, message)


async def link_memory(base: Any, request: Request):
    form, error, owner, project, table = await _form(base, request)
    if error:
        return error
    try:
        base.structured_data_service().link_memory(
            owner,
            project,
            table,
            str(form.get("memory_id") or ""),
            row_key=str(form.get("row_key") or "").strip() or None,
            relation=str(form.get("relation") or "rationale"),
            actor="human:webgui",
        )
        message = "Memory link saved"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return _redirect(project, table, message)


async def create_migration(base: Any, request: Request):
    form, error, owner, project, _table = await _form(base, request)
    if error:
        return error
    try:
        result = base.structured_data_service().create_migration(
            owner,
            project,
            str(form.get("title") or ""),
            _parse_json(form.get("operations_json"), label="operations"),
            apply=bool(form.get("apply")),
            actor="human:webgui",
        )
        message = f"Migration {result.get('sequence', '')} {result.get('status', '')}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return _redirect(project, "", message)


def _insert_route(app: Any, route: Route) -> None:
    for index, current in enumerate(app.router.routes):
        if isinstance(current, Mount):
            app.router.routes.insert(index, route)
            return
    app.router.routes.append(route)


def _extend_enterprise_nav() -> None:
    groups = []
    found = False
    for heading, links in v970.NAV_GROUPS:
        mutable = list(links)
        if any(item[0] == VIEW for item in mutable):
            found = True
        if heading == "Organize" and not found:
            insert_at = next(
                (index + 1 for index, item in enumerate(mutable) if item[0] == "projects"),
                len(mutable),
            )
            mutable.insert(insert_at, (VIEW, LABEL, "DT"))
            found = True
        groups.append((heading, tuple(mutable)))
    v970.NAV_GROUPS = tuple(groups)


def install_webgui_structured_data_v980(app: Any, base: Any) -> None:
    """Install the v9.8 Structured Data control plane with v9.8.1 loading fixes."""
    old_render = shell.render_view

    def render_with_data(bound_base: Any, core: Any, request: Request, view: str) -> str:
        if view == VIEW:
            return render_data(bound_base, request)
        return old_render(bound_base, core, request, view)

    if VIEW not in shell.VIEWS:
        shell.VIEWS = tuple(shell.VIEWS) + (VIEW,)
    if VIEW not in views.VIEWS:
        views.VIEWS = tuple(views.VIEWS) + (VIEW,)
    shell.render_view = render_with_data
    views.render_view = render_with_data
    _extend_enterprise_nav()

    if "v980-structured-data-context" not in shell.SCRIPT:
        shell.SCRIPT += r'''
<script id="v980-structured-data-context">
(() => {
  const isDataUrl=(value)=>{
    const u=new URL(value || location.href, location.href);
    return (u.hash||'').slice(1)==='data' || u.searchParams.get('ui_view')==='data';
  };
  const sync=(value)=>{
    if(!isDataUrl(value))return;
    const t=document.querySelector('[data-v970-context-title]');
    const s=document.querySelector('[data-v970-context-subtitle]');
    if(t && t.textContent!=='Structured Data')t.textContent='Structured Data';
    if(s && s.textContent!=='Project-scoped relational facts, schemas and provenance'){
      s.textContent='Project-scoped relational facts, schemas and provenance';
    }
  };
  addEventListener('hashchange',()=>sync(location.href));
  addEventListener('popstate',()=>sync(location.href));
  document.addEventListener('click',(event)=>{
    const node=event.target instanceof Element ? event.target.closest('a[href]') : null;
    if(node && isDataUrl(node.href))sync(node.href);
  },true);
  sync(location.href);
})();
</script>
'''

    handlers = (
        ("/dashboard/data/table/create", create_table, "v980_data_create_table"),
        ("/dashboard/data/table/add-column", add_column, "v980_data_add_column"),
        ("/dashboard/data/row/insert", insert_row, "v980_data_insert_row"),
        ("/dashboard/data/import", import_rows, "v980_data_import"),
        ("/dashboard/data/override", set_override, "v980_data_override"),
        ("/dashboard/data/memory-link", link_memory, "v980_data_memory_link"),
        ("/dashboard/data/migration/create", create_migration, "v980_data_migration"),
    )
    existing = {getattr(route, "path", None) for route in app.router.routes}
    for path, handler, name in handlers:
        if path in existing:
            continue

        async def endpoint(request: Request, _handler=handler):
            if request.method != "POST":
                return PlainTextResponse(
                    "Method Not Allowed",
                    status_code=405,
                    headers={"Allow": "POST"},
                )
            return await _handler(base, request)

        _insert_route(app, Route(path, endpoint, methods=["POST"], name=name))


__all__ = ["VIEW", "render_data", "install_webgui_structured_data_v980"]
