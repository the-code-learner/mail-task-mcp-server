from __future__ import annotations

import calendar as calendar_module
import json
from datetime import datetime, timezone
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from starlette.requests import Request

from .webgui_helpers import (
    dashboard_url,
    project_color_class,
    project_filter_html,
    project_label_html,
    project_legend_html,
    project_rows,
    selected_project,
)


TASK_DETAIL_FIELDS = (
    "id", "owner_id", "project_id", "title", "description", "action_type",
    "execution_profile_id", "schedule_type", "schedule_value", "timezone",
    "approval_mode", "status", "next_run_utc", "created_at", "updated_at",
    "last_run_utc", "last_error", "payload",
)
TASK_CALENDAR_TIMEZONE = "Europe/Rome"


def _task_view(request: Request) -> str:
    return "calendar" if request.query_params.get("task_view") == "calendar" else "agenda"


def _calendar_month(request: Request) -> tuple[int, int]:
    raw = (request.query_params.get("calendar_month") or "").strip()
    if raw:
        try:
            parsed = datetime.strptime(raw, "%Y-%m")
            return parsed.year, parsed.month
        except ValueError:
            pass
    now = datetime.now(ZoneInfo(TASK_CALENDAR_TIMEZONE))
    return now.year, now.month


def _month_value(year: int, month: int, offset: int) -> str:
    zero_based = year * 12 + (month - 1) + offset
    target_year, target_month_zero = divmod(zero_based, 12)
    return f"{target_year:04d}-{target_month_zero + 1:02d}"


def _parse_utc(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _view_extras(request: Request, show_completed: bool) -> dict[str, str | None]:
    view = _task_view(request)
    return {
        "show_completed": "1" if show_completed else None,
        "task_view": "calendar" if view == "calendar" else None,
        "calendar_month": (
            request.query_params.get("calendar_month") if view == "calendar" else None
        ),
    }


def _detail(base: Any, request: Request, project: str | None, show_completed: bool) -> str:
    job_id = (request.query_params.get("view_job") or "").strip()
    if not job_id:
        return ""
    item = base._safe_call(base.scheduler().get_job, job_id)
    close = escape(
        dashboard_url(
            request, tab="scheduler", project=project,
            extra=_view_extras(request, show_completed),
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
            extra=_view_extras(request, show_completed),
        ),
        quote=True,
    )
    if not isinstance(item, dict) or item.get("ok") is False:
        return ""
    if str(item.get("status") or "") == "completed":
        return f'<section class="card wide"><div class="panel-title"><h2>Edit task</h2><a href="{close}"><button type="button">Close</button></a></div><div class="flash">Completed tasks are immutable.</div></section>'
    payload = json.dumps(item.get("payload") or {}, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    approval = str(item.get("approval_mode") or "approval_required")
    approval_options = "".join(
        f'<option value="{value}"{" selected" if approval == value else ""}>{value}</option>'
        for value in ("approval_required", "automatic", "manual_only")
    )
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
<div class="row" style="margin-top:10px"><div class="field"><label>Approval mode</label><select name="approval_mode">{approval_options}</select></div>
<div class="field grow"><label>Read-only identity</label><div class="mono small">{escape(str(item.get("owner_id") or ""))} / {escape(str(item.get("project_id") or ""))} · {escape(str(item.get("action_type") or ""))}</div></div>
<button class="primary" type="submit">Save task</button></div>
</form></section>'''


def _calendar_html(
    request: Request,
    jobs: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    project: str | None,
    show_completed: bool,
) -> str:
    year, month = _calendar_month(request)
    zone = ZoneInfo(TASK_CALENDAR_TIMEZONE)
    month_value = f"{year:04d}-{month:02d}"
    today = datetime.now(zone).date()
    project_names = {
        str(row.get("id") or ""): str(row.get("name") or row.get("id") or "")
        for row in projects
    }
    by_day: dict[Any, list[tuple[datetime, dict[str, Any]]]] = {}
    outside: list[tuple[datetime | None, dict[str, Any]]] = []
    for job in jobs:
        due_utc = _parse_utc(job.get("next_run_utc"))
        if due_utc is None:
            outside.append((None, job))
            continue
        due_local = due_utc.astimezone(zone)
        if due_local.year == year and due_local.month == month:
            by_day.setdefault(due_local.date(), []).append((due_local, job))
        else:
            outside.append((due_local, job))
    for rows in by_day.values():
        rows.sort(key=lambda pair: pair[0])
    outside.sort(key=lambda pair: (pair[0] is None, pair[0] or datetime.max.replace(tzinfo=zone)))

    extras = {"show_completed": "1" if show_completed else None, "task_view": "calendar"}
    prev_url = escape(
        dashboard_url(request, tab="scheduler", project=project, extra={**extras, "calendar_month": _month_value(year, month, -1)}),
        quote=True,
    )
    now = datetime.now(zone)
    today_url = escape(
        dashboard_url(request, tab="scheduler", project=project, extra={**extras, "calendar_month": f"{now.year:04d}-{now.month:02d}"}),
        quote=True,
    )
    next_url = escape(
        dashboard_url(request, tab="scheduler", project=project, extra={**extras, "calendar_month": _month_value(year, month, 1)}),
        quote=True,
    )

    cells: list[str] = []
    weeks = calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month)
    base_extras = {
        "show_completed": "1" if show_completed else None,
        "task_view": "calendar",
        "calendar_month": month_value,
    }
    for week in weeks:
        for day in week:
            classes = ["task-calendar-day"]
            if day.month != month:
                classes.append("outside")
            if day == today:
                classes.append("today")
            events: list[str] = []
            for due_local, job in by_day.get(day, []):
                raw_id = str(job.get("id") or "")
                pid = str(job.get("project_id") or "") or None
                view = escape(
                    dashboard_url(request, tab="scheduler", project=project, extra={**base_extras, "view_job": raw_id}),
                    quote=True,
                )
                title = escape(str(job.get("title") or "Untitled task"))
                project_name = escape(project_names.get(pid or "", pid or "global"))
                events.append(
                    f'<a class="task-calendar-event {project_color_class(pid)}" href="{view}">'
                    f'{title}<small>{due_local:%H:%M} · {project_name}</small></a>'
                )
            date_label = f"{day.day}"
            if day.month != month:
                date_label = f"{day.day} {calendar_module.month_abbr[day.month]}"
            cells.append(
                f'<div class="{" ".join(classes)}"><div class="task-calendar-date">{escape(date_label)}</div>{"".join(events)}</div>'
            )

    outside_rows: list[str] = []
    for due_local, job in outside:
        raw_id = str(job.get("id") or "")
        pid = str(job.get("project_id") or "") or None
        view = escape(
            dashboard_url(request, tab="scheduler", project=project, extra={**base_extras, "view_job": raw_id}),
            quote=True,
        )
        title = project_label_html(str(job.get("title") or "Untitled task"), pid)
        due_label = due_local.strftime("%Y-%m-%d %H:%M") if due_local else "no next run"
        outside_rows.append(
            f'<div class="row"><a href="{view}" style="text-decoration:none">{title}</a>'
            f'<span class="small muted mono">{escape(due_label)}</span></div>'
        )
    outside_html = ""
    if outside_rows:
        outside_html = (
            '<div class="task-calendar-outside-list"><div class="panel-title">'
            '<h3>Tasks outside this month</h3><span class="small muted">Kept visible so Calendar and Agenda represent the same registry rows.</span>'
            f'</div>{"".join(outside_rows)}</div>'
        )

    legend = project_legend_html(projects, [job.get("project_id") for job in jobs])
    return f'''<div class="task-calendar-toolbar"><div><div class="task-calendar-month">{escape(calendar_module.month_name[month])} {year}</div><div class="small muted">Current registry next-run timestamps · {TASK_CALENDAR_TIMEZONE}</div></div>
<div class="task-calendar-controls"><a href="{prev_url}"><button type="button">‹</button></a><a href="{today_url}"><button type="button">Today</button></a><a href="{next_url}"><button type="button">›</button></a></div></div>
<div class="task-calendar-shell"><div class="task-calendar-head">{''.join(f'<div>{name}</div>' for name in ('Mon','Tue','Wed','Thu','Fri','Sat','Sun'))}</div><div class="task-calendar-grid">{''.join(cells)}</div></div>
{legend}
<p class="small muted"><strong>Registry only:</strong> Calendar places each task at its stored <code>next_run_utc</code>. It does not synthesize future executions or imply an autonomous worker.</p>
{outside_html}'''


def task_fragment(base: Any, request: Request) -> str:
    project = selected_project(request)
    show_completed = request.query_params.get("show_completed") == "1"
    view_mode = _task_view(request)
    result = base._safe_call(base.scheduler().list_jobs, project_id=project, limit=1000)
    all_jobs = result if isinstance(result, list) else []
    visible = all_jobs if show_completed else [job for job in all_jobs if str(job.get("status") or "") != "completed"]
    completed = sum(1 for job in all_jobs if str(job.get("status") or "") == "completed")
    due_result = base._safe_call(base.scheduler().list_due_jobs, project_id=project, limit=1000)
    due = len(due_result) if isinstance(due_result, list) else 0
    projects = project_rows(base)
    project_names = {
        str(row.get("id") or ""): str(row.get("name") or row.get("id") or "")
        for row in projects
    }
    filter_html = project_filter_html(request, tab="scheduler", selected=project, projects=projects)
    base_extras = _view_extras(request, show_completed)
    toggle = escape(
        dashboard_url(
            request, tab="scheduler", project=project,
            extra={**base_extras, "show_completed": None if show_completed else "1"},
        ),
        quote=True,
    )
    rows = []
    for job in visible:
        raw_id = str(job.get("id") or "")
        status = str(job.get("status") or "")
        pid = str(job.get("project_id") or "") or None
        view = escape(dashboard_url(request, tab="scheduler", project=project, extra={**base_extras, "view_job": raw_id}), quote=True)
        actions = f'<a href="{view}"><button type="button">View</button></a>'
        if status != "completed":
            edit = escape(dashboard_url(request, tab="scheduler", project=project, extra={**base_extras, "edit_job": raw_id}), quote=True)
            actions += f'<a href="{edit}"><button type="button">Edit</button></a>'
        if status == "paused":
            actions += f'''<form method="post" action="/dashboard/job/resume"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}"><input type="hidden" name="job_id" value="{escape(raw_id)}"><button class="ok" type="submit">Resume</button></form>'''
        elif status != "completed":
            actions += f'''<form method="post" action="/dashboard/job/pause"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}"><input type="hidden" name="job_id" value="{escape(raw_id)}"><button type="submit">Pause</button></form>'''
        title = project_label_html(str(job.get("title") or ""), pid)
        scope = project_label_html(project_names.get(pid or "", pid or "global"), pid, compact=True)
        rows.append(
            f'<tr><td>{title}<div class="small muted mono">{escape(raw_id)}</div></td>'
            f'<td>{escape(str(job.get("owner_id") or ""))}<br>{scope}</td>'
            f'<td>{escape(str(job.get("action_type") or ""))}</td><td><span class="badge">{escape(status)}</span></td>'
            f'<td class="mono">{escape(str(job.get("next_run_utc") or "—"))}</td><td class="actions">{actions}</td></tr>'
        )
    toggle_label = "Hide completed" if show_completed else f"Show completed ({completed})"
    count = f"{len(visible)} shown · {completed} completed" + ("" if show_completed else " hidden") + f" · {due} due · {len(all_jobs)} stored"
    agenda_url = escape(
        dashboard_url(
            request, tab="scheduler", project=project,
            extra={"show_completed": "1" if show_completed else None},
        ),
        quote=True,
    )
    year, month = _calendar_month(request)
    calendar_url = escape(
        dashboard_url(
            request, tab="scheduler", project=project,
            extra={
                "show_completed": "1" if show_completed else None,
                "task_view": "calendar",
                "calendar_month": f"{year:04d}-{month:02d}",
            },
        ),
        quote=True,
    )
    legend = project_legend_html(projects, [job.get("project_id") for job in visible])
    agenda = f'''{legend}<div class="scroll"><table><thead><tr><th>Task</th><th>Owner / project</th><th>Type</th><th>Status</th><th>Due / next UTC</th><th></th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6" class="muted">No tasks in this view</td></tr>'}</tbody></table></div>'''
    content = _calendar_html(request, visible, projects, project, show_completed) if view_mode == "calendar" else agenda
    return f'''<section class="tab-panel" id="panel-scheduler" data-panel="scheduler"><div class="grid">
{_detail(base, request, project, show_completed)}{_editor(base, request, project, show_completed)}
<section class="card wide"><div class="panel-title"><h2>Task registry</h2><div class="row"><div class="task-view-toggle"><a class="{"active" if view_mode == "agenda" else ""}" href="{agenda_url}">Agenda</a><a class="{"active" if view_mode == "calendar" else ""}" href="{calendar_url}">Calendar</a></div><span class="badge">{escape(count)}</span><a href="{toggle}"><button type="button">{escape(toggle_label)}</button></a></div></div>
{filter_html}
<p class="small muted"><strong>No cron worker runs here.</strong> Agenda and Calendar are two views of the same real task-registry rows; project colors are presentation metadata only.</p>
{content}
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
