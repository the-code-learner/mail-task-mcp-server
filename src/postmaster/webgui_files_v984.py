"""v9.8.4 authenticated Stored File inline preview, separate from download."""
from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route

from .webgui_mail_files_v971 import (
    _content_disposition, _preview_supported,
)

_TEXT_LIKE = {"application/json", "application/xml", "application/x-yaml", "application/yaml"}
_CSP = "default-src 'none'; sandbox; frame-ancestors 'self'"


def preview_file(base: Any, request: Request) -> Response:
    """Inline only for safe MIME; HTML/XML/JSON always served as inert plain text."""
    file_id = str(request.path_params.get("file_id") or "").strip()
    if not file_id or len(file_id) > 256:
        return PlainTextResponse("Not found", status_code=404)
    try:
        result = base.file_store().read_base64(file_id)
        if not isinstance(result, dict) or not result.get("ok"):
            raise ValueError("not found")
        meta = dict(result["file"])
        content = base64.b64decode(result["content_base64"], validate=True)
        media = str(meta.get("media_type") or "application/octet-stream").split(";", 1)[0].strip().lower()
    except Exception:
        return PlainTextResponse("File not found", status_code=404)
    if not _preview_supported(media) and media not in _TEXT_LIKE:
        return PlainTextResponse(
            "Preview unavailable for this MIME type. Use Download.",
            status_code=415,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    if media.startswith("text/") or media in _TEXT_LIKE:
        content = content.decode("utf-8", errors="replace").encode("utf-8")
        media = "text/plain; charset=utf-8"
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": _content_disposition("inline", str(meta.get("filename") or file_id)),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": _CSP,
            "Referrer-Policy": "no-referrer",
        },
    )


def install_file_previews_v984(app: Any, base: Any) -> None:
    route_path = "/dashboard/files/{file_id}/preview"
    if any(isinstance(route, Route) and route.path == route_path for route in app.router.routes):
        return

    async def handler(request: Request) -> Response:
        return preview_file(base, request)

    index = next((i for i, r in enumerate(app.router.routes) if isinstance(r, Mount)), len(app.router.routes))
    app.router.routes.insert(index, Route(route_path, handler, methods=["GET"]))
