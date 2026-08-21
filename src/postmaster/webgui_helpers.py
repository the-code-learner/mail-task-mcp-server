from __future__ import annotations

import hashlib
import os
from html import escape
from typing import Any, Iterable
from urllib.parse import urlencode

import bleach
import mistune
from starlette.requests import Request


MARKDOWN_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "em", "strong", "del", "blockquote",
    "code", "pre", "a", "table", "thead", "tbody", "tr", "th", "td",
}
MARKDOWN_ATTRS = {
    "a": ["href", "title"],
    "code": ["class"],
    "th": ["align"],
    "td": ["align"],
}
PROJECT_COLOR_COUNT = 8


def runtime_version(base: Any) -> str:
    try:
        status = base.build_status()
        version = str(status.get("version") or "").strip()
        if version:
            return version.removeprefix("v")
    except Exception:
        pass
    return str(os.getenv("BRIDGE_BUILD") or os.getenv("POSTMASTER_REF") or "unknown").removeprefix("v")


def selected_project(request: Request) -> str | None:
    value = (request.query_params.get("project") or "").strip()
    return value or None


def project_rows(base: Any) -> list[dict[str, Any]]:
    result = base._safe_call(base.scheduler().list_projects)
    return result if isinstance(result, list) else []


def project_color_class(project_id: str | None) -> str:
    """Return a deterministic presentation-only project color class."""
    value = str(project_id or "").strip()
    if not value:
        return "project-color-global"
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return f"project-color-{digest[0] % PROJECT_COLOR_COUNT}"


def project_label_html(label: str, project_id: str | None, *, compact: bool = False) -> str:
    role = "project-scope" if compact else "project-name"
    return (
        f'<span class="{role} {project_color_class(project_id)}">'
        f'{escape(str(label or ""))}</span>'
    )


def project_legend_html(
    projects: list[dict[str, Any]], project_ids: Iterable[str | None],
) -> str:
    names = {
        str(row.get("id") or ""): str(row.get("name") or row.get("id") or "")
        for row in projects
    }
    seen: set[str] = set()
    labels: list[str] = []
    for raw in project_ids:
        project_id = str(raw or "").strip()
        key = project_id or "__global__"
        if key in seen:
            continue
        seen.add(key)
        label = names.get(project_id, project_id) if project_id else "global"
        labels.append(project_label_html(label, project_id or None, compact=True))
    if not labels:
        return ""
    return f'<div class="project-key">{"".join(labels)}</div>'


def dashboard_url(
    request: Request,
    *,
    tab: str,
    project: str | None = None,
    extra: dict[str, str | None] | None = None,
) -> str:
    params: dict[str, str] = {}
    account = (request.query_params.get("account") or "").strip()
    if account:
        params["account"] = account
    if project:
        params["project"] = project
    for key, value in (extra or {}).items():
        if value not in {None, ""}:
            params[key] = str(value)
    query = urlencode(params)
    return "/" + (("?" + query) if query else "") + f"#{tab}"


def project_filter_html(
    request: Request,
    *,
    tab: str,
    selected: str | None,
    projects: list[dict[str, Any]],
) -> str:
    options = ['<option value="">All projects</option>']
    for project in projects:
        pid = str(project.get("id") or "")
        name = str(project.get("name") or pid)
        owner = str(project.get("owner_id") or "")
        sel = " selected" if selected == pid else ""
        options.append(
            f'<option value="{escape(pid)}"{sel}>{escape(owner)} / {escape(name)} ({escape(pid)})</option>'
        )
    account = (request.query_params.get("account") or "").strip()
    account_hidden = f'<input type="hidden" name="account" value="{escape(account)}">' if account else ""
    return f'''<form class="project-filter" method="get" action="/">
{account_hidden}
<div class="row"><div class="field grow"><label>Project filter</label><select name="project">{''.join(options)}</select></div>
<button type="submit">Apply</button><a href="/#{escape(tab)}"><button type="button">All projects</button></a></div>
</form>'''


def owner_options(owners: list[dict[str, Any]], selected: str) -> str:
    return "".join(
        f'<option value="{escape(str(owner.get("id") or ""))}"'
        f'{" selected" if str(owner.get("id") or "") == selected else ""}>'
        f'{escape(str(owner.get("display_name") or owner.get("id") or ""))} — '
        f'{escape(str(owner.get("id") or ""))}</option>'
        for owner in owners
    )


def project_options(projects: list[dict[str, Any]], selected: str | None) -> str:
    out = ['<option value="">Global / owner-wide</option>']
    for project in projects:
        pid = str(project.get("id") or "")
        owner = str(project.get("owner_id") or "")
        name = str(project.get("name") or pid)
        sel = " selected" if selected == pid else ""
        out.append(
            f'<option value="{escape(pid)}"{sel}>{escape(owner)} / {escape(name)} ({escape(pid)})</option>'
        )
    return "".join(out)


def render_markdown_safe(source: str) -> str:
    renderer = mistune.create_markdown(plugins=["table", "strikethrough"])
    rendered = renderer(str(source or ""))
    return bleach.clean(
        rendered,
        tags=MARKDOWN_TAGS,
        attributes=MARKDOWN_ATTRS,
        protocols={"http", "https", "mailto"},
        strip=True,
    )


def replace_panel(body: str, panel: str, fragment: str) -> str:
    marker = f'<section class="tab-panel" id="panel-{panel}" data-panel="{panel}">'
    start = body.find(marker)
    if start < 0:
        return body
    next_panel = body.find('\n<section class="tab-panel"', start + len(marker))
    script = body.find("\n<script>", start + len(marker))
    ends = [index for index in (next_panel, script) if index >= 0]
    if not ends:
        return body
    return body[:start] + fragment + body[min(ends):]


def decorate_navigation(body: str, project_count: int, project_fragment: str) -> str:
    knowledge_tab = '<a class="tab-link" href="#knowledge" data-tab="knowledge">'
    if knowledge_tab in body and 'data-tab="projects"' not in body:
        body = body.replace(
            knowledge_tab,
            f'<a class="tab-link" href="#projects" data-tab="projects">Projects '
            f'<span class="tab-count">{project_count}</span></a>\n  {knowledge_tab}',
            1,
        )
    old_allowed = "new Set(['overview','accounts','amp','tracking','domains','recipients','knowledge','files','scheduler'])"
    new_allowed = "new Set(['overview','accounts','amp','tracking','domains','recipients','projects','knowledge','files','scheduler'])"
    body = body.replace(old_allowed, new_allowed, 1)
    marker = '<section class="tab-panel" id="panel-knowledge" data-panel="knowledge">'
    if marker in body and 'id="panel-projects"' not in body:
        body = body.replace(marker, project_fragment + "\n" + marker, 1)
    return body


def decorate_version(body: str, version: str) -> str:
    safe = escape(version.removeprefix("v"))
    body = body.replace("<title>Postmaster MCP v9.1</title>", f"<title>Postmaster v{safe}</title>", 1)
    body = body.replace("<h1>Postmaster MCP</h1>", f"<h1>Postmaster v{safe}</h1>", 1)
    body = body.replace("task registry + small files · v9.1</p>", f"task registry + small files · v{safe}</p>", 1)
    return body


def decorate_styles(body: str) -> str:
    if "/* webgui-v951-foundation */" in body or "</style>" not in body:
        return body
    style = '''
/* webgui-v951-foundation */
.project-filter { margin:0 0 14px; }
.markdown-viewer { margin-top:16px; line-height:1.6; overflow-wrap:anywhere; }
.markdown-viewer h1 { font-size:24px; margin:20px 0 10px; }
.markdown-viewer h2 { font-size:20px; margin:18px 0 9px; }
.markdown-viewer h3 { font-size:17px; margin:16px 0 8px; }
.markdown-viewer pre { overflow:auto; padding:12px; border:1px solid var(--line); border-radius:9px; background:#101419; }
.markdown-viewer code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.markdown-viewer table { margin:12px 0; }
.markdown-viewer blockquote { margin:12px 0; padding-left:12px; border-left:3px solid var(--line); color:var(--muted); }
.project-summary { display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:10px; margin-top:14px; }
.project-summary > div { border:1px solid var(--line); border-radius:10px; padding:10px; }
.project-summary strong { display:block; font-size:20px; }
.project-summary span { color:var(--muted); font-size:12px; }
.project-key { display:flex; gap:7px; align-items:center; flex-wrap:wrap; margin:8px 0; }
.project-name { display:inline-block; padding:5px 8px; border-radius:8px; font-weight:750; line-height:1.25; border:1px solid transparent; white-space:normal; }
.project-scope { display:inline-flex; align-items:center; padding:3px 7px; border-radius:999px; font-size:11px; font-weight:700; border:1px solid transparent; }
.project-color-0 { color:#93c5fd; background:rgba(37,99,235,.16); border-color:rgba(96,165,250,.36); }
.project-color-1 { color:#d8b4fe; background:rgba(126,34,206,.16); border-color:rgba(192,132,252,.36); }
.project-color-2 { color:#99f6e4; background:rgba(13,148,136,.16); border-color:rgba(94,234,212,.36); }
.project-color-3 { color:#fde68a; background:rgba(202,138,4,.16); border-color:rgba(250,204,21,.36); }
.project-color-4 { color:#fda4af; background:rgba(225,29,72,.16); border-color:rgba(251,113,133,.36); }
.project-color-5 { color:#a5f3fc; background:rgba(8,145,178,.16); border-color:rgba(103,232,249,.36); }
.project-color-6 { color:#bef264; background:rgba(101,163,13,.16); border-color:rgba(163,230,53,.36); }
.project-color-7 { color:#fdba74; background:rgba(234,88,12,.16); border-color:rgba(251,146,60,.36); }
.project-color-global { color:var(--muted); background:rgba(127,127,127,.11); border-color:var(--line); }
.task-view-toggle { display:inline-flex; border:1px solid var(--line); border-radius:9px; overflow:hidden; }
.task-view-toggle a { display:inline-block; padding:7px 10px; color:var(--muted); text-decoration:none; border-right:1px solid var(--line); }
.task-view-toggle a:last-child { border-right:0; }
.task-view-toggle a.active { color:var(--accent); background:rgba(104,160,255,.12); font-weight:750; }
.task-calendar-toolbar { display:flex; justify-content:space-between; gap:10px; align-items:center; flex-wrap:wrap; margin:0 0 10px; }
.task-calendar-month { font-size:17px; font-weight:800; }
.task-calendar-controls { display:flex; gap:6px; }
.task-calendar-shell { overflow:auto; }
.task-calendar-head, .task-calendar-grid { display:grid; grid-template-columns:repeat(7,minmax(120px,1fr)); min-width:840px; }
.task-calendar-head { border:1px solid var(--line); border-bottom:0; border-radius:10px 10px 0 0; overflow:hidden; }
.task-calendar-head > div { padding:8px; color:var(--muted); font-size:11px; font-weight:800; text-transform:uppercase; border-right:1px solid var(--line); }
.task-calendar-head > div:last-child { border-right:0; }
.task-calendar-grid { border:1px solid var(--line); border-radius:0 0 10px 10px; overflow:hidden; }
.task-calendar-day { min-height:116px; padding:7px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); }
.task-calendar-day:nth-child(7n) { border-right:0; }
.task-calendar-day.outside { opacity:.62; background:rgba(127,127,127,.04); }
.task-calendar-day.today { box-shadow:inset 0 0 0 2px var(--accent); }
.task-calendar-date { font-size:11px; font-weight:800; margin-bottom:6px; }
.task-calendar-event { display:block; width:100%; padding:5px 6px; margin:4px 0; border:1px solid transparent; border-radius:7px; text-decoration:none; font-size:11px; font-weight:750; line-height:1.25; }
.task-calendar-event small { display:block; opacity:.82; font-weight:600; margin-top:2px; }
.task-calendar-outside-list { margin-top:12px; padding-top:12px; border-top:1px solid var(--line); }
.task-calendar-outside-list .row { align-items:center; }
@media (prefers-color-scheme: light) {
  .project-color-0 { color:#1d4ed8; }
  .project-color-1 { color:#7e22ce; }
  .project-color-2 { color:#0f766e; }
  .project-color-3 { color:#a16207; }
  .project-color-4 { color:#be123c; }
  .project-color-5 { color:#0e7490; }
  .project-color-6 { color:#4d7c0f; }
  .project-color-7 { color:#c2410c; }
}
'''
    return body.replace("</style>", style + "\n</style>", 1)
