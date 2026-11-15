"""v9.8.4 true weekly task calendar, retaining the stable monthly grid."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from starlette.requests import Request

from .webgui_helpers import dashboard_url, project_color_class, project_legend_html


def _week_start(raw: str) -> date:
    try:
        dt = date.fromisoformat(str(raw))
    except (ValueError, TypeError):
        dt = datetime.now(ZoneInfo("Europe/Rome")).date()
    return dt - timedelta(days=dt.weekday())


def week_calendar_html(tasks: Any, request: Request,
                       jobs: list[dict[str, Any]], projects: list[dict[str, Any]],
                       project: str | None, show_completed: bool) -> str:
    zone = ZoneInfo(tasks.TASK_CALENDAR_TIMEZONE)
    start = _week_start(request.query_params.get("calendar_week") or "")
    finish = start + timedelta(days=6)
    today = datetime.now(zone).date()
    proj = {
        str(x.get("id") or ""): str(x.get("name") or x.get("id") or "")
        for x in projects
    }
    events: dict[date, list[tuple[datetime, dict[str, Any]]]] = {}
    outside: list[dict[str, Any]] = []
    for job in jobs:
        utc = tasks._calendar_event_time(job)
        if utc is None:
            outside.append(job)
            continue
        local = utc.astimezone(zone)
        if start <= local.date() <= finish:
            events.setdefault(local.date(), []).append((local, job))
        else:
            outside.append(job)
    extras = {
        "show_completed": None, "hide_completed": None if show_completed else "1",
        "task_view": "calendar", "calendar_mode": "week",
    }
    def link(value: date | None) -> str:
        return escape(dashboard_url(
            request, tab="scheduler", project=project,
            extra={**extras, "calendar_week": (value or today).isoformat()},
        ),quote=True)
    def month_link() -> str:
        return escape(dashboard_url(
            request, tab="scheduler", project=project,
            extra={
                "show_completed": None, "hide_completed": None if show_completed else "1",
                "task_view": "calendar", "calendar_month": f"{start.year:04d}-{start.month:02d}",
                "calendar_mode": None, "calendar_week": None,
            },
        ),quote=True)
    cells = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        item_html = []
        for when, item in sorted(events.get(day,[]),key=lambda x:x[0]):
            jid = str(item.get("id") or "")
            pid = str(item.get("project_id") or "") or None
            target = escape(dashboard_url(
                request, tab="scheduler", project=project,
                extra={**extras,"calendar_week":start.isoformat(),"view_job":jid},
            ),quote=True)
            title = escape(str(item.get("title") or "Untitled task"))
            project_name = escape(proj.get(pid or "",pid or "global"))
            finished = item.get("status") == "completed"
            label = "Completed" if finished else "Scheduled"
            completed_at = tasks._parse_utc(item.get("completed_at_utc"))
            completion_note = (
                f" · done {completed_at.astimezone(zone):%H:%M}"
                if finished and completed_at else ""
            )
            item_html.append(
                f'<a class="task-calendar-event {project_color_class(pid)}{tasks._calendar_event_class(item)}" '
                f'data-task-status="{escape(str(item.get("status") or ""), quote=True)}" '
                f'aria-label="{escape(label + ": " + str(item.get("title") or "Untitled task"), quote=True)}" '
                f'href="{target}">{"✓ " if finished else ""}{title}'
                f'<small>{when:%H:%M} · {project_name} · {label}{completion_note}</small></a>'
            )
        classes = "task-calendar-day" + (" today" if day == today else "")
        cells.append(
            f'<div class="{classes}" data-date="{day.isoformat()}">'
            f'<div class="task-calendar-date">{escape(day.strftime("%a %d %b"))}</div>'
            f'{"".join(item_html)}</div>'
        )
    return (
        '<div class="task-calendar-toolbar"><div>'
        f'<div class="task-calendar-month">{start:%d %b} – {finish:%d %b %Y}</div>'
        f'<p class="small muted">Scheduled dates and completed executions · {escape(tasks.TASK_CALENDAR_TIMEZONE)}</p>'
        '</div><div class="task-calendar-controls">'
        f'<a href="{month_link()}"><button type="button">Month</button></a>'
        '<button type="button" aria-current="true">Week</button>'
        f'<a href="{link(start-timedelta(days=7))}"><button type="button">‹</button></a>'
        f'<a href="{link(None)}"><button type="button">Today</button></a>'
        f'<a href="{link(start+timedelta(days=7))}"><button type="button">›</button></a>'
        '</div></div><div class="task-calendar-shell v984-week-calendar">'
        '<div class="task-calendar-head">'
        + "".join(f'<div>{day}</div>' for day in ("Mon","Tue","Wed","Thu","Fri","Sat","Sun"))
        + '</div><div class="task-calendar-grid">'
        + "".join(cells)
        + '</div></div>'
        + project_legend_html(projects,[job.get("project_id") for job in jobs])
        + f'<p class="small muted">{len(outside)} other tasks are outside this week or have no next-run date. '
        'No future task runs are invented.</p>'
    )


def install_week_calendar_v984(tasks: Any) -> None:
    current = tasks._calendar_html
    if getattr(current, "_v984_installed", False):
        return

    def calendar_html(request: Request, jobs: list[dict[str, Any]],
                      projects: list[dict[str, Any]], project: str | None,
                      show_completed: bool) -> str:
        if str(request.query_params.get("calendar_mode") or "") == "week":
            return week_calendar_html(tasks, request, jobs, projects, project, show_completed)
        html = current(request, jobs, projects, project, show_completed)
        year, month = tasks._calendar_month(request)
        extra = {
            "task_view": "calendar", "calendar_mode": "week",
            "calendar_week": date(year,month,1).isoformat(),
            "show_completed": None,
            "hide_completed": None if show_completed else "1",
            "calendar_month": f"{year:04d}-{month:02d}",
        }
        week_link = escape(dashboard_url(
            request, tab="scheduler", project=project, extra=extra,
        ),quote=True)
        bar = '<div class="task-calendar-toolbar">'
        switch = (
            '<div class="task-calendar-controls v984-calendar-switch">'
            '<button type="button" aria-current="true">Month</button>'
            f'<a href="{week_link}"><button type="button">Week</button></a>'
            '</div>'
        )
        return html.replace(bar,switch+bar,1)

    calendar_html._v984_installed = True
    tasks._calendar_html = calendar_html
