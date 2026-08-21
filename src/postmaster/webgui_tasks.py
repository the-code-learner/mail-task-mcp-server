from __future__ import annotations

import json
from html import escape
from typing import Any

from starlette.requests import Request

from .webgui_helpers import dashboard_url, project_filter_html, project_rows, selected_project


TASK_DETAIL_FIELDS = (
    "id", "owner_id", "project_id", "title", "description", "action_type",
    "execution_profile_id", "schedule_type", "schedule_value", "timezone",
    "approval_mode", "status", "next_run_utc", "created_at", "updated_at",
    "last_run_utc", "last_error", "payload",
)


def _detail(base: Any, request: Request, project: str | None, show_completed: bool) -> str:
    job_id = (request.query_params.get("view_job") or "").strip()
    if not job_id:
        return ""
    item = base._safe_call(base.scheduler().get_job, job_id)
    close = escape(
        dashboard_url(
            request, tab="scheduler", project=project,
            extra={"show_completed": "1" if show_completed else None},
        ),
        quote=True,
    )
    if not isinstance(item, dict) or item.get("ok") is False:
        error = str(item.get("error") or "Task could not be loaded") if isinstance(item, dict) else "Task could not be loaded"
        return f'<section class="card wide"><div class="panel-title"><h2>Task detail</h2><a href="{close}"><button type="button">Close</button></a></div><div class="flash">{escape(error)}</div></section>'
    rows = []
    for field in TASK_DETAIL_FIELDS:
        value = item.get(field)
        if field == "payload":
            rendered = json.dumps(value if value is not None else {}, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            cell = f'<pre class="mono small" style="white-space:pre-wrap;overflow-wrap:anywhere;margin:0">{escape(rendered)}</pre>'
        elif value in {None, ""}:
            cell = '<span class="muted">—</span>'
        elif field == "description":
            cell = f'<div style="white-space:pre-wrap;overflow-wrap:anywhere">{escape(str(value))}</div>'
        else:
            cell = f'<span class="mono">{escape(str(value))}</span>'
        rows.append(f"<tr><th>{escape(field)}</th><td>{cell}</td></tr>")
    return f'''<section class="card wide">
<div class="panel-title"><h2>Task detail</h2><a href="{close}"><button type="button">Close</button></a></div>
<div class="scroll"><table><tbody>{''.join(rows)}</tbody></table></div></section>'''


def _editor(base: Any, request: Request, project: str | None, show_completed: bool) -> str:
    job_id = (request.query_params.get("edit_job") or "").strip()
    if not job_id:
        return ""
    item = base._safe_call(base.scheduler().get_job, job_id)
    close = escape(
        dashboard_url(
            request, tab="scheduler", project=project,
            extra={"show_completed": "1" if show_completed else None},
        ),
        quote=True,
    )
    if not isinstance(item, dict) or item.get("ok") is False:
        return ""
    if str(item.get("status") or "") == "completed":
        return f'<section class="card wide"><div class="panel-title"><h2>Edit task</h2><a href="{close}"><button type="button">Close</button></a></div><div class="flash">Completed tasks are immutable.</div></section>'
    payload = json.dumps(item.get("payload") or {}, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    approval = str(item.get("approval_mode") or "approval_required")
    return f'''<section class="card wide">
<div class="panel-title"><h2>Edit task</h2><a href="{close}"><button type="button">Cancel</button></a></div>
<p class="small muted">WebGUI editor only. It uses the existing scheduler update fields; the public MCP/API task contract is unchanged.</p>
<form method="post" action="/dashboard/job/update">
<input type="hidden" name="csrf" value="{escape(base._csrf_value())}">
<input type="hidden" name="job_id" value="{escape(str(item.get("id") or ""))}">
<input type="hidden" name="project_filter" value="{escape(project or "")}">
<input type="hidden" name="show_completed" value="{"1" if show_completed else ""}">
<div class="row">
<div class="field grow"><label>Title</label><input type="text" name="title" value="{escape(str(item.get("title") or ""))}" required></div>
<div class="field"><label>Schedule type</label><input type="text" name="schedule_type" value="{escape(str(item.get("schedule_type") or ""))}" required></div>
<div class="field"><label>Schedule value</label><input type="text" name="schedule_value" value="{escape(str(item.get("schedule_value") or ""))}" required></div>
<div class="field"><label>Timezone</label><input type="text" name="timezone" value="{escape(str(item.get("timezone") or ""))}" required></div>
</div>
<div class="field" style="margin-top:10px"><label>Description</label><textarea name="description" rows="4">{escape(str(item.get("description") or ""))}</textarea></div>
<div class="field" style="margin-top:10px"><label>Payload (JSON)</label><textarea name="payload" rows="8">{escape(payload)}</textarea></div>
<div class="row" style="margin-top:10px"><div class="field"><label>Approval mode</label>
<select name="approval_mode"><option value="approval_required"{" selected" if approval == "approval_required" else ""}>approval_required</option><option value="automatic"{" selected" if approval == "automatic" else ""}>automatic</option></select></div>
<div class="field grow"><label>Read-only identity</label><div class="mono small">{escape(str(item.get("owner_id") or ""))} / {escape(str(item.get("project_id") or ""))} · {escape(str(item.get("action_type") or ""))}</div></div>
<button class="primary" type="submit">Save task</button></div>
</form></section>'''


def task_fragment(base: Any, request: Request) -> str:
    project = selected_project(request)
    show_completed = request.query_params.get("show_completed") == "1"
    result = base._safe_call(base.scheduler().list_jobs, project_id=project, limit=1000)
    all_jobs = result if isinstance(result, list) else []
    visible = all_jobs if show_completed else [job for job in all_jobs if str(job.get("status") or "") != "completed"]
    completed = sum(1 for job in all_jobs if str(job.get("status") or "") == "completed")
    due_result = base._safe_call(base.scheduler().list_due_jobs, project_id=project, limit=1000)
    due = len(due_result) if isinstance(due_result, list) else 0
    projects = project_rows(base)
    filter_html = project_filter_html(request, tab="scheduler", selected=project, projects=projects)
    toggle = escape(
        dashboard_url(
            request, tab="scheduler", project=project,
            extra={"show_completed": None if show_completed else "1"},
        ),
        quote=True,
    )
    rows = []
    for job in visible:
        raw_id = str(job.get("id") or "")
        status = str(job.get("status") or "")
        extras = {"show_completed": "1" if show_completed else None}
        view = escape(dashboard_url(request, tab="scheduler", project=project, extra={**extras, "view_job": raw_id}), quote=True)
        actions = f'<a href="{view}"><button type="button">View</button></a>'
        if status != "completed":
            edit = escape(dashboard_url(request, tab="scheduler", project=project, extra={**extras, "edit_job": raw_id}), quote=True)
            actions += f'<a href="{edit}"><button type="button">Edit</button></a>'
        if status == "paused":
            actions += f'''<form method="post" action="/dashboard/job/resume"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}"><input type="hidden" name="job_id" value="{escape(raw_id)}"><button class="ok" type="submit">Resume</button></form>'''
        elif status != "completed":
            actions += f'''<form method="post" action="/dashboard/job/pause"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}"><input type="hidden" name="job_id" value="{escape(raw_id)}"><button type="submit">Pause</button></form>'''
        rows.append(
            f'<tr><td><strong>{escape(str(job.get("title") or ""))}</strong><div class="small muted mono">{escape(raw_id)}</div></td>'
            f'<td>{escape(str(job.get("owner_id") or ""))}<br><span class="muted">{escape(str(job.get("project_id") or ""))}</span></td>'
            f'<td>{escape(str(job.get("action_type") or ""))}</td><td><span class="badge">{escape(status)}</span></td>'
            f'<td class="mono">{escape(str(job.get("next_run_utc") or "—"))}</td><td class="actions">{actions}</td></tr>'
        )
    toggle_label = "Hide completed" if show_completed else f"Show completed ({completed})"
    count = f"{len(visible)} shown · {completed} completed" + ("" if show_completed else " hidden") + f" · {due} due · {len(all_jobs)} stored"
    return f'''<section class="tab-panel" id="panel-scheduler" data-panel="scheduler"><div class="grid">
{_detail(base, request, project, show_completed)}{_editor(base, request, project, show_completed)}
<section class="card wide"><div class="panel-title"><h2>Task registry</h2><div class="row"><span class="badge">{escape(count)}</span><a href="{toggle}"><button type="button">{escape(toggle_label)}</button></a></div></div>
{filter_html}
<p class="small muted"><strong>No cron worker runs here.</strong> Project filtering, completed visibility, View and Edit are WebGUI-only.</p>
<div class="scroll"><table><thead><tr><th>Task</th><th>Owner / project</th><th>Type</th><th>Status</th><th>Due / next UTC</th><th></th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6" class="muted">No tasks in this view</td></tr>'}</tbody></table></div>
</section></div></section>'''


async def dashboard_job_update(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    job_id = str(form.get("job_id") or "").strip()
    project = str(form.get("project_filter") or "").strip() or None
    show_completed = str(form.get("show_completed") or "") == "1"
    try:
        raw_payload = str(form.get("payload") or "").strip()
        payload = json.loads(raw_payload) if raw_payload else {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        result = base._safe_call(
            base.scheduler().update_job,
            job_id=job_id,
            title=str(form.get("title") or ""),
            description=str(form.get("description") or ""),
            payload=payload,
            schedule_type=str(form.get("schedule_type") or ""),
            schedule_value=str(form.get("schedule_value") or ""),
            timezone=str(form.get("timezone") or ""),
            approval_mode=str(form.get("approval_mode") or ""),
        )
        if isinstance(result, dict) and result.get("ok") is False:
            raise ValueError(str(result.get("error") or "Task update failed"))
        message = "Task updated"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    from starlette.responses import RedirectResponse
    target = dashboard_url(
        request, tab="scheduler", project=project,
        extra={"show_completed": "1" if show_completed else None, "flash": message},
    )
    return RedirectResponse(target, status_code=303)
