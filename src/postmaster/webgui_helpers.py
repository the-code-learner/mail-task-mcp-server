from __future__ import annotations

import os
from html import escape
from urllib.parse import urlencode
from typing import Any

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
    if ".markdown-viewer" in body or "</style>" not in body:
        return body
    style = '''
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
'''
    return body.replace("</style>", style + "\n</style>", 1)
