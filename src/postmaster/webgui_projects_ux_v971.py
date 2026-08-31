from __future__ import annotations

import re
from html import escape
from typing import Any
from urllib.parse import urlencode


STYLE = r'''
/* post-v9.7.0 project resource UX */
.v971-active-project{display:flex;align-items:center;gap:8px;margin:8px 0;padding:8px 10px;border:1px solid var(--line);border-radius:9px;background:color-mix(in srgb,var(--accent) 8%,var(--card))}.v971-item-link{color:inherit;text-decoration:none}.v971-item-link:hover strong,.v971-item-link:hover .v971-file-title{text-decoration:underline}.v971-click-row{cursor:pointer}.v971-click-row:focus{outline:2px solid var(--accent);outline-offset:-2px}.v971-project-resource-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.v971-project-resource-grid>.card{margin:0}@media(max-width:760px){.v971-project-resource-grid{grid-template-columns:1fr}}
'''

ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
VIEW_RE = re.compile(
    r'<a(?P<attrs>[^>]*) href="(?P<href>[^"]*ui_view=knowledge[^"]*)"><button type="button">View</button></a>'
)
EDITOR_RE = re.compile(
    r'<section class="card wide"><h2>(?:Edit knowledge item|Add memory / skill)</h2>.*?</section>',
    re.S,
)
KNOWLEDGE_SUMMARY_RE = re.compile(
    r'<div><strong>\d+</strong><span>knowledge</span></div>'
)
_INSTALLED_FLAG = "_projects_ux_v971_installed"


def _make_knowledge_rows_clickable(html: str) -> str:
    def row(match: re.Match[str]) -> str:
        body = match.group(1)
        view = VIEW_RE.search(body)
        if not view:
            return match.group(0)
        href = view.group("href")
        body = body[: view.start()] + body[view.end() :]
        if "<strong>" in body:
            body = body.replace(
                "<strong>",
                f'<a class="v971-item-link" data-v960-fragment="knowledge" href="{href}"><strong>',
                1,
            ).replace("</strong>", "</strong></a>", 1)
        return f'<tr class="v971-click-row" data-v960-href="{href}">{body}</tr>'

    return ROW_RE.sub(row, html)


def _move_knowledge_editor_after_inventory(html: str) -> str:
    editor = EDITOR_RE.search(html)
    if not editor:
        return html
    section = editor.group(0)
    html = html[: editor.start()] + html[editor.end() :]
    close = html.rfind("</div></section>")
    if close < 0:
        return html + section
    return html[:close] + section + html[close:]


def _make_file_rows_clickable(html: str) -> str:
    def row(match: re.Match[str]) -> str:
        body = match.group(1)
        download = re.search(r'<a href="(?P<href>/dashboard/files/[^"]+/download)"><button type="button">Download</button></a>', body)
        if not download:
            return match.group(0)
        href = download.group("href")
        first_end = body.find("</td>")
        if first_end >= 0:
            first = body[:first_end]
            if first.startswith("<td>"):
                first = '<td><a class="v971-item-link" href="' + href + '">' + first[4:] + "</a>"
                body = first + body[first_end:]
        return f'<tr class="v971-click-row" tabindex="0" data-v971-file-href="{href}">{body}</tr>'

    return ROW_RE.sub(row, html)


def _active_project_banner(base: Any, request: Any) -> str:
    project_id = str(request.query_params.get("project") or "").strip()
    if not project_id:
        return ""
    try:
        projects = base.project_registry().list_projects(include_archived=False)
    except Exception:
        projects = []
    meta = next((row for row in projects if str(row.get("id") or "") == project_id), {})
    name = str(meta.get("name") or project_id)
    return (
        '<div class="v971-active-project"><span class="badge">Active project</span>'
        f'<strong>{escape(name)}</strong><code>{escape(project_id)}</code></div>'
    )


def _knowledge_resource_table(items: list[dict[str, Any]], *, project_id: str, kind: str) -> str:
    rows: list[str] = []
    for item in items:
        if str(item.get("kind") or "").casefold() != kind:
            continue
        item_id = str(item.get("id") or "")
        title = str(item.get("title") or item_id)
        params = {"ui_view": "knowledge", "projects": project_id, "view_knowledge": item_id}
        view = "/?" + urlencode(params) + "#knowledge"
        edit_params = {"ui_view": "knowledge", "projects": project_id, "edit_knowledge": item_id}
        edit = "/?" + urlencode(edit_params) + "#knowledge"
        rows.append(
            '<tr class="v971-click-row" data-v960-href="' + escape(view, quote=True) + '">'
            f'<td><a class="v971-item-link" data-v960-fragment="knowledge" href="{escape(view, quote=True)}"><strong>{escape(title)}</strong></a>'
            f'<div class="small muted mono">{escape(item_id)}</div></td>'
            f'<td>{float(item.get("priority") or 0.0):.2f}</td>'
            f'<td class="actions"><a data-v960-fragment="knowledge" href="{escape(edit, quote=True)}"><button type="button">Edit</button></a></td></tr>'
        )
    label = "Memories" if kind == "memory" else "Skills"
    return (
        f'<section class="card"><div class="panel-title"><h2>{label}</h2><span class="badge">{len(rows)}</span></div>'
        '<div class="scroll"><table><thead><tr><th>Item</th><th>Priority</th><th></th></tr></thead><tbody>'
        + ("".join(rows) or f'<tr><td colspan="3" class="muted">No project-scoped {label.lower()}</td></tr>')
        + "</tbody></table></div></section>"
    )


def _project_resource_split(base: Any, request: Any, html: str) -> str:
    project_id = str(request.query_params.get("project") or "").strip()
    if not project_id:
        return html
    result = base._safe_call(
        base.context_engine().store.list_items,
        project_id=project_id,
        include_global=False,
        limit=1000,
    )
    items = result if isinstance(result, list) else []
    memories = sum(1 for item in items if str(item.get("kind") or "").casefold() == "memory")
    skills = sum(1 for item in items if str(item.get("kind") or "").casefold() == "skill")
    html = KNOWLEDGE_SUMMARY_RE.sub(
        f'<div><strong>{memories}</strong><span>memories</span></div><div><strong>{skills}</strong><span>skills</span></div>',
        html,
        count=1,
    )
    cards = (
        '<section class="card wide"><div class="panel-title"><div><h2>Project resources</h2>'
        '<p class="small muted">Memories and Skills are shown separately; Tasks and Files remain in the same aggregated project detail.</p></div></div>'
        '<div class="v971-project-resource-grid">'
        + _knowledge_resource_table(items, project_id=project_id, kind="memory")
        + _knowledge_resource_table(items, project_id=project_id, kind="skill")
        + "</div></section>"
    )
    close = html.rfind("</div></section>")
    if close >= 0:
        html = html[:close] + cards + html[close:]
    return _make_knowledge_rows_clickable(html)


def install_projects_ux_v971(webgui_v960: Any, webgui_projects: Any, webgui_v962_views: Any, webgui_v962: Any) -> None:
    """Improve project-scoped resource navigation without changing CRUD or persistence."""
    if getattr(webgui_v960, _INSTALLED_FLAG, False):
        return
    original_knowledge = webgui_v960.knowledge_fragment
    original_files = webgui_projects.files_fragment
    original_projects = webgui_projects.project_overview_fragment

    def knowledge_v971(base: Any, request: Any) -> str:
        html = original_knowledge(base, request)
        html = _make_knowledge_rows_clickable(html)
        return _move_knowledge_editor_after_inventory(html)

    def files_v971(base: Any, request: Any) -> str:
        html = original_files(base, request)
        banner = _active_project_banner(base, request)
        if banner:
            marker = '<div class="panel-title"><h2>Stored files</h2>'
            html = html.replace(marker, banner + marker, 1)
        return _make_file_rows_clickable(html)

    def projects_v971(base: Any, request: Any) -> str:
        return _project_resource_split(base, request, original_projects(base, request))

    webgui_v960.knowledge_fragment = knowledge_v971
    webgui_projects.files_fragment = files_v971
    webgui_v962_views.files_fragment = files_v971
    webgui_projects.project_overview_fragment = projects_v971
    webgui_v962_views.project_overview_fragment = projects_v971

    if "post-v9.7.0 project resource UX" not in webgui_v962.BASE_STYLE:
        webgui_v962.BASE_STYLE += STYLE
    script = r'''
<script id="v971-file-row-navigation">
document.addEventListener('click',event=>{const row=event.target.closest('tr[data-v971-file-href]');if(!row||event.target.closest('a,button,input,form,select,textarea'))return;location.assign(row.dataset.v971FileHref);});
document.addEventListener('keydown',event=>{const row=event.target.closest('tr[data-v971-file-href]');if(!row||!(event.key==='Enter'||event.key===' '))return;event.preventDefault();location.assign(row.dataset.v971FileHref);});
</script>
'''
    if "v971-file-row-navigation" not in webgui_v962.SCRIPT:
        webgui_v962.SCRIPT += script
    setattr(webgui_v960, _INSTALLED_FLAG, True)


__all__ = [
    "_make_file_rows_clickable",
    "_make_knowledge_rows_clickable",
    "_move_knowledge_editor_after_inventory",
    "_project_resource_split",
    "install_projects_ux_v971",
]
