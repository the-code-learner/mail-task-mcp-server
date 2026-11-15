"""Postmaster-hosted inline email images, optionally serving as open beacons.

Images are immutable scoped Stored Files exposed only through active, exact-incarnation
generic tracked-file capability tokens. No remote image URL or pixel is needed.
"""
from __future__ import annotations

import os
import re
from html import escape
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route

from .email_analytics import _safe_base_url

_MIME = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}
_TAG = re.compile(r'(<(?:amp-img|img)\b[^>]*?\s+src\s*=\s*)(["\'])([^"\']+)\2', re.I | re.S)
_TOKEN = re.compile(r"glnk_[A-Za-z0-9_-]{12,140}\Z")


def image_media_type(content: bytes) -> str | None:
    """Allow bounded raster formats only: never SVG/HTML or browser-sniffed content."""
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content[:6] in {b"GIF87a", b"GIF89a"} and len(content) >= 13:
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP" and len(content) >= 16:
        return "image/webp"
    return None


def image_url(tracked_file_url: str) -> str:
    public_base = _safe_base_url()
    if not public_base.startswith("https://"):
        raise ValueError("HTTPS is required for public email images")
    base = public_base + "/t/c/"
    if not str(tracked_file_url).startswith(base):
        raise ValueError("unexpected public tracked-file origin")
    token = str(tracked_file_url)[len(base):]
    if not _TOKEN.fullmatch(token):
        raise ValueError("invalid tracked-file capability")
    return _safe_base_url() + "/t/i/" + quote(token, safe="")


def personalize_images(html: str, delivery_token: str, *, tracking: bool) -> tuple[str, bool]:
    """Individualize the FIRST Postmaster inline raster in each MIME alternative.

    The rest are delivered untracked; thus a message with multiple images does
    not generate multiple open events for one render. No URL is changed when
    tracking is disabled, including the sender's clean Sent archive.
    """
    source = str(html or "")
    if not tracking or not delivery_token:
        return source, False
    prefix = _safe_base_url() + "/t/i/"
    used = False

    def rewrite(match: re.Match[str]) -> str:
        nonlocal used
        url = match.group(3)
        token = url[len(prefix):] if url.startswith(prefix) else ""
        if used or not _TOKEN.fullmatch(token):
            return match.group(0)
        used = True
        return (match.group(1) + match.group(2)
                + escape(prefix + token + "/" + quote(delivery_token, safe=""), quote=True)
                + match.group(2))

    return _TAG.sub(rewrite, source), used


def _not_found() -> PlainTextResponse:
    return PlainTextResponse(
        "Not found", status_code=404,
        headers={"Cache-Control": "private, no-store, no-cache, max-age=0",
                 "X-Content-Type-Options": "nosniff"},
    )


def serve_inline_image(base: Any, store: Any, request: Request) -> Response:
    token = str(request.path_params.get("token") or "")
    delivery_token = str(request.path_params.get("delivery_token") or "")
    if not _TOKEN.fullmatch(token):
        return _not_found()
    try:
        link = store.get_by_token(token)
        if str(link.get("target_type") or "") != "stored_file":
            return _not_found()
        info = store.resolve_file_target(link)
        media = str(info.get("media_type") or "").split(";", 1)[0].strip().lower()
        if media not in _MIME:
            return _not_found()
        meta, content = base.file_store().raw_bytes(str(info["id"]))
        if image_media_type(content) != media:
            return _not_found()
    except Exception:
        return _not_found()

    if delivery_token and request.method.upper() == "GET":
        try:
            analytics = base.analytics_store()
            delivery = analytics.get_delivery_by_tracking_token(delivery_token)
            campaign = analytics.get_campaign(str(delivery["campaign_id"]))
            if bool(campaign.get("track_opens")):
                forwarded = request.headers.get("x-forwarded-for", "")
                client_ip = forwarded.split(",", 1)[0].strip() if forwarded else ""
                if not client_ip and request.client:
                    client_ip = request.client.host or ""
                analytics.record_inline_image_open(
                    delivery_token, user_agent=request.headers.get("user-agent", ""),
                    client_ip=client_ip, country_code=request.headers.get("cf-ipcountry", ""),
                )
        except Exception:
            # Invalid delivery tokens are never permitted to fetch a tracked variant.
            return _not_found()
    elif delivery_token:
        # HEAD is allowed for mail-proxy probes without counting an open; token
        # validity is still checked.
        try:
            base.analytics_store().get_delivery_by_tracking_token(delivery_token)
        except Exception:
            return _not_found()

    headers = {
        "Cache-Control": "private, no-store, no-cache, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
        "Referrer-Policy": "no-referrer",
    }
    if request.method.upper() == "HEAD":
        return Response(b"", media_type=media, headers={**headers, "Content-Length": str(len(content))})
    return Response(content, media_type=media, headers=headers)


async def upload_compose_image(base: Any, store: Any, request: Request) -> Response:
    form, error = await base._verified_form(request)
    if error:
        return JSONResponse({"ok": False, "error": "Invalid or expired CSRF token"}, status_code=403,
                            headers={"Cache-Control": "no-store"})
    upload = form.get("file")
    owner = str(form.get("owner_id") or os.getenv("DEFAULT_OWNER_ID", "")).strip()
    project = str(form.get("project_id") or "").strip() or None
    if not owner or len(owner) > 160:
        return JSONResponse({"ok": False, "error": "Select an Owner before uploading an image"}, status_code=400)
    if project and len(project) > 160:
        return JSONResponse({"ok": False, "error": "Invalid project"}, status_code=400)
    if upload is None or not hasattr(upload, "read") or not getattr(upload, "filename", ""):
        return JSONResponse({"ok": False, "error": "Choose a raster image"}, status_code=400)

    try:
        # Resolve public image delivery before storing any bytes.
        if not _safe_base_url().startswith("https://"):
            raise ValueError("Public email image hosting requires HTTPS")
        file_store = base.file_store()
        max_bytes = min(file_store.max_bytes, 5_000_000)
        content = await upload.read(max_bytes + 1)
        if not content or len(content) > max_bytes:
            raise ValueError(f"Image exceeds configured storage limit ({max_bytes} bytes)")
        media = image_media_type(content)
        if media is None:
            raise ValueError("Only PNG, JPEG, GIF and WebP raster images are supported")
        info = file_store.save_bytes(
            owner_id=owner, project_id=project, filename=str(upload.filename),
            data=content, media_type=media, description="Composer inline image",
            tags=["compose-image"],
        )
        link = store.create_file(
            owner_id=owner, project_id=project, file_id=str(info["id"]),
            label="Composer image: " + str(info.get("filename") or "")[:180],
        )
        url = image_url(str(link["tracked_url"]))
        return JSONResponse({
            "ok": True, "file_id": str(info["id"]), "image_url": url,
            "media_type": media, "size_bytes": int(info.get("size_bytes") or len(content)),
        }, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    except Exception as exc:
        # No secrets / network internals in the error response.
        message = str(exc) if isinstance(exc, ValueError) else "Image could not be stored"
        return JSONResponse({"ok": False, "error": message[:200]}, status_code=400,
                            headers={"Cache-Control": "no-store"})


def install_compose_images(app: Any, base: Any, store: Any) -> None:
    paths = {"/t/i/{token}", "/t/i/{token}/{delivery_token}", "/dashboard/compose/image/upload"}
    if paths.issubset({r.path for r in app.router.routes if isinstance(r, Route)}):
        return

    async def image_handler(request: Request) -> Response:
        return serve_inline_image(base, store, request)

    async def upload_handler(request: Request) -> Response:
        return await upload_compose_image(base, store, request)

    index = next((i for i, route in enumerate(app.router.routes) if isinstance(route, Mount)),
                 len(app.router.routes))
    for route in (
        Route("/t/i/{token}/{delivery_token}", image_handler, methods=["GET", "HEAD"]),
        Route("/t/i/{token}", image_handler, methods=["GET", "HEAD"]),
        Route("/dashboard/compose/image/upload", upload_handler, methods=["POST"]),
    ):
        if not any(isinstance(r, Route) and r.path == route.path for r in app.router.routes):
            app.router.routes.insert(index, route)
            index += 1
