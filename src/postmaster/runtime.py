from __future__ import annotations

import os

import uvicorn
from mcp.types import CallToolResult
from starlette.requests import Request
from starlette.routing import Mount, Route

from . import server as _base
from .file_handoff import (
    read_stored_file_resource,
    stored_file_http_response,
    stored_file_resource_result,
)


mcp = _base.mcp
_legacy_build_status = _base.build_status


def build_status():
    """Read-only. Return running build identity and v9.3 file-handoff capability."""
    status = _legacy_build_status()
    status["native_file_resource_handoff"] = True
    return status


# Replace only the registered build-status implementation. MCPServer v2 exposes
# remove_tool() and add_tool() as public APIs; all v9.2 upload/file/mail tools stay intact.
mcp.remove_tool("build_status")
mcp.add_tool(build_status, name="build_status")
_base.build_status = build_status


@mcp.tool()
def get_stored_file_resource(file_id: str, transport: str = "auto") -> CallToolResult:
    """
    Read-only. Return a native MCP ResourceLink for a FileStore file.

    transport=auto prefers a configured signed HTTPS file URL and otherwise
    returns the canonical postmaster:// resource URI. transport=http requires
    FILE_STORE_PUBLIC_BASE_URL or PUBLIC_MCP_HOST; transport=mcp always returns
    the MCP resource.
    """
    return stored_file_resource_result(_base.file_store(), file_id, transport)


@mcp.resource(
    "postmaster://files/{file_id}",
    name="postmaster_stored_file",
    description="Original bytes for a Postmaster FileStore file identified by canonical file_id.",
)
def stored_file_resource(file_id: str) -> bytes:
    """Read original FileStore bytes; the MCP SDK emits BlobResourceContents for binary bytes."""
    return read_stored_file_resource(_base.file_store(), file_id)


async def public_stored_file_download(request: Request):
    return stored_file_http_response(request, _base.file_store(), require_signature=True)


async def dashboard_file_download(request: Request):
    return stored_file_http_response(request, _base.file_store(), require_signature=False)


# Replace the old dashboard download endpoint (which materialized the whole blob)
# and insert the signed handoff route before the catch-all MCP Mount.
_routes = _base.app.router.routes
for index, route in enumerate(list(_routes)):
    if isinstance(route, Route) and route.path == "/dashboard/files/{file_id}/download":
        _routes[index] = Route(
            "/dashboard/files/{file_id}/download",
            dashboard_file_download,
            methods=["GET", "HEAD"],
        )
        break

if not any(isinstance(route, Route) and route.path == "/files/{file_id}" for route in _routes):
    mount_index = next((i for i, route in enumerate(_routes) if isinstance(route, Mount)), len(_routes))
    _routes.insert(
        mount_index,
        Route("/files/{file_id}", public_stored_file_download, methods=["GET", "HEAD"]),
    )


app = _base.app

# Re-export the v9.2 and earlier callable surface for tests/importers that use the
# deployment entrypoint module rather than postmaster.server directly.
for _name in dir(_base):
    if _name.startswith("_") or _name in globals():
        continue
    globals()[_name] = getattr(_base, _name)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
        log_level="info",
    )
