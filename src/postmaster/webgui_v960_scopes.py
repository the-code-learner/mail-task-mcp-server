from __future__ import annotations

import json
from html import escape
from typing import Any

from starlette.requests import Request
from starlette.routing import Route

from . import webgui_v945 as v945
from . import webgui_v960 as v960
from .runtime_v960_knowledge import knowledge_scope_store
from .webgui_helpers import project_rows


def _scope_value(owner_id: str, project_id: str | None) -> str:
    return json.dumps(
        {"owner_id": owner_id, "project_id": project_id},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_scope_values(values: list[str]) -> list[dict[str, Any]]:
    scopes: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for raw in values:
        try:
            value = json.loads(str(raw or ""))
        except Exception as exc:
            raise ValueError("invalid knowledge scope selection") from exc
        if not isinstance(value, dict):
            raise ValueError("invalid knowledge scope selection")
        owner = str(value.get("owner_id") or "").strip()
        project = str(value.get("project_id") or "").strip() or None
        if not owner:
            raise ValueError("scope owner_id is required")
        key = (owner, project)
        if key in seen:
            continue
        seen.add(key)
        scopes.append({"owner_id": owner, "project_id": project})
    return scopes


def _selected_scopes(base: Any, request: Request) -> set[tuple[str, str]]:
    item_id = (request.query_params.get("edit_knowledge") or "").strip()
    if not item_id:
        return set()
    try:
        item = base.context_engine().store.get_item(item_id)
        attached = knowledge_scope_store().attach(item)
    except Exception:
        return set()
    return {
        (str(scope.get("owner_id") or ""), str(scope.get("project_id") or ""))
        for scope in attached.get("scopes", [])
        if isinstance(scope, dict) and scope.get("owner_id")
    }


def _scope_editor(base: Any, request: Request) -> str:
    selected = _selected_scopes(base, request)
    owners_result = base._safe_call(base.scheduler().list_owners)
    owners = owners_result if isinstance(owners_result, list) else []
    projects = project_rows(base)

    choices: list[tuple[str, str | None, str]] = []
    for owner in owners:
        owner_id = str(owner.get("id") or "").strip()
        if owner_id:
            label = str(owner.get("name") or owner_id)
            choices.append((owner_id, None, f"{label} / Global"))
    for project in projects:
        project_id = str(project.get("id") or "").strip()
        owner_id = str(project.get("owner_id") or "").strip()
        if not project_id or not owner_id:
            continue
        label = str(project.get("name") or project_id)
        choices.append((owner_id, project_id, f"{owner_id} / {label}"))

    seen: set[tuple[str, str | None]] = set()
    controls: list[str] = []
    for owner_id, project_id, label in choices:
        key = (owner_id, project_id)
        if key in seen:
            continue
        seen.add(key)
        checked = " checked" if (owner_id, project_id or "") in selected else ""
        value = escape(_scope_value(owner_id, project_id), quote=True)
        controls.append(
            f'<label><input type="checkbox" name="scope" value="{value}"{checked}> '
            f'{escape(label)}</label>'
        )
    if not controls:
        controls.append('<span class="small muted">No owner/project scope choices are registered.</span>')

    return (
        '<details class="v960-advanced"><summary>Additional Knowledge scopes</summary>'
        '<p class="small muted">Select any additional owner/project scopes for this item. '
        'The Owner + Primary project fields above remain the legacy primary scope and are always retained.</p>'
        '<div class="v951-checks">' + "".join(controls) + '</div></details>'
    )


def _augment_knowledge_fragment(base: Any, request: Request, html: str) -> str:
    marker = '<div class="field"><label>Content (Markdown)</label>'
    if marker not in html:
        return html
    return html.replace(marker, _scope_editor(base, request) + marker, 1)


async def knowledge_save(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    try:
        item_id = str(form.get("item_id") or "").strip()
        kind = str(form.get("kind") or "memory").strip().casefold()
        if kind not in {"memory", "skill"}:
            raise ValueError("kind must be memory or skill")
        owner_id = str(form.get("owner_id") or "").strip()
        project_raw = str(form.get("project_id") or "").strip()
        project_id = project_raw or None
        raw_scope_values = list(form.getlist("scope")) if hasattr(form, "getlist") else []
        scopes = decode_scope_values([str(value) for value in raw_scope_values])
        tags = [part.strip() for part in str(form.get("tags") or "").split(",") if part.strip()]
        common = {
            "title": str(form.get("title") or ""),
            "content": str(form.get("content") or ""),
            "priority": float(str(form.get("priority") or "0.5")),
            "always_include": str(form.get("always_include") or "") == "1",
            "enabled": str(form.get("enabled") or "") == "1",
            "tags": tags,
            "owner_id": owner_id,
            "scopes": scopes,
        }
        if item_id:
            existing = base.context_engine().store.get_item(item_id)
            if str(existing.get("kind") or "") != kind:
                raise ValueError("Changing memory/skill kind in-place is not supported")
            updater = base.update_memory if kind == "memory" else base.update_skill
            result = updater(item_id, project_id=project_raw, **common)
            success = "Knowledge item updated"
        else:
            creator = base.create_memory if kind == "memory" else base.create_skill
            result = creator(project_id=project_id, **common)
            success = "Knowledge item created"
        if not isinstance(result, dict) or result.get("ok") is False:
            detail = result.get("error") if isinstance(result, dict) else result
            raise RuntimeError(str(detail or "Knowledge save failed"))
        return base._redir(success, "knowledge")
    except Exception as exc:
        base.logger.info("v9.6 Knowledge save failed", exc_info=True)
        return base._redir(f"{type(exc).__name__}: {exc}", "knowledge")


def install_webgui_v960_scopes(app: Any, base: Any) -> None:
    """Add editable many-to-many Knowledge scopes without changing legacy routes or MCP names."""
    legacy_fragment = v960.knowledge_fragment

    def knowledge_fragment(base_arg: Any, request: Request) -> str:
        return _augment_knowledge_fragment(base_arg, request, legacy_fragment(base_arg, request))

    v960.knowledge_fragment = knowledge_fragment
    v945.knowledge_fragment = knowledge_fragment

    routes = app.router.routes
    for index, route in enumerate(list(routes)):
        if isinstance(route, Route) and route.path == "/dashboard/knowledge/save":
            async def save_route(request: Request):
                return await knowledge_save(base, request)
            routes[index] = Route("/dashboard/knowledge/save", save_route, methods=["POST"])
            break
