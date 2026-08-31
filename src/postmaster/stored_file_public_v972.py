from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from functools import lru_cache
from html import unescape
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

from mcp.types import CallToolResult, ResourceLink, TextContent
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .email_analytics import AnalyticsError, _now, _safe_base_url, _token
from .file_handoff import FileHandoffError, _public_base_url, _validate_signed_request
from .file_store import FileStore, FileStoreError
from .link_tracking_html import (
    already_tracked_url,
    collect_anchors,
    eligible_web_url,
    replace_href,
    rewrite_anchor_tags,
)
from .mail_bridge import MailBridgeError
from .mail_v960_unsubscribe import PostmasterV960NewsletterMailClient
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

_PUBLIC_FILE_TOKEN_PREFIX = "sfc1_"
_PUBLIC_FILE_TOKEN_RE = re.compile(r"^sfc1_[A-Za-z0-9_-]{40,80}$")
_PUBLIC_FILE_TOKEN_HASH_DOMAIN = "postmaster-stored-file-capability-token-v1"
_MAX_TRACKING_CHAIN_DEPTH = 8
_INTERNAL_CAPABILITY_KEY = "__stored_file_capability_id"


def _public_token_hash(token: str) -> str:
    canonical = f"{_PUBLIC_FILE_TOKEN_HASH_DOMAIN}\0{str(token or '').strip()}".encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _configured_public_path_prefix() -> tuple[str, str, str] | None:
    base = _public_base_url(required=False)
    if not base:
        return None
    parts = urlsplit(base)
    return (
        parts.scheme.lower(),
        parts.netloc.lower(),
        (parts.path or "").rstrip("/") + "/t/c/",
    )


def _public_stored_file_token_from_url(url: str) -> str | None:
    """Cheap public-capability classifier. It never touches FileStore."""
    configured = _configured_public_path_prefix()
    if configured is None:
        return None
    try:
        parts = urlsplit(unescape(str(url or "").strip()))
    except ValueError:
        return None
    scheme, netloc, prefix = configured
    if parts.scheme.lower() != scheme or parts.netloc.lower() != netloc:
        return None
    if parts.query or parts.fragment or not parts.path.startswith(prefix):
        return None
    token = parts.path[len(prefix) :]
    if "/" in token or not token.startswith(_PUBLIC_FILE_TOKEN_PREFIX):
        return None
    if not _PUBLIC_FILE_TOKEN_RE.fullmatch(token):
        raise FileHandoffError("invalid public stored-file capability")
    return token


def _local_signed_file_capability(url: str) -> tuple[str, str, str] | None:
    """Recognize a historical local /files capability without a network hop."""
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
    encoded_id = parts.path[len(prefix) :]
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


def _capability_active(capability: dict[str, Any]) -> bool:
    if str(capability.get("status") or "active") != "active" or capability.get("revoked_at"):
        return False
    expires_at = _parse_utc(capability.get("expires_at"))
    return expires_at is None or expires_at > datetime.now(timezone.utc)


def _same_incarnation(capability: dict[str, Any], info: dict[str, Any]) -> bool:
    expected_sha = str(capability.get("file_sha256") or "")
    expected_created = str(capability.get("file_created_at") or "")
    return (
        bool(expected_sha)
        and bool(expected_created)
        and hmac.compare_digest(expected_sha, str(info.get("sha256") or ""))
        and hmac.compare_digest(expected_created, str(info.get("created_at") or ""))
    )


def _legacy_incarnation_matches(link: dict[str, Any], info: dict[str, Any]) -> bool:
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
    return not (
        link_created is not None
        and file_created is not None
        and file_created > link_created
    )


def _terminal_file_response(
    request: Request,
    store: FileStore,
    file_id: str,
    *,
    expected_sha256: str = "",
    expected_created_at: str = "",
    download_filename: str | None = None,
    download_media_type: str | None = None,
) -> Response:
    info, blob = store.raw_bytes(file_id)
    if expected_sha256 or expected_created_at:
        if not (
            expected_sha256
            and expected_created_at
            and hmac.compare_digest(expected_sha256, str(info.get("sha256") or ""))
            and hmac.compare_digest(expected_created_at, str(info.get("created_at") or ""))
        ):
            raise FileHandoffError("stored-file capability no longer matches")
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


class StoredFileLinkTrackingStoreV972(StoredFileLinkTrackingStore):
    """Lazy per-runtime DB-first Stored File capability/tracking store."""

    def __init__(self, analytics: Any, *, file_store_provider: Callable[[], FileStore]) -> None:
        self._v972_file_store_provider = file_store_provider
        super().__init__(analytics)

    def _file_store_v972(self) -> FileStore:
        return self._v972_file_store_provider()

    def _init_schema(self) -> None:
        super()._init_schema()
        additions = {
            "stored_file_sha256": "TEXT NOT NULL DEFAULT ''",
            "stored_file_created_at": "TEXT NOT NULL DEFAULT ''",
            "stored_file_capability_id": "TEXT",
        }
        with self._connect() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(tracking_links)").fetchall()
            }
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE tracking_links ADD COLUMN {name} {declaration}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS stored_file_capabilities (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT,
                    file_id TEXT NOT NULL,
                    file_sha256 TEXT NOT NULL,
                    file_created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    revoked_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ix_stored_file_capabilities_token_hash
                    ON stored_file_capabilities(token_hash)
                    WHERE token_hash IS NOT NULL;
                CREATE INDEX IF NOT EXISTS ix_stored_file_capabilities_file
                    ON stored_file_capabilities(file_id, created_at);
                CREATE INDEX IF NOT EXISTS ix_tracking_links_stored_file_capability
                    ON tracking_links(stored_file_capability_id);
                """
            )

    def create_stored_file_capability(
        self,
        file_info: dict[str, Any],
        *,
        public_token: bool,
        expires_at: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        file_id = str(file_info.get("id") or "").strip()
        file_sha256 = str(file_info.get("sha256") or "").strip()
        file_created_at = str(file_info.get("created_at") or "").strip()
        if not file_id or not file_sha256 or not file_created_at:
            raise AnalyticsError("Stored File capability requires exact file incarnation metadata")
        capability_id = f"sfcap_{_token(12)}"
        raw_token = f"{_PUBLIC_FILE_TOKEN_PREFIX}{_token(32)}" if public_token else None
        token_hash = _public_token_hash(raw_token) if raw_token else None
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stored_file_capabilities(
                    id,token_hash,file_id,file_sha256,file_created_at,status,created_at,expires_at,revoked_at
                ) VALUES(?,?,?,?,?,'active',?,?,NULL)
                """,
                (
                    capability_id,
                    token_hash,
                    file_id,
                    file_sha256,
                    file_created_at,
                    now,
                    expires_at,
                ),
            )
        return (
            {
                "id": capability_id,
                "file_id": file_id,
                "file_sha256": file_sha256,
                "file_created_at": file_created_at,
                "status": "active",
                "created_at": now,
                "expires_at": expires_at,
                "revoked_at": None,
            },
            raw_token,
        )

    def get_public_capability_by_token(self, token: str) -> dict[str, Any]:
        candidate = str(token or "").strip()
        if not _PUBLIC_FILE_TOKEN_RE.fullmatch(candidate):
            raise AnalyticsError("Unknown Stored File capability")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM stored_file_capabilities WHERE token_hash=?",
                (_public_token_hash(candidate),),
            ).fetchone()
        if not row:
            raise AnalyticsError("Unknown Stored File capability")
        return dict(row)

    def get_stored_file_capability(self, capability_id: str) -> dict[str, Any]:
        capability_id = str(capability_id or "").strip()
        if not capability_id:
            raise AnalyticsError("Stored File capability reference is missing")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM stored_file_capabilities WHERE id=?",
                (capability_id,),
            ).fetchone()
        if not row:
            raise AnalyticsError("Stored File capability does not exist")
        return dict(row)

    def _resolve_capability_file(self, capability: dict[str, Any]) -> dict[str, Any]:
        if not _capability_active(capability):
            raise AnalyticsError("Stored File capability is inactive")
        try:
            info = self._file_store_v972().get_info(str(capability.get("file_id") or ""))
        except FileStoreError as exc:
            raise AnalyticsError("Stored File capability target is unavailable") from exc
        if not _same_incarnation(capability, info):
            raise AnalyticsError("Stored File capability target no longer matches")
        return info

    def _insert_stored_file_link(
        self,
        *,
        delivery: dict[str, Any],
        file_info: dict[str, Any],
        position: int,
        anchor_text: str,
    ) -> dict[str, Any]:
        capability_id = str(file_info.get(_INTERNAL_CAPABILITY_KEY) or "").strip()
        if capability_id:
            capability = self.get_stored_file_capability(capability_id)
            if not _capability_active(capability) or (
                str(capability.get("file_id") or "") != str(file_info.get("id") or "")
                or not _same_incarnation(capability, file_info)
            ):
                raise StoredFileMailError("stored_file_unavailable")
        else:
            capability, _ = self.create_stored_file_capability(file_info, public_token=False)
            capability_id = str(capability["id"])

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
                SET stored_file_sha256=?, stored_file_created_at=?, stored_file_capability_id=?
                WHERE id=?
                """,
                (
                    str(file_info.get("sha256") or ""),
                    str(file_info.get("created_at") or ""),
                    capability_id,
                    str(record["occurrence_id"]),
                ),
            )
        record.update(
            {
                "stored_file_sha256": str(file_info.get("sha256") or ""),
                "stored_file_created_at": str(file_info.get("created_at") or ""),
                "stored_file_capability_id": capability_id,
            }
        )
        return record

    @staticmethod
    def _safe_link_metadata(record: dict[str, Any], public_url: str) -> dict[str, Any]:
        safe = StoredFileLinkTrackingStore._safe_link_metadata(record, public_url)
        for key in (
            "stored_file_capability_id",
            "stored_file_sha256",
            "stored_file_created_at",
        ):
            safe.pop(key, None)
        return safe

    @staticmethod
    def _tracked_path_token(url: str) -> tuple[str, Any | None]:
        try:
            parts = urlsplit(unescape(str(url or "").strip()))
        except ValueError:
            return "", None
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            return "", parts
        if not (parts.path or "").startswith("/t/c/"):
            return "", parts
        token = (parts.path or "")[len("/t/c/") :]
        if not token or "/" in token or parts.query or parts.fragment:
            return "", parts
        return token, parts

    @staticmethod
    def _same_public_origin(parts: Any | None, public_base: Any | None) -> bool:
        return bool(
            parts is not None
            and public_base is not None
            and parts.scheme.lower() == public_base.scheme.lower()
            and parts.netloc.lower() == public_base.netloc.lower()
        )

    def _resolve_prior_tracking_url(self, url: str, public_base: Any):
        """Canonical local detracking with exact Stored File anti-resurrection checks."""
        current = unescape(str(url or "")).strip()
        changed = False
        seen_tokens: set[str] = set()

        for _ in range(_MAX_TRACKING_CHAIN_DEPTH):
            try:
                direct_token = _public_stored_file_token_from_url(current)
            except FileHandoffError as exc:
                raise AnalyticsError("Invalid Postmaster stored-file capability") from exc
            if direct_token is not None:
                capability = self.get_public_capability_by_token(direct_token)
                info = self._resolve_capability_file(capability)
                return f"postmaster-file:{info['id']}", True

            token, parts = self._tracked_path_token(current)
            if not token:
                return current, changed
            if token in seen_tokens:
                raise AnalyticsError("Cyclic Postmaster tracking-link chain")
            seen_tokens.add(token)
            try:
                record = self.get_by_token(token)
            except AnalyticsError:
                if self._same_public_origin(parts, public_base):
                    raise AnalyticsError("Unknown Postmaster tracking-link token")
                return current, changed

            target_type = str(record.get("target_type") or "url")
            if target_type == "stored_file":
                capability_id = str(record.get("stored_file_capability_id") or "")
                if capability_id:
                    capability = self.get_stored_file_capability(capability_id)
                    info = self._resolve_capability_file(capability)
                    return f"postmaster-file:{info['id']}", True
                file_id = str(record.get("stored_file_id") or "")
                if not file_id:
                    raise AnalyticsError("Stored Postmaster tracking-link target is invalid")
                try:
                    info = self._file_store_v972().get_info(file_id)
                except FileStoreError as exc:
                    raise AnalyticsError("Stored Postmaster tracking-link target is unavailable") from exc
                if not _legacy_incarnation_matches(record, info):
                    raise AnalyticsError("Stored Postmaster tracking-link target no longer matches")
                return f"postmaster-file:{file_id}", True

            original = str(record.get("original_url") or "").strip()
            if not eligible_web_url(original):
                raise AnalyticsError("Stored Postmaster tracking-link destination is invalid")
            current = original
            changed = True

        token, _ = self._tracked_path_token(current)
        if token:
            raise AnalyticsError("Postmaster tracking-link chain is too deep")
        return current, changed

    def instrument_html_with_shares(
        self,
        *,
        body_html: str,
        delivery: dict[str, Any],
        track_web_links: bool = True,
        stored_file_resolver: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[tuple[str, str]]]:
        html = body_html or ""
        anchors = collect_anchors(html)
        if not anchors:
            return html, [], []
        public_base = _safe_base_url()
        replacements: list[tuple[str, str]] = []
        tracked: list[dict[str, Any]] = []
        share_urls: list[tuple[str, str]] = []

        for anchor_index, anchor in enumerate(anchors):
            href = str(anchor.get("href") or "")
            anchor_text = str(anchor.get("anchor_text") or "")[:500]
            stored_file_id = _stored_file_id_from_href(href)
            record: dict[str, Any] | None = None

            if stored_file_id is not None:
                if stored_file_resolver is None:
                    raise StoredFileMailError("stored_file_resolver_unavailable")
                file_info = stored_file_resolver(stored_file_id)
                record = self._insert_stored_file_link(
                    delivery=delivery,
                    file_info=file_info,
                    position=anchor_index,
                    anchor_text=anchor_text,
                )
            else:
                try:
                    capability_token = _public_stored_file_token_from_url(href)
                except FileHandoffError as exc:
                    raise StoredFileMailError("stored_file_unavailable") from exc

                if capability_token is not None:
                    if stored_file_resolver is None:
                        raise StoredFileMailError("stored_file_resolver_unavailable")
                    try:
                        capability = self.get_public_capability_by_token(capability_token)
                        info = self._resolve_capability_file(capability)
                    except AnalyticsError as exc:
                        raise StoredFileMailError("stored_file_unavailable") from exc
                    authorized = stored_file_resolver(str(info["id"]))
                    if (
                        str(authorized.get("sha256") or "") != str(info.get("sha256") or "")
                        or str(authorized.get("created_at") or "")
                        != str(info.get("created_at") or "")
                    ):
                        raise StoredFileMailError("stored_file_unavailable")
                    file_info = dict(authorized)
                    file_info[_INTERNAL_CAPABILITY_KEY] = str(capability["id"])
                    stored_file_id = str(info["id"])
                    record = self._insert_stored_file_link(
                        delivery=delivery,
                        file_info=file_info,
                        position=anchor_index,
                        anchor_text=anchor_text,
                    )
                elif (
                    track_web_links
                    and eligible_web_url(href)
                    and not already_tracked_url(href, public_base)
                ):
                    record = self._insert_link(
                        delivery=delivery,
                        original_url=href,
                        position=anchor_index,
                        anchor_text=anchor_text,
                    )
                    record["target_type"] = "url"

            if record is None:
                continue
            tracked_url = f"{public_base}/t/c/{record['tracking_token']}"
            replacements.append(
                (str(anchor["raw_tag"]), replace_href(str(anchor["raw_tag"]), tracked_url))
            )
            tracked.append(self._safe_link_metadata(record, tracked_url))
            if stored_file_id is not None:
                share_urls.append((stored_file_id, tracked_url))

        return rewrite_anchor_tags(html, replacements), tracked, share_urls

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
                        capability = self.get_public_capability_by_token(token)
                    except AnalyticsError:
                        capability = None
                    file_id = str(capability.get("file_id") or "") if capability else None
            if file_id is None or not queues.get(file_id):
                continue
            replacements.append(
                (
                    str(anchor["raw_tag"]),
                    replace_href(str(anchor["raw_tag"]), queues[file_id].pop(0)),
                )
            )
        return rewrite_anchor_tags(body_html or "", replacements)


class PostmasterV972MailClient(PostmasterV960NewsletterMailClient):
    """Final composed mail client with instance-scoped v9.7.2 tracking normalization."""

    def _normalize_outbound_html(self, body_html: str | None) -> str | None:
        if body_html is None:
            return None
        try:
            return self._link_store().normalize_postmaster_html(body_html)
        except AnalyticsError as exc:
            raise MailBridgeError(
                f"Outbound HTML contains an unresolved Postmaster tracking artifact: {exc}"
            ) from exc

    def _normalized_entry_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if kwargs.get("body_html") is None:
            return kwargs
        normalized = dict(kwargs)
        normalized["body_html"] = self._normalize_outbound_html(kwargs.get("body_html"))
        return normalized

    def send_email(self, **kwargs: Any) -> dict[str, Any]:
        return super().send_email(**self._normalized_entry_kwargs(dict(kwargs)))

    def reply_email(self, **kwargs: Any) -> dict[str, Any]:
        return super().reply_email(**self._normalized_entry_kwargs(dict(kwargs)))

    def follow_up_email(self, **kwargs: Any) -> dict[str, Any]:
        return super().follow_up_email(**self._normalized_entry_kwargs(dict(kwargs)))


def build_public_stored_file_url(
    store: FileStore,
    capability_store: StoredFileLinkTrackingStoreV972,
    file_id: str,
) -> str:
    info = store.get_info(file_id)
    _, raw_token = capability_store.create_stored_file_capability(info, public_token=True)
    if not raw_token:
        raise AnalyticsError("Stored File public capability token was not created")
    return f"{_public_base_url(required=True)}/t/c/{raw_token}"


def stored_file_resource_result_v972(
    store: FileStore,
    capability_store: StoredFileLinkTrackingStoreV972,
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
        uri = (
            build_public_stored_file_url(store, capability_store, file_id)
            if mode == "http"
            else f"postmaster://files/{quote(file_id, safe='')}"
        )
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
    except (FileStoreError, AnalyticsError) as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=str(exc))],
            is_error=True,
        )


async def public_tracking_target_v972(
    request: Request,
    *,
    tracking_store: StoredFileLinkTrackingStoreV972,
    file_store: FileStore,
    logger: Any,
) -> Response:
    token = str(request.path_params.get("token", "")).strip()

    if token.startswith(_PUBLIC_FILE_TOKEN_PREFIX):
        try:
            capability = tracking_store.get_public_capability_by_token(token)
            info = tracking_store._resolve_capability_file(capability)
            return _terminal_file_response(
                request,
                file_store,
                str(info["id"]),
                expected_sha256=str(capability.get("file_sha256") or ""),
                expected_created_at=str(capability.get("file_created_at") or ""),
            )
        except Exception:
            return _public_not_found()

    try:
        link = tracking_store.get_by_token(token)
        target_type = str(link.get("target_type") or "url")
        capability: dict[str, Any] | None = None
        legacy_stored_file_id = ""
        signed_local: tuple[str, str, str] | None = None
        destination = ""

        if target_type == "stored_file":
            capability_id = str(link.get("stored_file_capability_id") or "")
            if capability_id:
                capability = tracking_store.get_stored_file_capability(capability_id)
            else:
                legacy_stored_file_id = str(link.get("stored_file_id") or "")
                if not legacy_stored_file_id:
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
            _validate_signed_request(file_store, stored_file_id, expires_raw, signature)
            return _terminal_file_response(request, file_store, stored_file_id)

        if capability is not None:
            info = tracking_store._resolve_capability_file(capability)
            return _terminal_file_response(
                request,
                file_store,
                str(info["id"]),
                expected_sha256=str(capability.get("file_sha256") or ""),
                expected_created_at=str(capability.get("file_created_at") or ""),
                download_filename=str(link.get("download_filename") or ""),
                download_media_type=str(link.get("download_media_type") or ""),
            )

        current = file_store.get_info(legacy_stored_file_id)
        if not _legacy_incarnation_matches(link, current):
            return _public_not_found()
        return _terminal_file_response(
            request,
            file_store,
            legacy_stored_file_id,
            expected_sha256=str(link.get("stored_file_sha256") or ""),
            expected_created_at=str(link.get("stored_file_created_at") or ""),
            download_filename=str(link.get("download_filename") or ""),
            download_media_type=str(link.get("download_media_type") or ""),
        )
    except Exception:
        return _public_not_found()


def bind_stored_file_link_store_v972(base: Any) -> Callable[[], StoredFileLinkTrackingStoreV972]:
    """Return a side-effect-free lazy per-runtime store factory."""

    @lru_cache(maxsize=1)
    def _store() -> StoredFileLinkTrackingStoreV972:
        return StoredFileLinkTrackingStoreV972(
            base.analytics_store(),
            file_store_provider=base.file_store,
        )

    return _store


def install_stored_file_public_v972(
    core: Any,
    link_store_factory: Callable[[], StoredFileLinkTrackingStoreV972],
) -> None:
    """Install DB-backed ResourceLink handoff without eager DB init or global class mutation."""

    def _resource_result(store: FileStore, file_id: str, transport: str = "auto") -> CallToolResult:
        return stored_file_resource_result_v972(
            store,
            link_store_factory(),
            file_id,
            transport,
        )

    core.stored_file_resource_result = _resource_result
