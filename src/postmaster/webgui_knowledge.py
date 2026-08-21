from __future__ import annotations

import os
from html import escape
from typing import Any

from starlette.requests import Request

from .webgui_helpers import (
    dashboard_url, owner_options, project_filter_html, project_options,
    project_rows, render_markdown_safe, selected_project,
)


def _knowledge_items(base: Any, project: str | None) -> list[dict[str, Any]]:
    if project:
        result = base._safe_call(
            base.context_engine().store.list_items,
            project_id=project, include_global=False, limit=500,
        )
    else:
        result = base._safe_call(base.context_engine().store.list_items, limit=500)
    return result if isinstance(result, list) else []


def _view(base: Any, request: Request, project: str | None) -> str:
    item_id = (request.query_params.get("view_knowledge") or "").strip()
    if not item_id:
        return ""
    item = base._safe_call(base.context_engine().store.get_item, item_id)
    close = escape(dashboard_url(request, tab="knowledge", project=project), quote=True)
    if not isinstance(item, dict) or item.get("ok") is False:
        error = str(item.get("error") or "Knowledge item could not be loaded") if isinstance(item, dict) else "Knowledge item could not be loaded"
        return f'<section class="card wide"><div class="panel-title"><h2>Knowledge view</h2><a href="{close}"><button type="button">Close</button></a></div><div class="flash">{escape(error)}</div></section>'
    edit = escape(
        dashboard_url(request, tab="knowledge", project=project, extra={"edit_knowledge": item_id}),
        quote=True,
    )
    title = escape(str(item.get("title") or ""))
    kind = escape(str(item.get("kind") or ""))
    owner = escape(str(item.get("owner_id") or ""))
    scope = escape(str(item.get("project_id") or "global"))
    tags = escape(", ".join(item.get("tags") or []))
    rendered = render_markdown_safe(str(item.get("content") or ""))
    return f'''<section class="card wide">
<div class="panel-title"><div><h2>{title}</h2><div class="small muted"><span class="badge">{kind}</span> {owner} / {scope} · {tags}</div></div>
<div class="row"><a href="{edit}"><button type="button">Edit raw source</button></a><a href="{close}"><button type="button">Close</button></a></div></div>
<div class="markdown-viewer">{rendered}</div></section>'''


def knowledge_fragment(base: Any, request: Request) -> str:
    project = selected_project(request)
    projects = project_rows(base)
    owners_result = base._safe_call(base.scheduler().list_owners)
    owners = owners_result if isinstance(owners_result, list) else []
    status = base._safe_call(base.context_engine().status)
    items = _knowledge_items(base, project)
    query = (request.query_params.get("knowledge_q") or "").strip()
    if query:
        search = base._safe_call(
            base.context_engine().search,
            query,
            project_id=project,
            include_global=not bool(project),
            limit=50,
        )
        search_results = search.get("results", []) if isinstance(search, dict) else []
        if project:
            search_results = [row for row in search_results if str(row.get("project_id") or "") == project]
    else:
        search_results = []

    edit_item = None
    edit_id = (request.query_params.get("edit_knowledge") or "").strip()
    if edit_id:
        candidate = base._safe_call(base.context_engine().store.get_item, edit_id)
        edit_item = candidate if isinstance(candidate, dict) and candidate.get("ok") is not False else None

    rows = []
    for item in items:
        item_id = str(item.get("id") or "")
        kind = str(item.get("kind") or "")
        view = escape(dashboard_url(request, tab="knowledge", project=project, extra={"view_knowledge": item_id}), quote=True)
        edit = escape(dashboard_url(request, tab="knowledge", project=project, extra={"edit_knowledge": item_id}), quote=True)
        flags = []
        if item.get("always_include"):
            flags.append('<span class="badge warn">always</span>')
        flags.append('<span class="badge ok">enabled</span>' if item.get("enabled") else '<span class="badge">disabled</span>')
        rows.append(
            f'<tr><td><strong>{escape(str(item.get("title") or ""))}</strong><div class="small muted mono">{escape(item_id)}</div><div class="small muted">{escape(", ".join(item.get("tags") or []))}</div></td>'
            f'<td><span class="badge">{escape(kind)}</span></td><td>{escape(str(item.get("owner_id") or ""))}<div class="small muted">{escape(str(item.get("project_id") or "global"))}</div></td>'
            f'<td>{float(item.get("priority") or 0.0):.2f}<div>{" ".join(flags)}</div></td>'
            f'<td class="actions"><a href="{view}"><button type="button">View</button></a><a href="{edit}"><button type="button">Edit</button></a>'
            f'<form method="post" action="/dashboard/knowledge/delete" onsubmit="return confirm(\'Delete this memory/skill?\');"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}"><input type="hidden" name="item_id" value="{escape(item_id)}"><button class="danger" type="submit">Delete</button></form></td></tr>'
        )

    search_rows = []
    for item in search_results:
        iid = escape(str(item.get("item_id") or item.get("id") or ""))
        search_rows.append(
            f'<tr><td><strong>{escape(str(item.get("title") or ""))}</strong><div class="small muted mono">{iid}</div></td>'
            f'<td><span class="badge">{escape(str(item.get("kind") or ""))}</span></td>'
            f'<td>{float(item.get("score") or 0.0):.5f}</td>'
            f'<td class="small">{escape(str(item.get("best_chunk") or item.get("content") or "")[:500])}</td></tr>'
        )

    current = edit_item or {}
    selected_owner = str(current.get("owner_id") or os.getenv("DEFAULT_OWNER_ID", ""))
    selected_form_project = str(current.get("project_id") or project or "") or None
    kind = str(current.get("kind") or "memory")
    if edit_item:
        kind_control = f'<input type="hidden" name="kind" value="{escape(kind)}"><div class="mono">{escape(kind)}</div>'
    else:
        kind_control = '<select name="kind"><option value="memory">Memory</option><option value="skill">Skill</option></select>'
    title = escape(str(current.get("title") or ""))
    tags = escape(", ".join(current.get("tags") or []))
    content = escape(str(current.get("content") or ""))
    priority = escape(str(current.get("priority", 0.5)))
    always = " checked" if current.get("always_include") else ""
    enabled = " checked" if (not edit_item or current.get("enabled")) else ""
    item_id_value = escape(str(current.get("id") or ""))
    cancel = escape(dashboard_url(request, tab="knowledge", project=project), quote=True)
    filter_html = project_filter_html(request, tab="knowledge", selected=project, projects=projects)
    semantic = status.get("semantic", {}) if isinstance(status, dict) else {}
    memory_count = sum(1 for item in items if item.get("kind") == "memory")
    skill_count = sum(1 for item in items if item.get("kind") == "skill")
    missing = int(status.get("missing_embeddings", 0)) if isinstance(status, dict) else 0
    account = (request.query_params.get("account") or "").strip()
    hidden = ""
    if project:
        hidden += f'<input type="hidden" name="project" value="{escape(project)}">'
    if account:
        hidden += f'<input type="hidden" name="account" value="{escape(account)}">'
    semantic_badge = '<span class="badge ok">Model2Vec ready</span>' if semantic.get("available") else '<span class="badge warn">Model2Vec unavailable / FTS fallback</span>'

    return f'''<section class="tab-panel" id="panel-knowledge" data-panel="knowledge"><div class="grid">
{_view(base, request, project)}
<section class="card"><h2>Context engine</h2><div><strong>{memory_count}</strong> memories · <strong>{skill_count}</strong> skills shown</div>
<div class="row" style="margin-top:10px"><span class="badge ok">FTS5</span>{semantic_badge}</div>
<form method="post" action="/dashboard/knowledge/reindex" style="margin-top:12px"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}"><button type="submit">Reindex embeddings ({missing} missing)</button></form></section>

<section class="card wide"><div class="panel-title"><h2>{"Edit knowledge item" if edit_item else "Add memory / skill"}</h2><span class="small muted">Raw Markdown source remains editable here</span></div>
{filter_html}
<form method="post" action="/dashboard/knowledge/save"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}"><input type="hidden" name="item_id" value="{item_id_value}">
<div class="row"><div class="field"><label>Kind</label>{kind_control}</div><div class="field"><label>Owner</label><select name="owner_id" required>{owner_options(owners, selected_owner)}</select></div>
<div class="field"><label>Project</label><select name="project_id">{project_options(projects, selected_form_project)}</select></div><div class="field"><label>Priority 0–1</label><input type="number" name="priority" min="0" max="1" step="0.05" value="{priority}"></div></div>
<div class="row" style="margin-top:10px"><div class="field grow"><label>Title</label><input type="text" name="title" value="{title}" required></div><div class="field grow"><label>Tags (comma separated)</label><input type="text" name="tags" value="{tags}"></div></div>
<div class="field" style="margin-top:10px"><label>Content (Markdown)</label><textarea name="content" rows="12" required>{content}</textarea></div>
<div class="row" style="margin-top:10px"><label><input type="checkbox" name="always_include" value="1"{always}> Always include in project context</label><label><input type="checkbox" name="enabled" value="1"{enabled}> Enabled</label><button class="primary" type="submit">{"Save changes" if edit_item else "Create item"}</button>{f'<a href="{cancel}" class="muted">Cancel edit</a>' if edit_item else ""}</div></form></section>

<section class="card wide"><div class="panel-title"><h2>Search knowledge</h2><span class="small muted">Search follows the selected project filter</span></div>
<form method="get" action="/">{hidden}<div class="row"><div class="grow"><input type="text" name="knowledge_q" value="{escape(query)}" placeholder="How did we decide to build the cluster?"></div><button class="primary" type="submit">Search</button><a href="{cancel}"><button type="button">Clear</button></a></div></form>
{('<div class="scroll" style="margin-top:12px"><table><thead><tr><th>Item</th><th>Kind</th><th>Score</th><th>Best chunk</th></tr></thead><tbody>' + (''.join(search_rows) or '<tr><td colspan="4" class="muted">No matches</td></tr>') + '</tbody></table></div>' if query else '')}</section>

<section class="card wide"><div class="panel-title"><h2>Memories + Skills</h2><span class="badge">{len(items)} shown</span></div>
<div class="scroll"><table><thead><tr><th>Item</th><th>Kind</th><th>Owner / project</th><th>Priority</th><th></th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5" class="muted">No persistent context in this project filter</td></tr>'}</tbody></table></div></section>
</div></section>'''
