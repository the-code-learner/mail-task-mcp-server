from __future__ import annotations

from functools import lru_cache
from typing import Any

from .knowledge_scopes import KnowledgeScopeStore


@lru_cache(maxsize=1)
def knowledge_scope_store() -> KnowledgeScopeStore:
    return KnowledgeScopeStore()


def _validate_scopes(base: Any, scopes: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if scopes is None:
        return None
    if not isinstance(scopes, list):
        raise ValueError("scopes must be a list of {owner_id, project_id} objects")
    clean: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for raw in scopes:
        if not isinstance(raw, dict):
            raise ValueError("each scope must be an object")
        owner = str(raw.get("owner_id") or "").strip()
        project = str(raw.get("project_id") or "").strip() or None
        if not owner:
            raise ValueError("scope owner_id is required")
        base._require_knowledge_scope(owner, project)
        key = (owner, project)
        if key in seen:
            continue
        seen.add(key)
        clean.append({"owner_id": owner, "project_id": project})
    return clean


def _attach(item: dict[str, Any]) -> dict[str, Any]:
    return knowledge_scope_store().attach(item)


def _sync_primary(item: dict[str, Any], *, remove_previous_primary: bool = True) -> dict[str, Any]:
    knowledge_scope_store().sync_primary(
        str(item["id"]),
        owner_id=str(item["owner_id"]),
        project_id=str(item["project_id"]) if item.get("project_id") else None,
        remove_previous_primary=remove_previous_primary,
    )
    return _attach(item)


def _apply_scopes(
    item: dict[str, Any],
    scopes: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if scopes is None:
        return _sync_primary(item)
    knowledge_scope_store().set_scopes(
        str(item["id"]),
        scopes,
        primary_owner_id=str(item["owner_id"]),
        primary_project_id=str(item["project_id"]) if item.get("project_id") else None,
    )
    return _attach(item)


def _list_scoped(
    base: Any,
    *,
    kind: str,
    owner_id: str | None,
    project_id: str | None,
    project_ids: list[str] | None,
    include_global: bool,
    enabled_only: bool,
    tag: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    requested_projects = project_ids
    if requested_projects is None and project_id is not None:
        requested_projects = [project_id]
    if project_id is not None and owner_id is None:
        raise ValueError("owner_id is required when project_id is provided")
    if requested_projects and owner_id is None:
        raise ValueError("owner_id is required when project_ids are provided")
    if owner_id:
        if requested_projects:
            for project in requested_projects:
                if str(project or "").strip():
                    base._require_knowledge_scope(owner_id, str(project).strip())
        else:
            base._require_knowledge_scope(owner_id, None)

    allowed: set[str] | None = None
    if owner_id or requested_projects is not None:
        allowed = knowledge_scope_store().item_ids_for(
            owner_id=owner_id,
            project_ids=[str(value or "").strip() for value in (requested_projects or [])],
            include_global=include_global,
        )
        if not allowed:
            return []

    rows = base.context_engine().store.list_items(
        kind=kind,
        enabled_only=enabled_only,
        tag=tag,
        limit=1000,
    )
    if allowed is not None:
        rows = [row for row in rows if str(row.get("id") or "") in allowed]
    return knowledge_scope_store().attach_many(rows[: max(1, min(int(limit), 1000))])


def _search_scoped(
    base: Any,
    *,
    query: str,
    owner_id: str | None,
    project_id: str | None,
    project_ids: list[str] | None,
    include_global: bool,
    kinds: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    requested_projects = project_ids
    if requested_projects is None and project_id is not None:
        requested_projects = [project_id]
    if (project_id is not None or requested_projects) and owner_id is None:
        raise ValueError("owner_id is required when a project scope is provided")
    if owner_id:
        for project in requested_projects or []:
            project_clean = str(project or "").strip()
            if project_clean:
                base._require_knowledge_scope(owner_id, project_clean)

    allowed: set[str] | None = None
    if owner_id or requested_projects is not None:
        allowed = knowledge_scope_store().item_ids_for(
            owner_id=owner_id,
            project_ids=[str(value or "").strip() for value in (requested_projects or [])],
            include_global=include_global,
        )
        if not allowed:
            return {
                "ok": True,
                "query": (query or "").strip(),
                "semantic_active": False,
                "results": [],
                "scope_filter_applied": True,
                "project_ids": requested_projects or [],
            }

    search_limit = max(100, min(1000, int(limit) * 8))
    result = base.context_engine().search(
        query,
        owner_id=None,
        project_id=None,
        include_global=True,
        kinds=kinds,
        limit=min(search_limit, 100),
    )
    rows = list(result.get("results") or []) if isinstance(result, dict) else []
    if allowed is not None:
        rows = [
            row
            for row in rows
            if str(row.get("item_id") or row.get("id") or "") in allowed
        ]
    attached = []
    for row in rows[: max(1, min(int(limit), 100))]:
        item = dict(row)
        item_id = str(item.get("item_id") or item.get("id") or "")
        item["scopes"] = knowledge_scope_store().scopes_for(item_id) if item_id else []
        attached.append(item)
    out = dict(result) if isinstance(result, dict) else {"ok": True, "query": query}
    out["results"] = attached
    out["scope_filter_applied"] = allowed is not None
    out["project_ids"] = requested_projects or []
    return out


def _project_context_scoped(
    base: Any,
    *,
    owner_id: str,
    project_ids: list[str],
    query: str,
    budget_chars: int,
    kinds: list[str] | None,
    limit: int,
    include_global: bool,
) -> dict[str, Any]:
    for project in project_ids:
        if str(project or "").strip():
            base._require_knowledge_scope(owner_id, str(project).strip())
    allowed = knowledge_scope_store().item_ids_for(
        owner_id=owner_id,
        project_ids=[str(value or "").strip() for value in project_ids],
        include_global=include_global,
    )
    all_items = base.context_engine().store.list_items(
        owner_id=None,
        project_id=None,
        include_global=True,
        enabled_only=True,
        limit=1000,
    )
    items_by_id = {
        str(item["id"]): item
        for item in all_items
        if str(item.get("id") or "") in allowed
        and (not kinds or str(item.get("kind") or "") in set(kinds))
    }

    ranked_ids: list[str] = []
    if (query or "").strip():
        searched = _search_scoped(
            base,
            query=query,
            owner_id=owner_id,
            project_id=None,
            project_ids=project_ids,
            include_global=include_global,
            kinds=kinds,
            limit=max(1, min(int(limit), 100)),
        )
        ranked_ids = [
            str(row.get("item_id") or row.get("id") or "")
            for row in searched.get("results", [])
        ]
    always_ids = [
        item_id
        for item_id, item in items_by_id.items()
        if bool(item.get("always_include"))
    ]
    always_ids.sort(
        key=lambda item_id: (
            float(items_by_id[item_id].get("priority") or 0.0),
            str(items_by_id[item_id].get("updated_at") or ""),
        ),
        reverse=True,
    )
    remaining = sorted(
        items_by_id,
        key=lambda item_id: (
            float(items_by_id[item_id].get("priority") or 0.0),
            str(items_by_id[item_id].get("updated_at") or ""),
        ),
        reverse=True,
    )
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for item_id in always_ids + ranked_ids + remaining:
        if item_id and item_id not in seen:
            seen.add(item_id)
            ordered_ids.append(item_id)

    budget = max(1000, min(int(budget_chars), 200000))
    parts: list[str] = []
    sources: list[dict[str, Any]] = []
    used = 0
    for item_id in ordered_ids:
        if len(sources) >= max(1, min(int(limit), 100)) and item_id not in always_ids:
            break
        item = _attach(base.context_engine().store.get_item(item_id))
        scope_text = ", ".join(
            (
                str(scope.get("owner_id") or "")
                + "/"
                + str(scope.get("project_id") or "global")
            )
            for scope in item.get("scopes", [])
        )
        tags = ", ".join(item.get("tags") or [])
        header = (
            f"## {str(item.get('kind','')).upper()}: {item.get('title','')}\n"
            f"[scopes={scope_text}; priority={float(item.get('priority') or 0.5):.2f}; "
            f"revision={int(item.get('revision') or 1)}"
        )
        if tags:
            header += f"; tags={tags}"
        header += "]\n"
        text = header + str(item.get("content") or "").strip() + "\n"
        if used + len(text) > budget:
            remaining_chars = budget - used
            if remaining_chars < 240:
                break
            text = text[: remaining_chars - 24].rstrip() + "\n[…context truncated…]\n"
        parts.append(text)
        used += len(text)
        sources.append(
            {
                "id": item["id"],
                "kind": item["kind"],
                "title": item["title"],
                "owner_id": item["owner_id"],
                "project_id": item.get("project_id"),
                "scopes": item.get("scopes", []),
                "revision": item["revision"],
            }
        )
        if used >= budget:
            break
    return {
        "ok": True,
        "owner_id": owner_id,
        "project_id": project_ids[0] if len(project_ids) == 1 else None,
        "project_ids": project_ids,
        "include_global": include_global,
        "query": query,
        "budget_chars": budget,
        "used_chars": used,
        "item_count": len(sources),
        "context_text": "\n".join(parts),
        "sources": sources,
        "semantic_active": bool(base.context_engine().semantic._model is not None),
        "scope_model": "many_to_many_with_legacy_primary",
    }


def install_runtime_v960_knowledge(base: Any, core: Any) -> None:
    # Force migration/backfill only after KnowledgeStore has initialized its legacy tables.
    base.context_engine()
    knowledge_scope_store()

    def create_memory(
        owner_id: str,
        title: str,
        content: str,
        project_id: str | None = None,
        priority: float = 0.5,
        always_include: bool = False,
        enabled: bool = True,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        scopes: list[dict[str, Any]] | None = None,
    ):
        try:
            base._require_knowledge_scope(owner_id, project_id)
            clean_scopes = _validate_scopes(base, scopes)
            item = base.context_engine().create(
                kind="memory",
                owner_id=owner_id,
                project_id=project_id,
                title=title,
                content=content,
                priority=priority,
                always_include=always_include,
                enabled=enabled,
                tags=tags or [],
                metadata=metadata or {},
                actor="mcp",
            )
            return _apply_scopes(item, clean_scopes)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_memory(item_id: str):
        try:
            item = base._require_knowledge_kind(item_id, "memory")
            return _attach(item)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def update_memory(
        item_id: str,
        title: str | None = None,
        content: str | None = None,
        priority: float | None = None,
        always_include: bool | None = None,
        enabled: bool | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        scopes: list[dict[str, Any]] | None = None,
    ):
        try:
            current = base._require_knowledge_kind(item_id, "memory")
            final_owner = (owner_id or str(current["owner_id"])).strip()
            set_project = project_id is not None
            final_project = (
                (str(project_id or "").strip() or None)
                if set_project
                else (str(current["project_id"]) if current.get("project_id") else None)
            )
            base._require_knowledge_scope(final_owner, final_project)
            clean_scopes = _validate_scopes(base, scopes)
            item = base.context_engine().update(
                item_id,
                title=title,
                content=content,
                priority=priority,
                always_include=always_include,
                enabled=enabled,
                tags=tags,
                metadata=metadata,
                owner_id=final_owner if owner_id is not None else None,
                project_id=final_project,
                set_project=set_project,
                actor="mcp",
            )
            return _apply_scopes(item, clean_scopes)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def list_memories(
        owner_id: str | None = None,
        project_id: str | None = None,
        include_global: bool = True,
        enabled_only: bool = False,
        tag: str | None = None,
        limit: int = 200,
        project_ids: list[str] | None = None,
    ):
        try:
            return _list_scoped(
                base,
                kind="memory",
                owner_id=owner_id,
                project_id=project_id,
                project_ids=project_ids,
                include_global=include_global,
                enabled_only=enabled_only,
                tag=tag,
                limit=limit,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def create_skill(
        owner_id: str,
        title: str,
        content: str,
        project_id: str | None = None,
        priority: float = 0.5,
        always_include: bool = False,
        enabled: bool = True,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        scopes: list[dict[str, Any]] | None = None,
    ):
        try:
            base._require_knowledge_scope(owner_id, project_id)
            clean_scopes = _validate_scopes(base, scopes)
            item = base.context_engine().create(
                kind="skill",
                owner_id=owner_id,
                project_id=project_id,
                title=title,
                content=content,
                priority=priority,
                always_include=always_include,
                enabled=enabled,
                tags=tags or [],
                metadata=metadata or {},
                actor="mcp",
            )
            return _apply_scopes(item, clean_scopes)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_skill(item_id: str):
        try:
            item = base._require_knowledge_kind(item_id, "skill")
            return _attach(item)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def update_skill(
        item_id: str,
        title: str | None = None,
        content: str | None = None,
        priority: float | None = None,
        always_include: bool | None = None,
        enabled: bool | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        scopes: list[dict[str, Any]] | None = None,
    ):
        try:
            current = base._require_knowledge_kind(item_id, "skill")
            final_owner = (owner_id or str(current["owner_id"])).strip()
            set_project = project_id is not None
            final_project = (
                (str(project_id or "").strip() or None)
                if set_project
                else (str(current["project_id"]) if current.get("project_id") else None)
            )
            base._require_knowledge_scope(final_owner, final_project)
            clean_scopes = _validate_scopes(base, scopes)
            item = base.context_engine().update(
                item_id,
                title=title,
                content=content,
                priority=priority,
                always_include=always_include,
                enabled=enabled,
                tags=tags,
                metadata=metadata,
                owner_id=final_owner if owner_id is not None else None,
                project_id=final_project,
                set_project=set_project,
                actor="mcp",
            )
            return _apply_scopes(item, clean_scopes)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def list_skills(
        owner_id: str | None = None,
        project_id: str | None = None,
        include_global: bool = True,
        enabled_only: bool = False,
        tag: str | None = None,
        limit: int = 200,
        project_ids: list[str] | None = None,
    ):
        try:
            return _list_scoped(
                base,
                kind="skill",
                owner_id=owner_id,
                project_id=project_id,
                project_ids=project_ids,
                include_global=include_global,
                enabled_only=enabled_only,
                tag=tag,
                limit=limit,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def search_knowledge(
        query: str,
        owner_id: str | None = None,
        project_id: str | None = None,
        include_global: bool = True,
        kinds: list[str] | None = None,
        limit: int = 20,
        project_ids: list[str] | None = None,
    ):
        try:
            return _search_scoped(
                base,
                query=query,
                owner_id=owner_id,
                project_id=project_id,
                project_ids=project_ids,
                include_global=include_global,
                kinds=kinds,
                limit=limit,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_project_context(
        owner_id: str,
        project_id: str | None = None,
        query: str = "",
        budget_chars: int = 12000,
        kinds: list[str] | None = None,
        limit: int = 40,
        project_ids: list[str] | None = None,
        include_global: bool = True,
    ):
        try:
            requested = project_ids if project_ids is not None else ([project_id] if project_id is not None else [])
            if not requested:
                base._require_knowledge_scope(owner_id, None)
                # Global-only context retains the legacy ranking implementation.
                result = base.context_engine().project_context(
                    owner_id=owner_id,
                    project_id=None,
                    query=query,
                    budget_chars=budget_chars,
                    kinds=kinds,
                    limit=limit,
                )
                if isinstance(result, dict):
                    for source in result.get("sources", []):
                        source["scopes"] = knowledge_scope_store().scopes_for(str(source.get("id") or ""))
                    result["scope_model"] = "many_to_many_with_legacy_primary"
                return result
            return _project_context_scoped(
                base,
                owner_id=owner_id,
                project_ids=[str(value or "").strip() for value in requested],
                query=query,
                budget_chars=budget_chars,
                kinds=kinds,
                limit=limit,
                include_global=include_global,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def export_knowledge(owner_id: str | None = None, project_id: str | None = None):
        try:
            bundle = base.context_engine().store.export_bundle(owner_id=owner_id, project_id=project_id)
            if isinstance(bundle, dict):
                bundle = dict(bundle)
                bundle["scope_model"] = "many_to_many_with_legacy_primary"
                items = []
                for raw in bundle.get("items", []):
                    item = dict(raw)
                    item["scopes"] = knowledge_scope_store().scopes_for(str(item.get("id") or ""))
                    items.append(item)
                bundle["items"] = items
            return bundle
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def import_knowledge(
        bundle: dict[str, Any],
        owner_id_override: str | None = None,
        project_id_override: str | None = None,
        replace_existing: bool = False,
    ):
        try:
            items = bundle.get("items", []) if isinstance(bundle, dict) else []
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                primary_owner = owner_id_override or str(raw.get("owner_id") or "")
                primary_project = (
                    project_id_override
                    if project_id_override is not None
                    else (str(raw.get("project_id")) if raw.get("project_id") else None)
                )
                base._require_knowledge_scope(primary_owner, primary_project)
                if owner_id_override is None and project_id_override is None:
                    _validate_scopes(base, raw.get("scopes") if isinstance(raw.get("scopes"), list) else None)
            result = base.context_engine().store.import_bundle(
                bundle,
                owner_id_override=owner_id_override,
                project_id_override=project_id_override,
                replace_existing=replace_existing,
                actor="mcp-import",
            )
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item_id = str(raw.get("id") or "")
                if not item_id:
                    continue
                try:
                    imported = base.context_engine().store.get_item(item_id)
                except Exception:
                    continue
                raw_scopes = raw.get("scopes") if isinstance(raw.get("scopes"), list) else None
                if owner_id_override is not None or project_id_override is not None:
                    raw_scopes = None
                _apply_scopes(imported, _validate_scopes(base, raw_scopes))
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def restore_knowledge_revision(item_id: str, revision: int):
        try:
            result = base.context_engine().store.restore_revision(item_id, revision, actor="mcp-restore")
            base._require_knowledge_scope(
                str(result["owner_id"]),
                str(result["project_id"]) if result.get("project_id") else None,
            )
            base.context_engine()._index_item_if_loaded(item_id)
            return _sync_primary(result)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    replacements = {
        "create_memory": create_memory,
        "get_memory": get_memory,
        "update_memory": update_memory,
        "list_memories": list_memories,
        "create_skill": create_skill,
        "get_skill": get_skill,
        "update_skill": update_skill,
        "list_skills": list_skills,
        "search_knowledge": search_knowledge,
        "get_project_context": get_project_context,
        "export_knowledge": export_knowledge,
        "import_knowledge": import_knowledge,
        "restore_knowledge_revision": restore_knowledge_revision,
    }
    for name, fn in replacements.items():
        core.mcp.remove_tool(name)
        core.mcp.add_tool(fn, name=name)
        setattr(base, name, fn)
        setattr(core, name, fn)
