"""v9.8.4 authenticated Tracking link management (URL and Stored File).

Reuses the existing MCP operations and their owner/project validation. The WebGUI
does not mint a second token type or bypass tracked Stored File incarnation checks.
"""
from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from .webgui_helpers import owner_options, project_options
from .webgui_v962_perf import active_projects, owners


def _e(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _redirect(owner: str = "", project: str = "", flash: str = "") -> str:
    params = {"ui_view": "tracking"}
    if owner:
        params["owner_id"] = owner
    if project:
        params["project_id"] = project
    if flash:
        params["flash"] = flash[:170]
    return "/?" + urlencode(params) + "#tracking"


def _scope(form: Any, base: Any) -> tuple[str, str | None]:
    owner = str(form.get("owner_id") or "").strip()
    project = str(form.get("project_id") or "").strip() or None
    valid_owners = {str(x.get("id") or "") for x in owners(base)}
    if not owner or owner not in valid_owners:
        raise ValueError("Choose a valid owner")
    if project:
        if not any(
            str(x.get("id") or "") == project
            and str(x.get("owner_id") or "") == owner
            for x in active_projects(base)
        ):
            raise ValueError("Project does not belong to the selected owner")
    return owner, project


def _form_field(label: str, name: str, *, required: bool = True,
                placeholder: str = "") -> str:
    return (
        f'<label>{_e(label)} '
        f'<input name="{_e(name)}" placeholder="{_e(placeholder)}"'
        f'{" required" if required else ""}></label>'
    )


def render_tracked_links_v984(base: Any, core: Any, request: Request) -> str:
    selected_owner = str(request.query_params.get("owner_id") or "").strip()
    selected_project = str(request.query_params.get("project_id") or "").strip()
    all_owners = owners(base)
    projects = active_projects(base)
    owner_ids = [str(x.get("id") or "") for x in all_owners if x.get("id")]
    results = []
    errors = []
    for oid in (owner_ids if not selected_owner else [selected_owner]):
        if oid not in owner_ids:
            continue
        try:
            result = core.list_tracked_links(
                owner_id=oid,
                project_id=selected_project or None,
                active_only=False, limit=200,
            )
            if isinstance(result, dict) and result.get("ok"):
                results += [x for x in result.get("links", []) if isinstance(x, dict)]
            else:
                errors.append(oid)
        except Exception:
            errors.append(oid)
    results.sort(key=lambda x: (str(x.get("created_at") or ""), str(x.get("id") or "")), reverse=True)
    results = results[:300]
    csrf = str(base._csrf_value())
    owner_choices = owner_options(all_owners, selected_owner or (owner_ids[0] if owner_ids else ""))
    project_choices = project_options(projects, selected_project or None)
    scope = (
        f'<label>Owner <select name="owner_id" required>{owner_choices}</select></label>'
        f'<label>Project <select name="project_id">{project_choices}</select></label>'
    )
    create_url = (
        '<form method="post" action="/dashboard/tracked-links/create" class="v984-link-form">'
        f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
        '<input type="hidden" name="target_type" value="url">'
        + scope
        + _form_field("Label", "label", placeholder="Campaign or purpose")
        + _form_field("Destination URL", "destination_url", placeholder="https://example.com/...")
        + '<button type="submit">Create tracked URL</button></form>'
    )
    create_file = (
        '<form method="post" action="/dashboard/tracked-links/create" class="v984-link-form">'
        f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
        '<input type="hidden" name="target_type" value="stored_file">'
        + scope
        + _form_field("Label", "label", placeholder="Document name or purpose")
        + _form_field("Stored File ID", "file_id", placeholder="FileStore file_id")
        + '<button type="submit">Create tracked file link</button></form>'
    )
    rows = []
    for link in results:
        oid = str(link.get("owner_id") or "")
        pid = str(link.get("project_id") or "")
        link_id = str(link.get("id") or "")
        status = str(link.get("status") or "unknown")
        target = str(link.get("destination_url") or link.get("filename") or link.get("file_id") or "")
        kind = "Tracked file" if link.get("target_type") == "stored_file" else "Tracked URL"
        tracked_url = str(link.get("tracked_url") or "")
        counters = link.get("analytics") or link.get("aggregate") or {}
        if not isinstance(counters, dict):
            counters = {}
        count = counters.get("total_clicks",link.get("total_clicks",0))
        revocation = ""
        if status == "active":
            revocation = (
                '<form method="post" action="/dashboard/tracked-links/revoke" '
                'onsubmit="return confirm(\'Revoke this tracked link? Existing clicks remain recorded, but the public URL stops working.\');">'
                f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
                f'<input type="hidden" name="owner_id" value="{_e(oid)}">'
                f'<input type="hidden" name="project_id" value="{_e(pid)}">'
                f'<input type="hidden" name="tracked_link_id" value="{_e(link_id)}">'
                '<input type="hidden" name="confirm" value="yes">'
                '<button type="submit" class="danger">Revoke</button></form>'
            )
        rows.append(
            '<tr>'
            f'<td><strong>{_e(link.get("label"))}</strong><div class="small muted">{_e(kind)} · {_e(status)}</div>'
            f'<small class="mono">{_e(link_id)}</small></td>'
            f'<td><span class="small">{_e(oid)} / {_e(pid or "owner-wide")}</span></td>'
            f'<td><span class="small">{_e(target)}</span></td>'
            f'<td><input aria-label="Tracked link URL" class="v984-link-value" readonly '
            f'value="{_e(tracked_url)}" onclick="this.select()" size="28"></td>'
            f'<td>{_e(count)}</td><td>{revocation}</td></tr>'
        )
    errors_html = (
        '<p class="small muted">Some owners could not be queried; their links are omitted.</p>'
        if errors else ""
    )
    return (
        '<section class="v984-panel v984-tracked-links" id="v984-tracked-links">'
        '<h3>Tracked URLs & files</h3>'
        '<p class="small muted">Create reusable tracked URLs or Stored File links. '
        'Owner and project are organizational scopes, not MCP permissions. '
        'File links point to a specific stored-file incarnation; revoking a link preserves click history.</p>'
        '<details><summary>Create tracked URL</summary>'+create_url+'</details>'
        '<details><summary>Create tracked file link</summary>'+create_file+'</details>'
        f'{errors_html}'
        '<div class="scroll"><table><thead><tr><th>Link</th><th>Owner / project</th>'
        '<th>Destination</th><th>Tracked address</th><th>Clicks</th><th>Action</th>'
        '</tr></thead><tbody>'
        + ("".join(rows) or '<tr><td colspan="6" class="muted">No tracked links in this scope.</td></tr>')
        + '</tbody></table></div></section>'
        '<style>.v984-tracked-links details{border:1px solid var(--line);border-radius:8px;'
        'padding:9px;margin:8px 0}.v984-tracked-links summary{font-weight:600;cursor:pointer}'
        '.v984-link-form{display:flex;gap:9px;flex-wrap:wrap;align-items:end;margin-top:12px}'
        '.v984-link-form label{display:flex;flex-direction:column;gap:5px}'
        '.v984-link-value{min-width:180px}.v984-tracked-links td{overflow-wrap:anywhere}</style>'
    )


def install_tracked_links_webgui_v984(app: Any, base: Any, core: Any) -> None:
    if any(
        isinstance(route, Route) and route.path == "/dashboard/tracked-links/create"
        for route in app.router.routes
    ):
        return

    async def create(request: Request) -> Response:
        form, error = await base._verified_form(request)
        if error is not None:
            return error
        try:
            owner, project = _scope(form, base)
            target_type = str(form.get("target_type") or "").strip()
            label = str(form.get("label") or "").strip()
            if not label:
                raise ValueError("A label is required")
            if target_type == "url":
                value = str(form.get("destination_url") or "").strip()
                result = core.create_tracked_link(
                    owner_id=owner, project_id=project,
                    destination_url=value, label=label,
                )
            elif target_type == "stored_file":
                fid = str(form.get("file_id") or "").strip()
                result = core.create_tracked_file_link(
                    owner_id=owner, project_id=project,
                    file_id=fid, label=label,
                )
            else:
                raise ValueError("Unsupported tracked-link target type")
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ValueError(str(result.get("error") if isinstance(result,dict) else "Link creation failed"))
        except Exception as exc:
            return PlainTextResponse(
                "Unable to create tracked link: " + str(exc)[:250],
                status_code=400,
                headers={"Cache-Control":"private, no-store"},
            )
        return RedirectResponse(_redirect(owner, project, "Tracked link created"), status_code=303)

    async def revoke(request: Request) -> Response:
        form, error = await base._verified_form(request)
        if error is not None:
            return error
        if str(form.get("confirm") or "") != "yes":
            return PlainTextResponse("Explicit revocation confirmation is required", status_code=400)
        try:
            owner, project = _scope(form, base)
            link_id = str(form.get("tracked_link_id") or "").strip()
            if not link_id:
                raise ValueError("Missing tracked link")
            result = core.revoke_tracked_link(
                owner_id=owner, project_id=project, tracked_link_id=link_id,
                confirm=True,
            )
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ValueError(str(result.get("error") if isinstance(result,dict) else "Revocation failed"))
        except Exception as exc:
            return PlainTextResponse(
                "Unable to revoke tracked link: " + str(exc)[:250],
                status_code=400,
                headers={"Cache-Control":"private, no-store"},
            )
        return RedirectResponse(_redirect(owner, project, "Tracked link revoked"),status_code=303)

    index = next(
        (i for i, route in enumerate(app.router.routes) if isinstance(route, Mount)),
        len(app.router.routes),
    )
    app.router.routes.insert(index, Route("/dashboard/tracked-links/create", create, methods=["POST"]))
    app.router.routes.insert(index+1, Route("/dashboard/tracked-links/revoke", revoke, methods=["POST"]))
