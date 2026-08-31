from __future__ import annotations

import re
from email import policy
from email.parser import BytesParser
from html import escape
from typing import Any
from urllib.parse import quote, urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route


STYLE = r'''
/* post-v9.7.0 mail files + composer IA */
.v971-attachments{margin:12px 0}.v971-attachment-list{display:grid;gap:8px;margin-top:8px}.v971-attachment{display:flex;align-items:center;justify-content:space-between;gap:12px;border:1px solid var(--line);border-radius:10px;padding:10px;background:color-mix(in srgb,var(--surface) 96%,var(--v963-accent) 4%)}.v971-attachment-meta{min-width:0}.v971-attachment-meta strong,.v971-attachment-meta code{overflow-wrap:anywhere}.v971-attachment-actions{display:flex;gap:6px;flex-wrap:wrap;flex:0 0 auto}
.v971-floating-compose{position:fixed;right:24px;bottom:24px;z-index:1200;width:min(720px,calc(100vw - 48px));max-height:min(82vh,820px);overflow:auto;box-shadow:0 18px 60px rgba(0,0,0,.38);margin:0}.v971-floating-compose>summary{position:sticky;top:0;z-index:2;background:var(--card);padding:12px 14px}.v971-floating-compose:not([open]){width:auto;overflow:visible;border:0;background:transparent;box-shadow:none}.v971-floating-compose:not([open])>summary{position:static;display:inline-flex;align-items:center;gap:8px;border:1px solid color-mix(in srgb,var(--accent) 70%,var(--line));border-radius:999px;padding:12px 16px;background:var(--card2);box-shadow:0 10px 30px rgba(0,0,0,.30);font-weight:800;color:var(--text)}.v971-floating-compose:not([open])>summary::marker{content:""}.v971-file-picker{width:100%;min-height:126px}.v971-compose-note{margin:6px 0 0}.v971-keyword{min-width:240px}
@media(max-width:760px){.v971-floating-compose{right:12px;bottom:12px;width:calc(100vw - 24px);max-height:86vh}.v971-attachment{align-items:flex-start;flex-direction:column}.v971-attachment-actions{width:100%}}
'''

_SAFE_IMAGE_TYPES = {
    "image/avif", "image/gif", "image/jpeg", "image/png", "image/webp",
}
_SAFE_VIDEO_TYPES = {
    "video/mp4", "video/ogg", "video/webm",
}
_SAFE_AUDIO_TYPES = {
    "audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/webm",
}
_FILTER_RE = re.compile(
    r'<label>Subject <input name="subject" value="[^"]*"></label>'
    r'<label>Text <input name="text" value="[^"]*"></label>'
)
_INSTALLED_FLAG = "_mail_files_composer_v971_installed"


def _attachment_records(raw: bytes) -> list[dict[str, Any]]:
    message = BytesParser(policy=policy.default).parsebytes(bytes(raw))
    rows: list[dict[str, Any]] = []
    for part_index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue
        filename = str(part.get_filename() or "").strip()
        disposition = str(part.get_content_disposition() or "").strip().lower()
        if not filename and disposition != "attachment":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            value = part.get_payload()
            payload = str(value or "").encode(part.get_content_charset() or "utf-8", errors="replace")
        media_type = str(part.get_content_type() or "application/octet-stream").lower()
        rows.append(
            {
                "part_index": part_index,
                "filename": filename or f"attachment-{len(rows) + 1}",
                "media_type": media_type,
                "size_bytes": len(payload),
                "content": bytes(payload),
            }
        )
    return rows


def _preview_supported(media_type: str) -> bool:
    media = str(media_type or "").lower()
    return (
        media == "application/pdf"
        or media.startswith("text/")
        or media in _SAFE_IMAGE_TYPES
        or media in _SAFE_VIDEO_TYPES
        or media in _SAFE_AUDIO_TYPES
    )


def _safe_filename(value: str) -> str:
    name = str(value or "attachment").replace("\r", "").replace("\n", "").replace("\\", "_").replace('"', "_")
    name = name.rsplit("/", 1)[-1].strip() or "attachment"
    return name[:255]


def _content_disposition(kind: str, filename: str) -> str:
    safe = _safe_filename(filename)
    return f"{kind}; filename=\"{safe}\"; filename*=UTF-8''{quote(safe)}"


def _attachment_query(account_id: str, mailbox: str, uid: str, part_index: int) -> str:
    return urlencode(
        {
            "account_id": account_id,
            "mailbox": mailbox,
            "uid": uid,
            "part": str(part_index),
        }
    )


def _attachments_html(raw: bytes, *, account_id: str, mailbox: str, uid: str) -> str:
    try:
        rows = _attachment_records(raw)
    except Exception as exc:
        return (
            '<section class="card v971-attachments"><h4>Attachments</h4>'
            f'<p class="small muted">Attachment metadata could not be parsed ({escape(type(exc).__name__)}). '
            "The message reader remains available.</p></section>"
        )
    if not rows:
        return ""
    rendered: list[str] = []
    for row in rows:
        query = _attachment_query(account_id, mailbox, uid, int(row["part_index"]))
        download = f"/dashboard/inbox/attachment/download?{query}"
        preview = f"/dashboard/inbox/attachment/preview?{query}"
        preview_html = (
            f'<a href="{escape(preview, quote=True)}" target="_blank" rel="noopener"><button type="button">Preview</button></a>'
            if _preview_supported(str(row["media_type"]))
            else '<span class="small muted">Preview unavailable</span>'
        )
        rendered.append(
            '<div class="v971-attachment">'
            f'<div class="v971-attachment-meta"><strong>{escape(str(row["filename"]))}</strong>'
            f'<div class="small muted"><code>{escape(str(row["media_type"]))}</code> · {int(row["size_bytes"])} bytes</div></div>'
            f'<div class="v971-attachment-actions">{preview_html}'
            f'<a href="{escape(download, quote=True)}"><button type="button">Download</button></a></div></div>'
        )
    return (
        '<section class="card v971-attachments"><h4>Attachments</h4>'
        '<p class="small muted">Preview is best-effort and cache-only. Download stays available independently.</p>'
        '<div class="v971-attachment-list">' + "".join(rendered) + "</div></section>"
    )


def _find_attachment(base: Any, request: Request) -> tuple[dict[str, Any] | None, Response | None]:
    account_id = str(request.query_params.get("account_id") or "").strip()
    mailbox = str(request.query_params.get("mailbox") or "").strip()
    uid = str(request.query_params.get("uid") or "").strip()
    try:
        part_index = int(request.query_params.get("part") or "-1")
    except ValueError:
        part_index = -1
    if not account_id or not mailbox or not uid or part_index < 0:
        return None, PlainTextResponse("Invalid attachment reference", status_code=400)
    # Deliberately cache-only: attachment viewing must never trigger IMAP or any remote fetch.
    raw = base.mailbox_cache_store().raw_message(account_id, mailbox, uid)
    if not raw:
        return None, PlainTextResponse("Cached message is unavailable", status_code=404)
    try:
        row = next(
            (item for item in _attachment_records(raw) if int(item["part_index"]) == part_index),
            None,
        )
    except Exception:
        row = None
    if row is None:
        return None, PlainTextResponse("Attachment not found", status_code=404)
    return row, None


def _download_response(base: Any, request: Request) -> Response:
    row, error = _find_attachment(base, request)
    if error is not None:
        return error
    assert row is not None
    return Response(
        row["content"],
        media_type=str(row["media_type"]),
        headers={
            "Content-Disposition": _content_disposition("attachment", str(row["filename"])),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _preview_response(base: Any, request: Request) -> Response:
    row, error = _find_attachment(base, request)
    if error is not None:
        return error
    assert row is not None
    media_type = str(row["media_type"])
    if not _preview_supported(media_type):
        return PlainTextResponse(
            "Preview is not available for this attachment type. Use Download instead.",
            status_code=415,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    content = row["content"]
    if media_type.startswith("text/"):
        content = bytes(content).decode("utf-8", errors="replace").encode("utf-8")
        media_type = "text/plain; charset=utf-8"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition("inline", str(row["filename"])),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox; frame-ancestors 'self'",
            "Referrer-Policy": "no-referrer",
        },
    )


def _file_options(base: Any) -> str:
    try:
        rows = base.file_store().list_files(limit=200)
    except Exception:
        rows = []
    options: list[str] = []
    for row in rows:
        file_id = str(row.get("id") or "").strip()
        if not file_id:
            continue
        label = str(row.get("filename") or file_id)
        project = str(row.get("project_id") or "Global")
        options.append(
            f'<option value="{escape(file_id, quote=True)}">{escape(label)} · {escape(project)}</option>'
        )
    return "".join(options)


def _floating_composer(base: Any, accounts: list[dict[str, Any]], account_id: str | None, *, mailbox: str, uid: str, role: str, v960: Any) -> str:
    html = v960._compose_panel(base, accounts, account_id, mailbox=mailbox, uid=uid, role=role)
    html = html.replace('class="card v960-compose"', 'class="card v960-compose v971-floating-compose"', 1)
    html = html.replace('<summary>Compose inside Inbox</summary>', '<summary>Compose</summary>', 1)
    marker = '<label class="wide">Stored File attachment IDs <input name="attachment_file_ids" placeholder="file_id_1, file_id_2"></label>'
    options = _file_options(base)
    picker = (
        '<label class="wide">Attachments'
        f'<select class="v971-file-picker" name="attachment_file_ids" multiple>{options}</select>'
        '<span class="small muted">Select one or more stored Files. The existing MIME attachment pipeline is reused for send, draft, reply and follow-up.</span></label>'
    )
    html = html.replace(marker, picker, 1)
    return html


def _filter_html(html: str, request: Request) -> str:
    keyword = str(
        request.query_params.get("keyword")
        or request.query_params.get("text")
        or request.query_params.get("subject")
        or ""
    ).strip()
    replacement = (
        '<label>Keyword <input class="v971-keyword" name="keyword" value="'
        + escape(keyword, quote=True)
        + '" placeholder="Subject, sender, recipient or message text"></label>'
    )
    html = _FILTER_RE.sub(replacement, html, count=1)
    html = html.replace('<label>Mailbox ', '<label>Folder ', 1)
    return html


def install_mail_files_composer_v971(app: Any, base: Any, webgui_v963: Any, webgui_v962: Any, webgui_v964: Any) -> None:
    """Install cache-only attachment UX and Inbox-first composer IA without changing MCP schemas."""
    if getattr(webgui_v963, _INSTALLED_FLAG, False):
        return

    original_params = webgui_v963._params
    original_detail = webgui_v963._detail
    original_render = webgui_v963.render_inbox_v963
    original_payload = webgui_v964._form_payload

    def params_v971(request: Request, account_id: str | None) -> dict[str, str]:
        values = original_params(request, account_id)
        keyword = str(
            request.query_params.get("keyword")
            or request.query_params.get("text")
            or request.query_params.get("subject")
            or ""
        ).strip()
        values.pop("subject", None)
        values["text"] = keyword
        return values

    def detail_v971(proxied_base: Any, params: dict[str, str], account_id: str, mailbox: str, role: str, uid: str, request: Request) -> str:
        html = original_detail(proxied_base, params, account_id, mailbox, role, uid, request)
        try:
            raw = proxied_base.mailbox_cache_store().raw_message(account_id, mailbox, uid)
            attachment_html = _attachments_html(raw or b"", account_id=account_id, mailbox=mailbox, uid=uid) if raw else ""
            if attachment_html:
                close = html.rfind("</div>")
                if close >= 0:
                    html = html[:close] + attachment_html + html[close:]
        except Exception:
            # Attachment enrichment is read-only. Message metadata/body must remain usable.
            pass
        return html

    def payload_v971(form: Any) -> dict[str, Any]:
        payload = original_payload(form)
        values: list[str] = []
        try:
            raw_values = list(form.getlist("attachment_file_ids"))
        except Exception:
            raw_values = [form.get("attachment_file_ids")]
        for raw in raw_values:
            for value in webgui_v963.v960._split_addresses(raw):
                if value not in values:
                    values.append(value)
        payload["attachments"] = [{"file_id": value} for value in values]
        return payload

    def render_inbox_v971(proxied_base: Any, request: Request) -> str:
        html = original_render(proxied_base, request)
        try:
            html = _filter_html(html, request)
            accounts, account_id = webgui_v963._selected(proxied_base, request)
            if account_id:
                catalog = proxied_base.mailbox_cache_store().list_mailboxes(str(account_id))
                mailbox = str(request.query_params.get("mailbox") or "INBOX").strip() or "INBOX"
                available = [str(row.get("name") or "") for row in catalog if row.get("name")]
                if available and mailbox not in available:
                    mailbox = next((str(row.get("name") or "") for row in catalog if row.get("role") == "received"), available[0])
                role = webgui_v963._role(catalog, mailbox)
                uid = str(request.query_params.get("message_uid") or "").strip()
                composer = _floating_composer(
                    proxied_base, accounts, str(account_id), mailbox=mailbox, uid=uid, role=role, v960=webgui_v963.v960
                )
                flash = str(request.query_params.get("compose_result") or "").strip()
                if flash:
                    composer = f'<div class="flash">{escape(flash)}</div>' + composer
                close = html.rfind("</section>")
                if close >= 0:
                    html = html[:close] + composer + html[close:]
        except Exception:
            # Composer inventory is convenience-only. Inbox remains usable if Files are unavailable.
            pass
        return html

    webgui_v963._params = params_v971
    webgui_v963._detail = detail_v971
    webgui_v964._form_payload = payload_v971
    webgui_v963.render_inbox_v963 = render_inbox_v971
    webgui_v963.v960.render_inbox = render_inbox_v971
    webgui_v963.v951.render_inbox = render_inbox_v971

    # Tracking and Compose remain addressable for backward-compatible deep links, but are no
    # longer top-level destinations after Inbox/Sent have contextual feature parity.
    webgui_v962.NAV = tuple(row for row in webgui_v962.NAV if row[0] not in {"compose", "tracking"})
    if "post-v9.7.0 mail files + composer IA" not in webgui_v962.BASE_STYLE:
        webgui_v962.BASE_STYLE += STYLE

    routes = app.router.routes
    paths = {
        "/dashboard/inbox/attachment/download",
        "/dashboard/inbox/attachment/preview",
    }
    routes[:] = [route for route in routes if not (isinstance(route, Route) and route.path in paths)]

    async def download_route(request: Request) -> Response:
        return _download_response(base, request)

    async def preview_route(request: Request) -> Response:
        return _preview_response(base, request)

    mount_index = next((index for index, route in enumerate(routes) if isinstance(route, Mount)), len(routes))
    routes.insert(
        mount_index,
        Route("/dashboard/inbox/attachment/download", download_route, methods=["GET"], name="v971_attachment_download"),
    )
    routes.insert(
        mount_index + 1,
        Route("/dashboard/inbox/attachment/preview", preview_route, methods=["GET"], name="v971_attachment_preview"),
    )
    setattr(webgui_v963, _INSTALLED_FLAG, True)


__all__ = [
    "_attachment_records",
    "_attachments_html",
    "_filter_html",
    "_preview_supported",
    "install_mail_files_composer_v971",
]
