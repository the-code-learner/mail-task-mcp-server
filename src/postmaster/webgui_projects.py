from __future__ import annotations

import os
from html import escape
from typing import Any

from starlette.requests import Request

from .webgui_helpers import (
    dashboard_url, owner_options, project_filter_html, project_options,
    project_rows, selected_project,
)


def files_fragment(base: Any, request: Request) -> str:
    project = selected_project(request)
    projects = project_rows(base)
    owners_result = base._safe_call(base.scheduler().list_owners)
    owners = owners_result if isinstance(owners_result, list) else []
    store = base.file_store()
    status = base._safe_call(store.status)
    result = (
        base._safe_call(store.list_files, project_id=project, include_global=False, limit=500)
        if project else base._safe_call(store.list_files, limit=500)
    )
    files = result if isinstance(result, list) else []
    project_meta = next((row for row in projects if str(row.get("id") or "") == project), {})
    selected_owner = str(project_meta.get("owner_id") or os.getenv("DEFAULT_OWNER_ID", ""))
    rows = []
    for stored in files:
        file_id = str(stored.get("id") or "")
        rows.append(
            f'<tr><td><strong>{escape(str(stored.get("filename") or ""))}</strong><div class="small muted mono">{escape(file_id)}</div><div class="small muted">{escape(str(stored.get("description") or ""))}</div></td>'
            f'<td>{escape(str(stored.get("owner_id") or ""))}<div class="small muted">{escape(str(stored.get("project_id") or "global"))}</div></td>'
            f'<td class="mono small">{escape(str(stored.get("media_type") or "application/octet-stream"))}<div class="muted">{int(stored.get("size_bytes") or 0)} bytes</div></td>'
            f'<td class="small">{escape(", ".join(stored.get("tags") or []))}</td>'
            f'<td class="actions"><a href="/dashboard/files/{escape(file_id)}/download"><button type="button">Download</button></a>'
            f'<form method="post" action="/dashboard/files/delete" onsubmit="return confirm(\'Delete this stored file?\');"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}"><input type="hidden" name="file_id" value="{escape(file_id)}"><button class="danger" type="submit">Delete</button></form></td></tr>'
        )
    total = int(status.get("files", 0)) if isinstance(status, dict) else len(files)
    logical = int(status.get("logical_bytes", 0)) if isinstance(status, dict) else 0
    max_bytes = int(status.get("max_bytes_per_file", 0)) if isinstance(status, dict) else 0
    filter_html = project_filter_html(request, tab="files", selected=project, projects=projects)
    return f'''<section class="tab-panel" id="panel-files" data-panel="files"><div class="grid">
<section class="card"><h2>Small-file store</h2><div><strong>{len(files)}</strong> shown · <strong>{total}</strong> stored globally</div><div><strong>{logical}</strong> logical bytes</div><div class="small muted">Per-file limit: {max_bytes} bytes. Project filtering is WebGUI-only.</div></section>
<section class="card wide"><div class="panel-title"><h2>Upload file</h2><span class="small muted">owner/project scopes reuse the Tasks registry</span></div>{filter_html}
<form method="post" action="/dashboard/files/upload" enctype="multipart/form-data"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}">
<div class="row"><div class="field"><label>Owner</label><select name="owner_id" required>{owner_options(owners, selected_owner)}</select></div><div class="field"><label>Project</label><select name="project_id">{project_options(projects, project)}</select></div><div class="field grow"><label>File</label><input type="file" name="file" required></div></div>
<div class="row" style="margin-top:10px"><div class="field grow"><label>Description</label><input type="text" name="description"></div><div class="field grow"><label>Tags (comma separated)</label><input type="text" name="tags"></div><button class="primary" type="submit">Upload</button></div></form></section>
<section class="card wide"><div class="panel-title"><h2>Stored files</h2><span class="badge">{len(files)} shown</span></div><div class="scroll"><table><thead><tr><th>File</th><th>Owner / project</th><th>Type / size</th><th>Tags</th><th></th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5" class="muted">No stored files in this project filter</td></tr>'}</tbody></table></div></section>
</div></section>'''


def project_overview_fragment(base: Any, request: Request) -> str:
    projects = project_rows(base)
    project = selected_project(request)
    selector = project_filter_html(request, tab="projects", selected=project, projects=projects)
    if not project:
        cards = []
        for row in projects:
            pid = str(row.get("id") or "")
            open_url = escape(dashboard_url(request, tab="projects", project=pid), quote=True)
            description = str(row.get("description") or "")
            description_html = f'<p class="small">{escape(description)}</p>' if description else ""
            cards.append(
                f'<section class="card"><div class="panel-title"><h2>{escape(str(row.get("name") or pid))}</h2><a href="{open_url}"><button type="button">Open</button></a></div>'
                f'<div class="small muted mono">{escape(str(row.get("owner_id") or ""))} / {escape(pid)}</div>'
                f'{description_html}</section>'
            )
        return f'''<section class="tab-panel" id="panel-projects" data-panel="projects"><div class="grid">
<section class="card wide"><div class="panel-title"><h2>Projects</h2><span class="badge">{len(projects)} total</span></div>{selector}<p class="small muted">Select one project to see its Tasks, Knowledge and Files together. The existing owner/project registry remains authoritative.</p></section>
{''.join(cards) or '<section class="card wide"><span class="muted">No projects registered</span></section>'}</div></section>'''

    tasks_result = base._safe_call(base.scheduler().list_jobs, project_id=project, limit=1000)
    tasks = tasks_result if isinstance(tasks_result, list) else []
    knowledge_result = base._safe_call(
        base.context_engine().store.list_items,
        project_id=project, include_global=False, limit=1000,
    )
    knowledge = knowledge_result if isinstance(knowledge_result, list) else []
    files_result = base._safe_call(
        base.file_store().list_files,
        project_id=project, include_global=False, limit=1000,
    )
    files = files_result if isinstance(files_result, list) else []
    due_result = base._safe_call(base.scheduler().list_due_jobs, project_id=project, limit=1000)
    due = len(due_result) if isinstance(due_result, list) else 0
    completed = sum(1 for row in tasks if str(row.get("status") or "") == "completed")
    meta = next((row for row in projects if str(row.get("id") or "") == project), {})
    name = str(meta.get("name") or project)
    owner = str(meta.get("owner_id") or "")

    task_rows = []
    for row in tasks:
        jid = str(row.get("id") or "")
        view = escape(dashboard_url(request, tab="scheduler", project=project, extra={"view_job": jid, "show_completed": "1"}), quote=True)
        actions = f'<a href="{view}"><button type="button">View</button></a>'
        if str(row.get("status") or "") != "completed":
            edit = escape(dashboard_url(request, tab="scheduler", project=project, extra={"edit_job": jid, "show_completed": "1"}), quote=True)
            actions += f'<a href="{edit}"><button type="button">Edit</button></a>'
        task_rows.append(
            f'<tr><td><strong>{escape(str(row.get("title") or ""))}</strong><div class="small muted mono">{escape(jid)}</div></td><td><span class="badge">{escape(str(row.get("status") or ""))}</span></td><td class="mono">{escape(str(row.get("next_run_utc") or "—"))}</td><td class="actions">{actions}</td></tr>'
        )

    knowledge_rows = []
    for row in knowledge:
        iid = str(row.get("id") or "")
        view = escape(dashboard_url(request, tab="knowledge", project=project, extra={"view_knowledge": iid}), quote=True)
        edit = escape(dashboard_url(request, tab="knowledge", project=project, extra={"edit_knowledge": iid}), quote=True)
        knowledge_rows.append(
            f'<tr><td><strong>{escape(str(row.get("title") or ""))}</strong><div class="small muted mono">{escape(iid)}</div></td><td><span class="badge">{escape(str(row.get("kind") or ""))}</span></td><td>{float(row.get("priority") or 0.0):.2f}</td><td class="actions"><a href="{view}"><button type="button">View</button></a><a href="{edit}"><button type="button">Edit</button></a></td></tr>'
        )

    file_rows = []
    for row in files:
        fid = str(row.get("id") or "")
        file_rows.append(
            f'<tr><td><strong>{escape(str(row.get("filename") or ""))}</strong><div class="small muted mono">{escape(fid)}</div></td><td>{escape(str(row.get("media_type") or ""))}</td><td>{int(row.get("size_bytes") or 0)} bytes</td><td class="actions"><a href="/dashboard/files/{escape(fid)}/download"><button type="button">Download</button></a></td></tr>'
        )

    task_tab = escape(dashboard_url(request, tab="scheduler", project=project), quote=True)
    knowledge_tab = escape(dashboard_url(request, tab="knowledge", project=project), quote=True)
    files_tab = escape(dashboard_url(request, tab="files", project=project), quote=True)
    return f'''<section class="tab-panel" id="panel-projects" data-panel="projects"><div class="grid">
<section class="card wide"><div class="panel-title"><div><h2>{escape(name)}</h2><div class="small muted mono">{escape(owner)} / {escape(project)}</div></div><span class="badge">Project overview</span></div>{selector}
<div class="project-summary"><div><strong>{len(tasks)}</strong><span>tasks</span></div><div><strong>{due}</strong><span>due</span></div><div><strong>{completed}</strong><span>completed</span></div><div><strong>{len(knowledge)}</strong><span>knowledge</span></div><div><strong>{len(files)}</strong><span>files</span></div></div></section>
<section class="card wide"><div class="panel-title"><h2>Tasks</h2><a href="{task_tab}"><button type="button">Open Tasks</button></a></div><div class="scroll"><table><thead><tr><th>Task</th><th>Status</th><th>Next UTC</th><th></th></tr></thead><tbody>{''.join(task_rows) or '<tr><td colspan="4" class="muted">No tasks in this project</td></tr>'}</tbody></table></div></section>
<section class="card wide"><div class="panel-title"><h2>Knowledge</h2><a href="{knowledge_tab}"><button type="button">Open Knowledge</button></a></div><div class="scroll"><table><thead><tr><th>Item</th><th>Kind</th><th>Priority</th><th></th></tr></thead><tbody>{''.join(knowledge_rows) or '<tr><td colspan="4" class="muted">No project-scoped knowledge</td></tr>'}</tbody></table></div></section>
<section class="card wide"><div class="panel-title"><h2>Files</h2><a href="{files_tab}"><button type="button">Open Files</button></a></div><div class="scroll"><table><thead><tr><th>File</th><th>Type</th><th>Size</th><th></th></tr></thead><tbody>{''.join(file_rows) or '<tr><td colspan="4" class="muted">No project-scoped files</td></tr>'}</tbody></table></div></section>
</div></section>'''
