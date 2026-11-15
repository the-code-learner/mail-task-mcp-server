from __future__ import annotations

import os
from html import escape
from typing import Any

from starlette.requests import Request

from .webgui_helpers import (
    dashboard_url, owner_options, project_filter_html, project_label_html,
    project_legend_html, project_options, project_rows, selected_project,
)


def _file_size_label(value: Any) -> str:
    """Display-friendly file sizes, keeping raw bytes in file metadata."""
    size = max(0, int(value or 0))
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MiB"


def _file_type_icon(filename: str, mime: str) -> tuple[str, str]:
    media = mime.split(";", 1)[0].lower().strip()
    if media == "application/pdf" or filename.lower().endswith(".pdf"):
        return "PDF", "pm-drive-pdf"
    if media.startswith("image/"):
        return "IMG", "pm-drive-image"
    if media.startswith("text/") or media in {
        "application/json", "application/xml", "application/yaml",
        "application/x-yaml",
    }:
        return "TXT", "pm-drive-text"
    if media.startswith("audio/"):
        return "AUD", ""
    if media.startswith("video/"):
        return "VID", ""
    return "FILE", ""


def files_fragment(base: Any, request: Request) -> str:
    """Responsive file manager with *inline, authenticated* previews.

    Preview URLs are independent of the download action: opening a file
    never triggers the browser download route. Store, CRUD and MCP API are
    preserved, and project scope is still applied on the server.
    """
    from urllib.parse import quote
    from .webgui_files_v984 import preview_supported

    project = selected_project(request)
    projects = project_rows(base)
    project_names = {
        str(row.get("id") or ""): str(row.get("name") or row.get("id") or "")
        for row in projects
    }
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
    csrf = escape(str(base._csrf_value()), quote=True)
    total = int(status.get("files", 0)) if isinstance(status, dict) else len(files)
    logical = int(status.get("logical_bytes", 0)) if isinstance(status, dict) else 0
    max_bytes = int(status.get("max_bytes_per_file", 0)) if isinstance(status, dict) else 0
    project_options_filter = ['<option value="all">All projects</option>']
    visible_project_ids = {str(row.get("project_id") or "") for row in files}
    for row in projects:
        pid = str(row.get("id") or "")
        if pid in visible_project_ids:
            label = str(row.get("name") or pid)
            project_options_filter.append(
                f'<option value="{escape(pid, quote=True)}">{escape(label)}</option>'
            )
    if "" in visible_project_ids:
        project_options_filter.append('<option value="__global__">Global / owner-wide</option>')

    rows: list[str] = []
    for stored in files:
        file_id = str(stored.get("id") or "")
        filename = str(stored.get("filename") or file_id or "Untitled file")
        file_project = str(stored.get("project_id") or "")
        project_label = project_names.get(file_project, file_project) if file_project else "Global / owner-wide"
        media = str(stored.get("media_type") or "application/octet-stream")
        description = str(stored.get("description") or "")
        tags = ", ".join(str(t) for t in stored.get("tags") or [])
        created = str(stored.get("created_at") or "")
        size_bytes = max(0, int(stored.get("size_bytes") or 0))
        size_label = _file_size_label(size_bytes)
        icon, icon_class = _file_type_icon(filename, media)
        filename_safe = escape(filename)
        file_url_id = quote(file_id, safe="")
        preview_url = f"/dashboard/files/{file_url_id}/preview"
        download_url = f"/dashboard/files/{file_url_id}/download"
        supported = preview_supported(media)
        searchable = " ".join((filename, description, tags, project_label)).casefold()
        project_scope = project_label_html(project_label, file_project or None, compact=True)
        if description:
            detail = description[:165]
        else:
            detail = tags[:120] if tags else media
        created_label = created[:10] if created else ""
        subtitle = f"{created_label} · {detail}" if created_label else detail
        rows.append(
            f'<tr tabindex="0" class="pm-drive-row"'
            f' data-pm-file-id="{escape(file_id, quote=True)}"'
            f' data-pm-name="{escape(filename, quote=True)}"'
            f' data-pm-project="{escape(file_project or "__global__", quote=True)}"'
            f' data-pm-project-label="{escape(project_label, quote=True)}"'
            f' data-pm-media="{escape(media, quote=True)}"'
            f' data-pm-size="{size_bytes}" data-pm-size-label="{escape(size_label)}"'
            f' data-pm-created="{escape(created, quote=True)}"'
            f' data-pm-description="{escape(description[:400], quote=True)}"'
            f' data-pm-tags="{escape(tags[:400], quote=True)}"'
            f' data-pm-search="{escape(searchable[:1200], quote=True)}"'
            f' data-pm-preview="{escape(preview_url, quote=True)}"'
            f' data-pm-download="{escape(download_url, quote=True)}"'
            f' data-pm-preview-supported="{"1" if supported else "0"}">'
            '<td><a class="pm-drive-file-name" data-pm-preview-link'
            f' href="{escape(preview_url, quote=True)}" target="_blank" rel="noopener"'
            f' aria-label="Preview {escape(filename, quote=True)}">'
            f'<span class="pm-drive-file-icon {icon_class}" aria-hidden="true">{icon}</span>'
            '<span class="pm-drive-file-title-wrap">'
            f'<span class="pm-drive-title">{filename_safe}</span>'
            f'<span class="pm-drive-file-sub">{escape(subtitle)}</span>'
            '</span></a></td>'
            f'<td class="pm-drive-cell-project"><span class="pm-drive-project">{project_scope}</span></td>'
            f'<td class="pm-drive-cell-size"><span class="pm-drive-size">{escape(size_label)}</span></td>'
            '<td class="pm-drive-cell-actions"><div class="pm-drive-actions">'
            f'<a class="pm-drive-download" href="{escape(download_url, quote=True)}"'
            f' aria-label="Download {escape(filename, quote=True)}">↓ Download</a>'
            '<details class="pm-drive-menu"><summary aria-label="More file actions">⋯</summary>'
            '<div class="pm-drive-menu-inner">'
            f'<a data-pm-preview-link href="{escape(preview_url, quote=True)}"'
            ' target="_blank" rel="noopener">Preview</a>'
            f'<button type="button" data-pm-copy-id="{escape(file_id, quote=True)}">Copy file ID</button>'
            '<form method="post" action="/dashboard/files/delete"'
            ' onsubmit="return confirm(\'Delete this stored file?\');">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<input type="hidden" name="file_id" value="{escape(file_id, quote=True)}">'
            '<button class="pm-drive-delete" type="submit">Delete file</button></form>'
            '</div></details></div></td></tr>'
        )

    scope_name = project_names.get(project or "", project or "") if project else ""
    scope_hint = (
        f" · Project: {escape(scope_name)}" if project else " · All project scopes"
    )
    file_limit = _file_size_label(max_bytes) if max_bytes else "configured store limit"
    from .webgui_helpers import project_options, owner_options
    return f'''<section class="tab-panel" id="panel-files" data-panel="files">
<div class="pm-drive-root">
  <div class="pm-drive-head">
    <div>
      <div class="pm-drive-kicker">Postmaster MCP / Small-file store</div>
      <h2>Files</h2>
      <div class="pm-drive-summary"><strong>{total}</strong> stored globally · {_file_size_label(logical)} logical storage{scope_hint}<br>
      <span class="small muted">{len(files)} loaded for browsing · Per-file limit: {escape(file_limit)}. Project filtering is WebGUI-only.</span></div>
    </div>
    <details class="pm-drive-upload">
      <summary class="pm-drive-primary">＋ Upload file</summary>
      <div class="pm-drive-upload-shade" data-pm-upload-shade aria-hidden="true"></div>
      <section class="pm-drive-upload-pane" aria-label="Upload file">
        <header><h3>Upload file</h3><button type="button" class="pm-drive-close" data-pm-close-upload aria-label="Close upload">✕</button></header>
        <p class="small muted">The file is saved in Postmaster storage and stays accessible through MCP.</p>
        <form method="post" action="/dashboard/files/upload" enctype="multipart/form-data">
          <input type="hidden" name="csrf" value="{csrf}">
          <div class="pm-drive-drop">
            <div class="pm-drive-drop-title">Choose a file for storage</div>
            <div class="small muted">Maximum {escape(file_limit)} per file</div>
            <input type="file" name="file" required aria-label="File to upload">
          </div>
          <div class="pm-drive-field"><label for="pm-drive-owner">Owner</label>
            <select id="pm-drive-owner" name="owner_id" required>{owner_options(owners, selected_owner)}</select></div>
          <div class="pm-drive-field"><label for="pm-drive-project">Project</label>
            <select id="pm-drive-project" name="project_id">{project_options(projects, project)}</select></div>
          <div class="pm-drive-field"><label for="pm-drive-description">Description (optional)</label>
            <input id="pm-drive-description" type="text" name="description" maxlength="1000"></div>
          <div class="pm-drive-field"><label for="pm-drive-tags">Tags (comma separated)</label>
            <input id="pm-drive-tags" type="text" name="tags" placeholder="cv, outreach, approved"></div>
          <button class="pm-drive-primary pm-drive-upload-submit" type="submit">Upload to storage</button>
        </form>
      </section>
    </details>
  </div>
  <div class="pm-drive-main">
    <div class="pm-drive-toolbar">
      <label class="pm-drive-search"><span aria-hidden="true">⌕</span><input type="search" data-pm-search
        placeholder="Search names, tags and descriptions..." aria-label="Search stored files"></label>
      <select data-pm-project-filter aria-label="Filter by project">{''.join(project_options_filter)}</select>
      <select data-pm-sort aria-label="Sort files">
        <option value="recent">Newest first</option><option value="name">Name A–Z</option>
        <option value="size">Largest first</option>
      </select>
      <button type="button" class="pm-drive-layout" data-pm-layout-toggle aria-pressed="false">▦ Grid</button>
    </div>
    <div class="pm-drive-inventory-head"><strong>Stored files</strong>
      <span data-pm-visible-count>{len(files)} shown of {len(files)} loaded</span></div>
    <div class="pm-drive-table-wrap" data-pm-table-wrap>
      <table class="pm-drive-table">
      <thead><tr><th>File</th><th>Project</th><th>Size</th><th>Actions</th></tr></thead>
      <tbody data-pm-list>{''.join(rows)}</tbody></table>
      <div class="pm-drive-empty" data-pm-empty {"hidden" if files else ""}>
        No matching files. Clear the search or choose another project.
      </div>
    </div>
    <div class="pm-drive-foot"><span>Preview opens here without downloading · Download is separate</span>
      <span>{len(files)} shown · {total} stored globally</span></div>
    <p class="pm-drive-status" data-pm-status role="status" aria-live="polite"></p>
  </div>
  <div class="pm-drive-preview-shade" data-pm-preview-shade hidden></div>
  <aside class="pm-drive-preview-pane" data-pm-preview-pane hidden
    role="dialog" aria-modal="true" aria-label="Stored file preview">
    <header><h3>File preview</h3><button type="button" class="pm-drive-close"
      data-pm-close-preview aria-label="Close preview">✕</button></header>
    <div class="pm-drive-preview-name" data-pm-detail-name></div>
    <div class="pm-drive-preview-description" data-pm-detail-description></div>
    <div class="pm-drive-preview-stage">
      <iframe class="pm-drive-preview-frame" data-pm-preview-frame title="Preview of stored file" sandbox
        referrerpolicy="no-referrer" loading="lazy" hidden></iframe>
      <div class="pm-drive-preview-unavailable" data-pm-preview-unavailable hidden>
        In-browser preview is unavailable for this file type. The file remains
        stored securely. Use Download to open it in a compatible app.
      </div>
    </div>
    <dl class="pm-drive-preview-meta">
      <div class="pm-drive-meta-row"><dt>Project</dt><dd data-pm-detail-project></dd></div>
      <div class="pm-drive-meta-row"><dt>Size</dt><dd data-pm-detail-size></dd></div>
      <div class="pm-drive-meta-row"><dt>File type</dt><dd data-pm-detail-kind></dd></div>
      <div class="pm-drive-meta-row"><dt>Tags</dt><dd data-pm-detail-tags></dd></div>
      <div class="pm-drive-meta-row"><dt>File ID</dt><dd class="mono" data-pm-detail-id></dd></div>
    </dl>
    <div class="pm-drive-preview-actions">
      <a class="pm-drive-download" data-pm-direct-download href="#">↓ Download file</a>
      <a data-pm-open-tab href="#" target="_blank" rel="noopener">Open preview in new tab</a>
    </div>
  </aside>
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
            name = project_label_html(str(row.get("name") or pid), pid)
            cards.append(
                f'<section class="card"><div class="panel-title"><h2>{name}</h2><a href="{open_url}"><button type="button">Open</button></a></div>'
                f'<div class="small muted mono">{escape(str(row.get("owner_id") or ""))} / {escape(pid)}</div>'
                f'{description_html}</section>'
            )
        legend = project_legend_html(projects, [row.get("id") for row in projects])
        return f'''<section class="tab-panel" id="panel-projects" data-panel="projects"><div class="grid">
<section class="card wide"><div class="panel-title"><h2>Projects</h2><span class="badge">{len(projects)} total</span></div>{selector}{legend}<p class="small muted">Select one project to see its Tasks, Knowledge and Files together. The existing owner/project registry remains authoritative.</p></section>
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
        title = project_label_html(str(row.get("title") or ""), project)
        task_rows.append(
            f'<tr><td>{title}<div class="small muted mono">{escape(jid)}</div></td><td><span class="badge">{escape(str(row.get("status") or ""))}</span></td><td class="mono">{escape(str(row.get("next_run_utc") or "—"))}</td><td class="actions">{actions}</td></tr>'
        )

    knowledge_rows = []
    for row in knowledge:
        iid = str(row.get("id") or "")
        view = escape(dashboard_url(request, tab="knowledge", project=project, extra={"view_knowledge": iid}), quote=True)
        edit = escape(dashboard_url(request, tab="knowledge", project=project, extra={"edit_knowledge": iid}), quote=True)
        title = project_label_html(str(row.get("title") or ""), project)
        knowledge_rows.append(
            f'<tr><td>{title}<div class="small muted mono">{escape(iid)}</div></td><td><span class="badge">{escape(str(row.get("kind") or ""))}</span></td><td>{float(row.get("priority") or 0.0):.2f}</td><td class="actions"><a href="{view}"><button type="button">View</button></a><a href="{edit}"><button type="button">Edit</button></a></td></tr>'
        )

    file_rows = []
    for row in files:
        fid = str(row.get("id") or "")
        filename = project_label_html(str(row.get("filename") or ""), project)
        file_rows.append(
            f'<tr><td><a href="/dashboard/files/{escape(fid)}/preview" target="_blank" rel="noopener">{filename}</a><div class="small muted mono">{escape(fid)}</div></td><td>{escape(str(row.get("media_type") or ""))}</td><td>{int(row.get("size_bytes") or 0)} bytes</td><td class="actions"><a href="/dashboard/files/{escape(fid)}/download"><button type="button">Download</button></a></td></tr>'
        )

    task_tab = escape(dashboard_url(request, tab="scheduler", project=project), quote=True)
    knowledge_tab = escape(dashboard_url(request, tab="knowledge", project=project), quote=True)
    files_tab = escape(dashboard_url(request, tab="files", project=project), quote=True)
    project_name = project_label_html(name, project)
    project_scope = project_label_html(name, project, compact=True)
    return f'''<section class="tab-panel" id="panel-projects" data-panel="projects"><div class="grid">
<section class="card wide"><div class="panel-title"><div><h2>{project_name}</h2><div class="small muted mono">{escape(owner)} / {escape(project)}</div></div><span class="badge">Project overview</span></div>{selector}
<div class="project-key">{project_scope}</div><div class="project-summary"><div><strong>{len(tasks)}</strong><span>tasks</span></div><div><strong>{due}</strong><span>due</span></div><div><strong>{completed}</strong><span>completed</span></div><div><strong>{len(knowledge)}</strong><span>knowledge</span></div><div><strong>{len(files)}</strong><span>files</span></div></div></section>
<section class="card wide"><div class="panel-title"><h2>Tasks</h2><a href="{task_tab}"><button type="button">Open Tasks</button></a></div><div class="scroll"><table><thead><tr><th>Task</th><th>Status</th><th>Next UTC</th><th></th></tr></thead><tbody>{''.join(task_rows) or '<tr><td colspan="4" class="muted">No tasks in this project</td></tr>'}</tbody></table></div></section>
<section class="card wide"><div class="panel-title"><h2>Knowledge</h2><a href="{knowledge_tab}"><button type="button">Open Knowledge</button></a></div><div class="scroll"><table><thead><tr><th>Item</th><th>Kind</th><th>Priority</th><th></th></tr></thead><tbody>{''.join(knowledge_rows) or '<tr><td colspan="4" class="muted">No project-scoped knowledge</td></tr>'}</tbody></table></div></section>
<section class="card wide"><div class="panel-title"><h2>Files</h2><a href="{files_tab}"><button type="button">Open Files</button></a></div><div class="scroll"><table><thead><tr><th>File</th><th>Type</th><th>Size</th><th></th></tr></thead><tbody>{''.join(file_rows) or '<tr><td colspan="4" class="muted">No project-scoped files</td></tr>'}</tbody></table></div></section>
</div></section>'''
