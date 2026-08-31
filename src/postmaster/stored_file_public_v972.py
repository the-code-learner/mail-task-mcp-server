from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from mcp.types import CallToolResult, ResourceLink, TextContent
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .email_analytics import AnalyticsError
from .file_handoff import (
    FileHandoffError,
    _download_secret,
    _public_base_url,
    _validate_signed_request,
)
from .file_store import FileStore, FileStoreError
from .link_tracking_html import collect_anchors, eligible_web_url, replace_href, rewrite_anchor_tags
from .stored_file_delivery import (
    StoredFileLinkTrackingStore,
    StoredFileMailError,
    _content_disposition,
    _parse_utc,
    _public_not_found,
    _stored_file_id_from_href,
    _validated_filename,
    _validated_media_type,
)

_PUBLIC_FILE_TOKEN_PREFIX = "sfp1_"
_PUBLIC_FILE_TOKEN_RE = re.compile(r"^sfp1_[0-9a-f]{64}$")
_PUBLIC_FILE_TOKEN_DOMAIN = "postmaster-public-stored-file-v1"
_SCAN_PAGE_SIZE = 1000


def _public_file_token_for_info(
    info: dict[str, Any],
    *,
    secret: bytes | None = None,
) -> str:
    canonical = "\n".join(
        (
            _PUBLIC_FILE_TOKEN_DOMAIN,
            str(info.get("id") or ""),
            str(info.get("sha256") or ""),
            str(info.get("created_at") or ""),
        )
    ).encode("utf-8")
    digest = hmac.new(secret or _download_secret(), canonical, hashlib.sha256).hexdigest()
    return f"{_PUBLIC_FILE_TOKEN_PREFIX}{digest}"


def build_public_stored_file_url(store: FileStore, file_id: str) -> str:
    info = store.get_info(file_id)
    base = _public_base_url(required=True)
    return f"{base}/t/c/{_public_file_token_for_info(info)}"


def resolve_public_stored_file_token(store: FileStore, token: str) -> dict[str, Any]:
    candidate = str(token or "").strip()
    if not _PUBLIC_FILE_TOKEN_RE.fullmatch(candidate):
        raise FileHandoffError("invalid public stored-file capability")

    max_files = min(int(store.max_files), int(store.HARD_MAX_FILES))
    secret = _download_secret()
    for offset in range(0, max_files, _SCAN_PAGE_SIZE):
        items = store.list_files(limit=_SCAN_PAGE_SIZE, offset=offset)
        for info in items:
            expected = _public_file_token_for_info(info, secret=secret)
            if hmac.compare_digest(expected, candidate):
                current = store.get_info(str(info.get("id") or ""))
                if not hmac.compare_digest(
                    _public_file_token_for_info(current, secret=secret),
                    candidate,
                ):
                    raise FileHandoffError("public stored-file capability no longer matches")
                return current
        if len(items) < _SCAN_PAGE_SIZE:
            break

    raise FileHandoffError("invalid public stored-file capability")


def _configured_public_path_prefix() -> tuple[str, str, str] | None:
    base = _public_base_url(required=False)
    if not base:
        return None
    parts = urlsplit(base)
    prefix = (parts.path or "").rstrip("/") + "/t/c/"
    return parts.scheme.lower(), parts.netloc.lower(), prefix


def _public_stored_file_token_from_url(url: str) -> str | None:
    """Cheap syntax/origin classifier. It never opens the File Store."""
    configured = _configured_public_path_prefix()
    if configured is None:
        return None
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return None
    scheme, netloc, prefix = configured
    if parts.scheme.lower() != scheme or parts.netloc.lower() != netloc:
        return None
    if parts.query or parts.fragment or not parts.path.startswith(prefix):
        return None
    token = parts.path[len(prefix):]
    if "/" in token or not token.startswith(_PUBLIC_FILE_TOKEN_PREFIX):
        return None
    if not _PUBLIC_FILE_TOKEN_RE.fullmatch(token):
        raise FileHandoffError("invalid public stored-file capability")
    return token


def resolve_public_stored_file_url(store: FileStore, url: str) -> dict[str, Any] | None:
    token = _public_stored_file_token_from_url(url)
    if token is None:
        return None
    return resolve_public_stored_file_token(store, token)


def _local_signed_file_capability(url: str) -> tuple[str, str, str] | None:
    """Recognize an old local /files capability without performing any network fetch."""
    base = _public_base_url(required=False)
    if not base:
        return None
    try:
        parts = urlsplit(str(url or "").strip())
        base_parts = urlsplit(base)
    except ValueError:
        return None
    if (
        parts.scheme.lower() != base_parts.scheme.lower()
        or parts.netloc.lower() != base_parts.netloc.lower()
        or parts.fragment
    ):
        return None
    prefix = (base_parts.path or "").rstrip("/") + "/files/"
    if not parts.path.startswith(prefix):
        return None
    encoded_id = parts.path[len(prefix):]
    if not encoded_id or "/" in encoded_id:
        return None
    file_id = unquote(encoded_id)
    if not file_id or any(ch in file_id for ch in "/\\\x00\r\n"):
        return None
    query = parse_qs(parts.query, keep_blank_values=True)
    expires = (query.get("expires") or [""])[0]
    signature = (query.get("sig") or [""])[0]
    if not expires or not signature:
        raise FileHandoffError("invalid local stored-file capability")
    return file_id, expires, signature


def _terminal_file_response(
    request: Request,
    store: FileStore,
    file_id: str,
    *,
    download_filename: str | None = None,
    download_media_type: str | None = None,
) -> Response:
    info, blob = store.raw_bytes(file_id)
    filename = _validated_filename(
        str(download_filename or info.get("filename") or ""),
        code="invalid_download_filename",
    )
    media_type = _validated_media_type(
        str(download_media_type or info.get("media_type") or ""),
        code="invalid_download_media_type",
    )
    headers = {
        "Content-Disposition": _content_disposition(filename),
        "Cache-Control": "private, no-store, no-cache, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if request.method.upper() == "HEAD":
        return Response(
            content=b"",
            media_type=media_type,
            headers={**headers, "Content-Length": str(len(blob))},
        )
    return Response(content=blob, media_type=media_type, headers=headers)


def _incarnation_matches(link: dict[str, Any], info: dict[str, Any]) -> bool:
    expected_sha = str(link.get("stored_file_sha256") or "")
    expected_created = str(link.get("stored_file_created_at") or "")
    if expected_sha or expected_created:
        return (
            bool(expected_sha)
            and bool(expected_created)
            and hmac.compare_digest(expected_sha, str(info.get("sha256") or ""))
            and hmac.compare_digest(expected_created, str(info.get("created_at") or ""))
        )

    link_created = _parse_utc(link.get("created_at"))
    file_created = _parse_utc(info.get("created_at"))
    if link_created is not None and file_created is not None and file_created > link_created:
        return False
    return True


def stored_file_resource_result_v972(
    store: FileStore,
    file_id: str,
    transport: str = "auto",
) -> CallToolResult:
    try:
        mode = (transport or "auto").strip().lower()
        if mode not in {"auto", "http", "mcp"}:
            raise FileHandoffError("transport must be one of: auto, http, mcp")

        info = store.get_info(file_id)
        if mode == "auto":
            mode = "http" if _public_base_url(required=False) else "mcp"

        if mode == "http":
            uri = build_public_stored_file_url(store, file_id)
        else:
            from urllib.parse import quote

            uri = f"postmaster://files/{quote(file_id, safe='')}"

        description = str(info.get("description") or "").strip() or "Postmaster stored file"
        return CallToolResult(
            content=[
                ResourceLink(
                    type="resource_link",
                    uri=uri,
                    name=str(info.get("filename") or file_id),
                    description=description,
                    mime_type=str(info.get("media_type") or "application/octet-stream"),
                    size=int(info.get("size_bytes") or 0),
                )
            ]
        )
    except FileStoreError as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=str(exc))],
            is_error=True,
        )


async def public_tracking_target_v972(
    request: Request,
    *,
    tracking_store: StoredFileLinkTrackingStore,
    file_store: FileStore,
    logger: Any,
) -> Response:
    token = str(request.path_params.get("token", "")).strip()

    if token.startswith(_PUBLIC_FILE_TOKEN_PREFIX):
        try:
            info = resolve_public_stored_file_token(file_store, token)
            return _terminal_file_response(request, file_store, str(info["id"]))
        except Exception:
            return _public_not_found()

    try:
        link = tracking_store.get_by_token(token)
        target_type = str(link.get("target_type") or "url")
        stored_file_id = ""
        signed_local: tuple[str, str, str] | None = None
        destination = ""

        if target_type == "stored_file":
            if str(link.get("status") or "active") != "active" or link.get("revoked_at"):
                return _public_not_found()
            expires_at = _parse_utc(link.get("expires_at"))
            if expires_at is not None and expires_at <= datetime.now(timezone.utc):
                return _public_not_found()
            stored_file_id = str(link.get("stored_file_id") or "")
            if not stored_file_id:
                return _public_not_found()
        elif target_type == "url":
            destination = str(link.get("original_url") or "")
            if not eligible_web_url(destination):
                return _public_not_found()
            signed_local = _local_signed_file_capability(destination)
        else:
            return _public_not_found()
    except Exception:
        return _public_not_found()

    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",", 1)[0].strip() if forwarded else ""
    if not client_ip and request.client:
        client_ip = request.client.host or ""
    try:
        tracking_store.record_click(
            link,
            user_agent=request.headers.get("user-agent", ""),
            client_ip=client_ip,
            country_code=request.headers.get("cf-ipcountry", ""),
        )
    except Exception:
        logger.info("Tracking event could not be recorded", exc_info=True)

    if target_type == "url" and signed_local is None:
        response = RedirectResponse(destination, status_code=302)
        response.headers["Cache-Control"] = "private, no-store, no-cache, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    try:
        if signed_local is not None:
            stored_file_id, expires_raw, signature = signed_local
            _validate_signed_request(
                file_store,
                stored_file_id,
                expires_raw,
                signature,
            )
            return _terminal_file_response(request, file_store, stored_file_id)

        current = file_store.get_info(stored_file_id)
        if not _incarnation_matches(link, current):
            return _public_not_found()
        return _terminal_file_response(
            request,
            file_store,
            stored_file_id,
            download_filename=str(link.get("download_filename") or ""),
            download_media_type=str(link.get("download_media_type") or ""),
        )
    except Exception:
        return _public_not_found()


class StoredFileLinkTrackingStoreV972(StoredFileLinkTrackingStore):
    """Per-runtime v9.7.2 tracking store; no class-global runtime capture."""

    def __init__(
        self,
        analytics: Any,
        *,
        file_store_provider: Callable[[], FileStore],
    ) -> None:
        self._v972_file_store_provider = file_store_provider
        super().__init__(analytics)

    def _file_store_v972(self) -> FileStore:
        return self._v972_file_store_provider()

    def _init_schema(self) -> None:
        super()._init_schema()
        additions = {
            "stored_file_sha256": "TEXT NOT NULL DEFAULT ''",
            "stored_file_created_at": "TEXT NOT NULL DEFAULT ''",
        }
        with self._connect() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(tracking_links)").fetchall()
            }
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE tracking_links ADD COLUMN {name} {declaration}")

    def _insert_stored_file_link(
        self,
        *,
        delivery: dict[str, Any],
        file_info: dict[str, Any],
        position: int,
        anchor_text: str,
    ) -> dict[str, Any]:
        record = super()._insert_stored_file_link(
            delivery=delivery,
            file_info=file_info,
            position=position,
            anchor_text=anchor_text,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tracking_links
                SET stored_file_sha256=?, stored_file_created_at=?
                WHERE id=?
                """,
                (
                    str(file_info.get("sha256") or ""),
                    str(file_info.get("created_at") or ""),
                    str(record["occurrence_id"]),
                ),
            )
        return record

    def _resolve_prior_tracking_url(self, url: str, public_base: Any):
        try:
            token = _public_stored_file_token_from_url(url)
        except FileHandoffError as exc:
            raise AnalyticsError("Invalid Postmaster stored-file capability") from exc
        if token is None:
            return super()._resolve_prior_tracking_url(url, public_base)
        try:
            resolve_public_stored_file_token(self._file_store_v972(), token)
        except (FileStoreError, FileHandoffError) as exc:
            raise AnalyticsError("Invalid Postmaster stored-file capability") from exc
        return str(url or "").strip(), False

    def instrument_html_with_shares(
        self,
        *,
        body_html: str,
        delivery: dict[str, Any],
        track_web_links: bool = True,
        stored_file_resolver: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[tuple[str, str]]]:
        html = body_html or ""
        if stored_file_resolver is not None and html:
            replacements: list[tuple[str, str]] = []
            for anchor in collect_anchors(html):
                href = str(anchor.get("href") or "")
                try:
                    token = _public_stored_file_token_from_url(href)
                except FileHandoffError as exc:
                    raise StoredFileMailError("stored_file_unavailable") from exc
                if token is None:
                    continue
                try:
                    info = resolve_public_stored_file_token(self._file_store_v972(), token)
                except (FileStoreError, FileHandoffError) as exc:
                    raise StoredFileMailError("stored_file_unavailable") from exc
                authorized = stored_file_resolver(str(info["id"]))
                if (
                    str(authorized.get("sha256") or "") != str(info.get("sha256") or "")
                    or str(authorized.get("created_at") or "")
                    != str(info.get("created_at") or "")
                ):
                    raise StoredFileMailError("stored_file_unavailable")
                replacements.append(
                    (
                        str(anchor["raw_tag"]),
                        replace_href(
                            str(anchor["raw_tag"]),
                            f"postmaster-file:{info['id']}",
                        ),
                    )
                )
            html = rewrite_anchor_tags(html, replacements)

        return super().instrument_html_with_shares(
            body_html=html,
            delivery=delivery,
            track_web_links=track_web_links,
            stored_file_resolver=stored_file_resolver,
        )

    def rewrite_stored_file_links_for_sent_copy(
        self,
        body_html: str,
        share_urls: list[tuple[str, str]],
    ) -> str:
        if not share_urls:
            return body_html
        queues: dict[str, list[str]] = {}
        for file_id, public_url in share_urls:
            queues.setdefault(file_id, []).append(public_url)

        replacements: list[tuple[str, str]] = []
        for anchor in collect_anchors(body_html or ""):
            href = str(anchor.get("href") or "")
            file_id = _stored_file_id_from_href(href)
            if file_id is None:
                try:
                    token = _public_stored_file_token_from_url(href)
                except FileHandoffError:
                    token = None
                if token is not None:
                    try:
                        info = resolve_public_stored_file_token(self._file_store_v972(), token)
                    except (FileStoreError, FileHandoffError):
                        info = None
                    file_id = str(info.get("id") or "") if info is not None else None
            if file_id is None or not queues.get(file_id):
                continue
            replacements.append(
                (
                    str(anchor["raw_tag"]),
                    replace_href(str(anchor["raw_tag"]), queues[file_id].pop(0)),
                )
            )
        return rewrite_anchor_tags(body_html or "", replacements)


def bind_stored_file_link_store_v972(base: Any) -> Callable[[], StoredFileLinkTrackingStoreV972]:
    """Create a lazy per-runtime store factory with the existing cache_clear contract."""

    @lru_cache(maxsize=1)
    def _store() -> StoredFileLinkTrackingStoreV972:
        return StoredFileLinkTrackingStoreV972(
            base.analytics_store(),
            file_store_provider=base.file_store,
        )

    return _store


def install_stored_file_public_v972(base: Any, core: Any) -> None:
    """Install the resource handoff helper without eager DB or global class mutation."""
    del base
    core.stored_file_resource_result = stored_file_resource_result_v972
