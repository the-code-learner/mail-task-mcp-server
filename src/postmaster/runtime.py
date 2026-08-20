from __future__ import annotations

import json
import os
from html import escape
from urllib.parse import urlencode

import uvicorn
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from . import runtime_core as _core

# v9.4.4 keeps the v9.4.2 task MCP/backend contract in runtime_core and layers
# completed-task visibility/detail only onto the browser dashboard.
for _name in dir(_core):
    if _name.startswith("_"):
        continue
    globals()[_name] = getattr(_core, _name)

_base = _core._base
_tracking_dashboard_fragment = _core._tracking_dashboard_fragment
_legacy_dashboard_home = _core.dashboard_home
mcp = _core.mcp
app = _core.app

_TASK_DETAIL_FIELDS = (
    "id",
    "owner_id",
    "project_id",
    "title",
    "description",
    "action_type",
    "execution_profile_id",
    "schedule_type",
    "schedule_value",
    "timezone",
    "approval_mode",
    "status",
    "next_run_utc",
    "created_at",
    "updated_at",
    "last_run_utc",
    "last_error",
    "payload",
)


def _task_dashboard_url(
    request: Request,
    *,
    show_completed: bool,
    view_job: str | None = None,
) -> str:
    params: dict[str, str] = {}
    account_id = (request.query_params.get("account") or "").strip()
    if account_id:
        params["account"] = account_id
    if show_completed:
        params["show_completed"] = "1"
    if view_job:
        params["view_job"] = view_job
    query = urlencode(params)
    return "/" + (("?" + query) if query else "") + "#scheduler"


def _task_detail_html(request: Request, *, show_completed: bool) -> str:
    job_id = (request.query_params.get("view_job") or "").strip()
    if not job_id:
        return ""

    detail = _base._safe_call(_base.scheduler().get_job, job_id)
    close_url = escape(
        _task_dashboard_url(request, show_completed=show_completed),
        quote=True,
    )
    if not isinstance(detail, dict) or detail.get("ok") is False:
        message = "Task could not be loaded"
        if isinstance(detail, dict):
            message = str(detail.get("error") or message)
        return f"""
<section class="card wide">
<div class="panel-title"><h2>Task detail</h2><a href="{close_url}"><button type="button">Close</button></a></div>
<div class="flash">{escape(message)}</div>
</section>
"""

    rows: list[str] = []
    for field in _TASK_DETAIL_FIELDS:
        value = detail.get(field)
        if field == "payload":
            try:
                rendered = json.dumps(
                    value if value is not None else {},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            except Exception:
                rendered = str(value)
            cell = (
                '<pre class="mono small" style="white-space:pre-wrap;overflow-wrap:anywhere;margin:0">'
                + escape(rendered)
                + "</pre>"
            )
        elif value is None or value == "":
            cell = '<span class="muted">—</span>'
        elif field == "description":
            cell = (
                '<div style="white-space:pre-wrap;overflow-wrap:anywhere">'
                + escape(str(value))
                + "</div>"
            )
        else:
            cell = '<span class="mono">' + escape(str(value)) + "</span>"
        rows.append(f"<tr><th>{escape(field)}</th><td>{cell}</td></tr>")

    return f"""
<section class="card wide">
<div class="panel-title"><h2>Task detail</h2><a href="{close_url}"><button type="button">Close</button></a></div>
<div class="scroll"><table><tbody>{''.join(rows)}</tbody></table></div>
</section>
"""


def _task_dashboard_fragment(request: Request) -> str:
    show_completed = request.query_params.get("show_completed") == "1"
    listed = _base._safe_call(_base.scheduler().list_jobs, limit=1000)
    all_jobs = listed if isinstance(listed, list) else []
    visible_jobs = (
        all_jobs
        if show_completed
        else [job for job in all_jobs if str(job.get("status") or "") != "completed"]
    )

    status_result = _base._safe_call(_base.scheduler().status)
    job_counts = (
        status_result.get("job_counts") or {}
        if isinstance(status_result, dict)
        else {}
    )
    completed_count = int(job_counts.get("completed") or 0)
    stored_count = sum(int(value or 0) for value in job_counts.values())
    if stored_count == 0 and all_jobs:
        stored_count = len(all_jobs)
        completed_count = sum(
            1 for job in all_jobs if str(job.get("status") or "") == "completed"
        )

    due_result = _base._safe_call(_base.scheduler().list_due_jobs, limit=1000)
    due_count = len(due_result) if isinstance(due_result, list) else 0

    toggle_url = escape(
        _task_dashboard_url(request, show_completed=not show_completed),
        quote=True,
    )
    toggle_label = "Hide completed" if show_completed else f"Show completed ({completed_count})"
    if show_completed:
        count_text = (
            f"{len(visible_jobs)} shown · {completed_count} completed · "
            f"{due_count} due · {stored_count} stored"
        )
    else:
        count_text = (
            f"{len(visible_jobs)} shown · {completed_count} completed hidden · "
            f"{due_count} due · {stored_count} stored"
        )

    job_rows: list[str] = []
    for job in visible_jobs:
        raw_id = str(job.get("id") or "")
        job_id = escape(raw_id)
        status = escape(str(job.get("status") or ""))
        title = escape(str(job.get("title") or ""))
        owner = escape(str(job.get("owner_id") or ""))
        project = escape(str(job.get("project_id") or ""))
        action = escape(str(job.get("action_type") or ""))
        next_run = escape(str(job.get("next_run_utc") or "—"))
        payload = job.get("payload") or {}
        account_ref = (
            escape(str(payload.get("account_id") or ""))
            if isinstance(payload, dict)
            else ""
        )
        view_url = escape(
            _task_dashboard_url(
                request,
                show_completed=show_completed,
                view_job=raw_id,
            ),
            quote=True,
        )
        buttons = f'<a href="{view_url}"><button type="button">View</button></a>'
        if status == "paused":
            buttons += f"""<form method="post" action="/dashboard/job/resume">
<input type="hidden" name="csrf" value="{escape(_base._csrf_value())}"><input type="hidden" name="job_id" value="{job_id}">
<button class="ok" type="submit">Resume</button></form>"""
        elif status != "completed":
            buttons += f"""<form method="post" action="/dashboard/job/pause">
<input type="hidden" name="csrf" value="{escape(_base._csrf_value())}"><input type="hidden" name="job_id" value="{job_id}">
<button type="submit">Pause</button></form>"""
        account_note = (
            f'<div class="small muted">account ref: <span class="mono">{account_ref}</span></div>'
            if account_ref
            else ""
        )
        job_rows.append(
            f"""<tr>
<td><strong>{title}</strong><div class="small muted mono">{job_id}</div></td>
<td>{owner}<br><span class="muted">{project}</span>{account_note}</td>
<td>{action}</td><td><span class="badge">{status}</span></td><td class="mono">{next_run}</td>
<td class="actions">{buttons}</td></tr>"""
        )

    detail_html = _task_detail_html(request, show_completed=show_completed)
    empty_text = (
        "No tasks registered"
        if show_completed
        else "No non-completed tasks registered"
    )
    return f"""
<section class="tab-panel" id="panel-scheduler" data-panel="scheduler">
<div class="grid">
{detail_html}
<section class="card wide">
<div class="panel-title"><h2>Task registry</h2><div class="row"><span class="badge">{escape(count_text)}</span><a href="{toggle_url}"><button type="button">{escape(toggle_label)}</button></a></div></div>
<p class="small muted"><strong>No cron worker runs here.</strong> Dates and recurrence are stored only so an AI or user can query what is due. Tasks never send email or execute actions by themselves. Completed tasks remain stored; the default hiding on this page is WebGUI-only.</p>
<div class="scroll"><table><thead><tr><th>Task</th><th>Owner / project</th><th>Type</th><th>Status</th><th>Due / next UTC</th><th></th></tr></thead>
<tbody>{''.join(job_rows) or f'<tr><td colspan="6" class="muted">{escape(empty_text)}</td></tr>'}</tbody></table></div>
</section>
</div>
</section>
"""


def _replace_task_dashboard(body: str, fragment: str) -> str:
    start_marker = '<section class="tab-panel" id="panel-scheduler" data-panel="scheduler">'
    end_marker = "\n<script>\n(() => {"
    start = body.find(start_marker)
    end = body.find(end_marker, start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        return body
    return body[:start] + fragment + body[end:]


async def dashboard_home(request: Request):
    response = await _legacy_dashboard_home(request)
    if "text/html" not in str(response.headers.get("content-type", "")).lower():
        return response
    try:
        body = response.body.decode("utf-8")
        body = _replace_task_dashboard(body, _task_dashboard_fragment(request))
        return HTMLResponse(body, status_code=response.status_code, headers={
            key: value
            for key, value in response.headers.items()
            if key.lower() != "content-length"
        })
    except Exception:
        _base.logger.info("Could not augment task dashboard", exc_info=True)
        return response


_routes = app.router.routes
for _index, _route in enumerate(list(_routes)):
    if isinstance(_route, Route) and _route.path == "/":
        _routes[_index] = Route("/", dashboard_home, methods=["GET"])
        break


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
        log_level="info",
    )
