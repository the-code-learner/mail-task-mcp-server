"""v9.8.4 authenticated Stored File inline preview, separate from download."""
from __future__ import annotations

import base64
from io import BytesIO
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route

from .webgui_mail_files_v971 import (
    _content_disposition, _preview_supported,
)

_TEXT_LIKE = {"application/json", "application/xml", "application/x-yaml", "application/yaml"}
_CSP = "default-src 'none'; sandbox; frame-ancestors 'self'"
# Browser PDF viewers cannot reliably run inside an iframe/CSP sandbox:
# embed only exact PDF MIME with same-origin framing; all other types retain
# the original sandbox. Built-in PDF viewers isolate PDF execution.
_PDF_CSP = "frame-ancestors 'self'"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAX_DOCX_XML = 3_000_000


def _docx_preview_text(content: bytes) -> bytes:
    """Extract readable, inert text from a small OOXML Word document.

    No external renderer, remote requests, or macros. Bound compressed and
    decompressed sizes before parsing so storage previews cannot unzip bombs.
    """
    if len(content) > 2_000_000:
        raise ValueError("Word file is too large to preview")
    try:
        with ZipFile(BytesIO(content)) as archive:
            if len(archive.infolist()) > 1000:
                raise ValueError("Word document contains too many ZIP entries")
            info = archive.getinfo("word/document.xml")
            if info.file_size > _MAX_DOCX_XML:
                raise ValueError("Word document XML is too large")
            with archive.open(info) as document:
                raw = document.read(_MAX_DOCX_XML + 1)
            if len(raw) > _MAX_DOCX_XML:
                raise ValueError("Word document XML is too large")
        xml = ElementTree.fromstring(raw)
    except (BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, OSError) as exc:
        raise ValueError("Word file is invalid or cannot be previewed") from exc

    paragraphs: list[str] = []
    for paragraph in xml.iter(_DOCX_NS + "p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == _DOCX_NS + "t" and node.text:
                parts.append(node.text)
            elif node.tag == _DOCX_NS + "tab":
                parts.append("\t")
            elif node.tag in (_DOCX_NS + "br", _DOCX_NS + "cr"):
                parts.append("\n")
        if parts:
            paragraphs.append("".join(parts))
        if len(paragraphs) >= 6000:
            break
    text = "\n".join(paragraphs).strip()
    if not text:
        text = "This Word document contains no extractable paragraph text."
    return text[:400_000].encode("utf-8", errors="replace")



def preview_supported(media_type: str) -> bool:
    """Share the exact preview allowlist with the file manager controls.

    Unknown/unsafe formats get a metadata view and separate download rather
    than being served inline with a potentially executable content type.
    """
    media = str(media_type or "").split(";", 1)[0].strip().lower()
    return _preview_supported(media) or media in _TEXT_LIKE or media == _DOCX


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
    if not preview_supported(media):
        return PlainTextResponse(
            "Preview unavailable for this MIME type. Use Download.",
            status_code=415,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    if media == _DOCX:
        try:
            content = _docx_preview_text(content)
        except ValueError:
            return PlainTextResponse(
                "Word document cannot be safely previewed. Use Download.",
                status_code=415,
                headers={"Cache-Control": "private, no-store",
                         "X-Content-Type-Options": "nosniff"},
            )
        media = "text/plain; charset=utf-8"
    elif media.startswith("text/") or media in _TEXT_LIKE:
        content = content.decode("utf-8", errors="replace").encode("utf-8")
        media = "text/plain; charset=utf-8"
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": _content_disposition("inline", str(meta.get("filename") or file_id)),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": _PDF_CSP if media == "application/pdf" else _CSP,
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
