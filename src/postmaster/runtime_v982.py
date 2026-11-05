from __future__ import annotations

import inspect
from typing import Any

from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Route

from .email_analytics import AnalyticsError
from .generic_tracking_v982 import (
    GenericTrackedLinkError,
    GenericTrackedLinkStore,
)

MCP_COMMAND_COUNT_V981 = 118
MCP_GENERIC_TRACKED_LINK_COMMANDS_V982 = 5
MCP_COMMAND_COUNT_V982 = MCP_COMMAND_COUNT_V981 + MCP_GENERIC_TRACKED_LINK_COMMANDS_V982


def _annotations(
    *,
    read_only: bool,
    destructive: bool = False,
    idempotent: bool = False,
):
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=False,
    )


def _safe(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except (GenericTrackedLinkError, AnalyticsError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def install_runtime_v982(
    app: Any,
    base: Any,
    core: Any,
    previous_runtime_status: Any,
    *,
    store: GenericTrackedLinkStore | None = None,
) -> dict[str, Any]:
    """Install v9.8.2 generic MCP tracked links without changing email-link semantics."""

    service = store or GenericTrackedLinkStore(base.analytics_store())

    def authorize(owner_id: str, project_id: str | None) -> None:
        # Reuse the established Files/Memory/Skills project-scope authorization boundary.
        base._require_knowledge_scope(
            str(owner_id or "").strip(),
            str(project_id).strip() if project_id else None,
        )

    def create_tracked_link(
        owner_id: str,
        destination_url: str,
        label: str,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ):
        """WRITE ACTION. Create a reusable generic tracked HTTP/HTTPS link.

        The returned tracked_url is intentionally complete and may be used by the MCP client/LLM
        in any user-authorized context (chat, document, social post, QR payload, etc.), not only
        email. label is required so the link remains human/agent identifiable. Optional
        project_id scopes it to an existing Postmaster project. Reusing the same non-empty
        idempotency_key with the same intent returns the original link.
        """
        authorize(owner_id, project_id)
        return _safe(
            service.create,
            owner_id=owner_id,
            destination_url=destination_url,
            label=label,
            project_id=project_id,
            idempotency_key=idempotency_key,
        )

    def list_tracked_links(
        owner_id: str,
        project_id: str | None = None,
        label: str | None = None,
        active_only: bool = True,
        clicked_only: bool = False,
        limit: int = 100,
    ):
        """Read-only. List generic tracked links and return their complete tracked_url values.

        These are generic MCP-managed links, separate from email tracking occurrences. Omit
        project_id for an owner-wide view, or provide it for exact project filtering.
        """
        authorize(owner_id, project_id)
        return _safe(
            service.list_links,
            owner_id=owner_id,
            project_id=project_id,
            label=label,
            active_only=active_only,
            clicked_only=clicked_only,
            limit=limit,
        )

    def get_tracked_link(
        owner_id: str,
        tracked_link_id: str,
        project_id: str | None = None,
    ):
        """Read-only. Return one generic tracked link, complete tracked_url and click counters."""
        authorize(owner_id, project_id)
        return _safe(
            service.get,
            owner_id=owner_id,
            tracked_link_id=tracked_link_id,
            project_id=project_id,
        )

    def list_tracked_link_events(
        owner_id: str,
        project_id: str | None = None,
        tracked_link_id: str | None = None,
        limit: int = 100,
    ):
        """Read-only. List click telemetry for generic tracked links without storing raw IP."""
        authorize(owner_id, project_id)
        return _safe(
            service.list_events,
            owner_id=owner_id,
            project_id=project_id,
            tracked_link_id=tracked_link_id,
            limit=limit,
        )

    def revoke_tracked_link(
        owner_id: str,
        tracked_link_id: str,
        project_id: str | None = None,
        confirm: bool = False,
    ):
        """DESTRUCTIVE WRITE. Disable a generic tracked URL after explicit confirm=true.

        Revocation preserves its telemetry/history but the public token immediately stops
        redirecting. The operation is idempotent after the first successful revocation.
        """
        authorize(owner_id, project_id)
        return _safe(
            service.revoke,
            owner_id=owner_id,
            tracked_link_id=tracked_link_id,
            project_id=project_id,
            confirm=confirm,
        )

    additions = (
        (
            "create_tracked_link",
            create_tracked_link,
            _annotations(read_only=False),
        ),
        (
            "list_tracked_links",
            list_tracked_links,
            _annotations(read_only=True, idempotent=True),
        ),
        (
            "get_tracked_link",
            get_tracked_link,
            _annotations(read_only=True, idempotent=True),
        ),
        (
            "list_tracked_link_events",
            list_tracked_link_events,
            _annotations(read_only=True, idempotent=True),
        ),
        (
            "revoke_tracked_link",
            revoke_tracked_link,
            _annotations(read_only=False, destructive=True, idempotent=True),
        ),
    )
    for name, fn, annotations in additions:
        core.mcp.add_tool(fn, name=name, annotations=annotations)
        setattr(core, name, fn)
        setattr(base, name, fn)

    legacy_endpoint = None
    routes = app.router.routes
    for index, route in enumerate(list(routes)):
        if isinstance(route, Route) and route.path == "/t/c/{token}":
            legacy_endpoint = route.endpoint

            async def generic_or_legacy_tracking_target(
                request: Request,
                _legacy=legacy_endpoint,
            ):
                token = str(request.path_params.get("token", "")).strip()
                if not service.is_generic_token(token):
                    result = _legacy(request)
                    return await result if inspect.isawaitable(result) else result

                try:
                    link = service.get_by_token(token)
                    destination = str(link.get("destination_url") or "")
                except Exception:
                    return PlainTextResponse(
                        "Not found",
                        status_code=404,
                        headers={"Cache-Control": "private, no-store, no-cache, max-age=0"},
                    )

                forwarded = request.headers.get("x-forwarded-for", "")
                client_ip = forwarded.split(",", 1)[0].strip() if forwarded else ""
                if not client_ip and request.client:
                    client_ip = request.client.host or ""
                try:
                    service.record_click(
                        link,
                        user_agent=request.headers.get("user-agent", ""),
                        client_ip=client_ip,
                        country_code=request.headers.get("cf-ipcountry", ""),
                    )
                except Exception:
                    base.logger.info(
                        "Generic tracked-link click could not be recorded",
                        exc_info=True,
                    )

                response = RedirectResponse(destination, status_code=302)
                response.headers["Cache-Control"] = "private, no-store, no-cache, max-age=0"
                response.headers["Pragma"] = "no-cache"
                return response

            routes[index] = Route(
                "/t/c/{token}",
                generic_or_legacy_tracking_target,
                methods=["GET"],
            )
            break

    if legacy_endpoint is None:
        raise RuntimeError("v9.8.2 requires the existing /t/c/{token} tracking route")

    def runtime_status():
        status = previous_runtime_status()
        if not isinstance(status, dict):
            status = {"ok": True}
        status = dict(status)
        status.update(
            {
                "version_capability": "9.8.2",
                "mcp_command_count_expected": MCP_COMMAND_COUNT_V982,
                "mcp_command_count_delta_from_v981": MCP_GENERIC_TRACKED_LINK_COMMANDS_V982,
                "generic_tracked_links": {
                    **service.status(),
                    "project_scoped": True,
                    "owner_wide_listing_supported": True,
                    "labels_required": True,
                    "revocation_supported": True,
                    "email_tracking_semantics_unchanged": True,
                },
            }
        )
        return status

    core.mcp.remove_tool("runtime_status")
    core.mcp.add_tool(
        runtime_status,
        name="runtime_status",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    core.runtime_status = runtime_status
    base.runtime_status = runtime_status
    base.generic_tracked_link_store_v982 = lambda: service

    return {
        "store": service,
        "runtime_status": runtime_status,
        "mcp_command_count_expected": MCP_COMMAND_COUNT_V982,
    }


__all__ = [
    "MCP_COMMAND_COUNT_V982",
    "MCP_GENERIC_TRACKED_LINK_COMMANDS_V982",
    "install_runtime_v982",
]
