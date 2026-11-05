from __future__ import annotations

import inspect
from typing import Any

from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from .email_analytics import AnalyticsError
from .generic_tracking_v982 import GenericTrackedLinkError
from .generic_tracking_v983 import GenericTrackedLinkStoreV983
from .stored_file_public_v972 import _terminal_file_response

MCP_COMMAND_COUNT_V982 = 123
MCP_TRACKED_FILE_COMMANDS_V983 = 1
MCP_COMMAND_COUNT_V983 = MCP_COMMAND_COUNT_V982 + MCP_TRACKED_FILE_COMMANDS_V983


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


def install_runtime_v983(
    app: Any,
    base: Any,
    core: Any,
    previous_runtime_status: Any,
    *,
    store: GenericTrackedLinkStoreV983,
) -> dict[str, Any]:
    """Install v9.8.3 tracked Stored File links over the v9.8.2 generic-link surface."""

    def authorize(owner_id: str, project_id: str | None) -> None:
        base._require_knowledge_scope(
            str(owner_id or "").strip(),
            str(project_id).strip() if project_id else None,
        )

    def create_tracked_file_link(
        owner_id: str,
        file_id: str,
        label: str,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ):
        """WRITE ACTION. Create a tracked public download URL for one Stored File file_id.

        This is the MCP equivalent of Postmaster's tracked Stored File links in email. The link is
        bound to the exact current file incarnation (file_id + SHA-256 + created_at), so deleting
        and recreating the same file_id cannot resurrect an old public link. The returned
        tracked_url is intentionally complete so the MCP client/LLM may use it in any
        user-authorized context. label is required. Optional idempotency_key retries the exact
        same create intent safely.
        """
        authorize(owner_id, project_id)
        return _safe(
            store.create_file,
            owner_id=owner_id,
            file_id=file_id,
            label=label,
            project_id=project_id,
            idempotency_key=idempotency_key,
        )

    core.mcp.add_tool(
        create_tracked_file_link,
        name="create_tracked_file_link",
        annotations=_annotations(read_only=False),
    )
    core.create_tracked_file_link = create_tracked_file_link
    base.create_tracked_file_link = create_tracked_file_link

    legacy_endpoint = None
    routes = app.router.routes
    for index, route in enumerate(list(routes)):
        if isinstance(route, Route) and route.path == "/t/c/{token}":
            legacy_endpoint = route.endpoint

            async def tracked_file_or_previous_target(
                request: Request,
                _legacy=legacy_endpoint,
            ):
                token = str(request.path_params.get("token", "")).strip()
                if not store.is_generic_token(token):
                    result = _legacy(request)
                    return await result if inspect.isawaitable(result) else result

                try:
                    link = store.get_by_token(token)
                except Exception:
                    return PlainTextResponse(
                        "Not found",
                        status_code=404,
                        headers={
                            "Cache-Control": "private, no-store, no-cache, max-age=0",
                            "Pragma": "no-cache",
                        },
                    )

                if str(link.get("target_type") or "url") != "stored_file":
                    result = _legacy(request)
                    return await result if inspect.isawaitable(result) else result

                forwarded = request.headers.get("x-forwarded-for", "")
                client_ip = forwarded.split(",", 1)[0].strip() if forwarded else ""
                if not client_ip and request.client:
                    client_ip = request.client.host or ""
                try:
                    store.record_click(
                        link,
                        user_agent=request.headers.get("user-agent", ""),
                        client_ip=client_ip,
                        country_code=request.headers.get("cf-ipcountry", ""),
                    )
                except Exception:
                    base.logger.info(
                        "Generic tracked-file click could not be recorded",
                        exc_info=True,
                    )

                try:
                    info = store.resolve_file_target(link)
                    return _terminal_file_response(
                        request,
                        base.file_store(),
                        str(info["id"]),
                        expected_sha256=str(link.get("stored_file_sha256") or ""),
                        expected_created_at=str(
                            link.get("stored_file_created_at") or ""
                        ),
                        download_filename=str(
                            link.get("download_filename") or info.get("filename") or ""
                        ),
                        download_media_type=str(
                            link.get("download_media_type")
                            or info.get("media_type")
                            or ""
                        ),
                    )
                except Exception:
                    return PlainTextResponse(
                        "Not found",
                        status_code=404,
                        headers={
                            "Cache-Control": "private, no-store, no-cache, max-age=0",
                            "Pragma": "no-cache",
                        },
                    )

            routes[index] = Route(
                "/t/c/{token}",
                tracked_file_or_previous_target,
                methods=["GET"],
            )
            break

    if legacy_endpoint is None:
        raise RuntimeError("v9.8.3 requires the existing /t/c/{token} tracking route")

    def runtime_status():
        status = previous_runtime_status()
        if not isinstance(status, dict):
            status = {"ok": True}
        status = dict(status)
        generic = dict(status.get("generic_tracked_links") or {})
        generic.update(store.status())
        generic.update(
            {
                "stored_file_targets_supported": True,
                "stored_file_exact_incarnation_enforced": True,
                "tracked_file_delivery": "direct_bytes",
            }
        )
        status.update(
            {
                "version_capability": "9.8.3",
                "mcp_command_count_expected": MCP_COMMAND_COUNT_V983,
                "mcp_command_count_delta_from_v982": MCP_TRACKED_FILE_COMMANDS_V983,
                "generic_tracked_links": generic,
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

    return {
        "store": store,
        "runtime_status": runtime_status,
        "mcp_command_count_expected": MCP_COMMAND_COUNT_V983,
    }


__all__ = [
    "MCP_COMMAND_COUNT_V983",
    "MCP_TRACKED_FILE_COMMANDS_V983",
    "install_runtime_v983",
]
