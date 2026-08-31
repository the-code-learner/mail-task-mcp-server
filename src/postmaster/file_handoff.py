from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote, urlsplit

from mcp.types import CallToolResult, ResourceLink
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse

from .file_store import FileStore, FileStoreError


# Legacy expiring-link bounds are retained so URLs generated before the durable
# capability transition keep their original verification semantics. New links use
# DURABLE_DOWNLOAD_EXPIRY and remain valid for the lifetime of the exact stored-file
# record instead of depending on this TTL.
DEFAULT_DOWNLOAD_TTL_SECONDS = 900
MAX_DOWNLOAD_TTL_SECONDS = 86400
MIN_DOWNLOAD_TTL_SECONDS = 30
DURABLE_DOWNLOAD_EXPIRY = 0
DOWNLOAD_SECRET_PATH = Path("/data/file-store-download.secret")
STREAM_CHUNK_BYTES = 64 * 1024


class FileHandoffError(FileStoreError):
    pass


def _public_base_url(*, required: bool = False) -> str | None:
    """Resolve the external HTTPS base without coupling file transfer to mail callbacks.

    A dedicated FILE_STORE_PUBLIC_BASE_URL may override the normal MCP host. Otherwise
    PUBLIC_MCP_HOST is reused because /files is served by the same public MCP service.
    This keeps existing single-YAML deployments usable without adding mandatory envs.
    """
    raw = os.getenv("FILE_STORE_PUBLIC_BASE_URL", "").strip()
    if not raw:
        public_host = os.getenv("PUBLIC_MCP_HOST", "").strip()
        if public_host:
            raw = f"https://{public_host}"
    if not raw:
        if required:
            raise FileHandoffError(
                "HTTP file handoff is not configured; set FILE_STORE_PUBLIC_BASE_URL or PUBLIC_MCP_HOST to an externally reachable HTTPS host"
            )
        return None
    parsed = urlsplit(raw)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise FileHandoffError(
            "file handoff public base must be an absolute HTTPS URL without credentials, query parameters or fragments"
        )
    return raw.rstrip("/")


def download_ttl_seconds() -> int:
    """Return the configured lifetime for legacy expiring download capabilities."""
    raw = os.getenv("FILE_STORE_DOWNLOAD_URL_TTL_SECONDS", str(DEFAULT_DOWNLOAD_TTL_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise FileHandoffError("FILE_STORE_DOWNLOAD_URL_TTL_SECONDS must be an integer") from exc
    return max(MIN_DOWNLOAD_TTL_SECONDS, min(MAX_DOWNLOAD_TTL_SECONDS, value))


def _download_secret() -> bytes:
    configured = os.getenv("FILE_STORE_DOWNLOAD_SECRET", "").strip()
    if configured:
        if len(configured.encode("utf-8")) < 32:
            raise FileHandoffError("FILE_STORE_DOWNLOAD_SECRET must contain at least 32 bytes")
        return configured.encode("utf-8")

    path = DOWNLOAD_SECRET_PATH
    try:
        existing = path.read_bytes().strip()
    except FileNotFoundError:
        existing = b""
    except OSError as exc:
        raise FileHandoffError(f"could not read persistent file-download secret: {exc}") from exc
    if existing:
        if len(existing) < 32:
            raise FileHandoffError("persistent file-download secret is unexpectedly short")
        return existing

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(48).encode("ascii")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(generated + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return generated
    except FileExistsError:
        try:
            existing = path.read_bytes().strip()
        except OSError as exc:
            raise FileHandoffError(f"could not read persistent file-download secret: {exc}") from exc
        if len(existing) < 32:
            raise FileHandoffError("persistent file-download secret is unexpectedly short")
        return existing
    except OSError as exc:
        raise FileHandoffError(f"could not create persistent file-download secret: {exc}") from exc


def _download_signature(file_id: str, expires: int) -> str:
    """Legacy v9.3 expiring capability signature retained for backwards compatibility."""
    canonical = f"{file_id}\n{int(expires)}".encode("utf-8")
    return hmac.new(_download_secret(), canonical, hashlib.sha256).hexdigest()


def _durable_download_signature(info: dict) -> str:
    """Bind a durable capability to one exact immutable FileStore record incarnation.

    The token binds the opaque file id plus content digest and immutable creation time.
    Deleting and later re-creating a record with the same id therefore does not resurrect
    an old capability, even when the replacement happens to contain identical bytes.
    """
    canonical = "\n".join(
        (
            "postmaster-file-capability-v2",
            str(info.get("id") or ""),
            str(info.get("sha256") or ""),
            str(info.get("created_at") or ""),
        )
    ).encode("utf-8")
    return hmac.new(_download_secret(), canonical, hashlib.sha256).hexdigest()


def build_signed_file_url(
    store: FileStore,
    file_id: str,
    *,
    now: int | None = None,
    expires: int | None = None,
) -> str:
    """Build a public file capability.

    New calls without an explicit ``expires`` value produce a durable capability using
    ``expires=0`` as the versioned no-expiry sentinel. It remains valid while the exact
    stored-file record exists. Supplying ``expires`` intentionally produces the legacy
    short-lived capability so older integrations and tests remain interoperable.
    """
    info = store.get_info(file_id)
    base = _public_base_url(required=True)

    if expires is None:
        signature = _durable_download_signature(info)
        return (
            f"{base}/files/{quote(file_id, safe='')}?"
            f"expires={DURABLE_DOWNLOAD_EXPIRY}&sig={signature}"
        )

    current = int(time.time()) if now is None else int(now)
    expiry = int(expires)
    if expiry <= current:
        raise FileHandoffError("signed file URL expiry must be in the future")
    if expiry - current > MAX_DOWNLOAD_TTL_SECONDS:
        raise FileHandoffError(f"signed file URL TTL cannot exceed {MAX_DOWNLOAD_TTL_SECONDS} seconds")
    signature = _download_signature(file_id, expiry)
    return f"{base}/files/{quote(file_id, safe='')}?expires={expiry}&sig={signature}"


def _validate_signed_request(
    store: FileStore,
    file_id: str,
    expires_raw: str,
    signature: str,
    *,
    now: int | None = None,
) -> None:
    if not expires_raw or not signature:
        raise FileHandoffError("missing signed file URL expiry or signature")
    try:
        expires = int(expires_raw)
    except ValueError as exc:
        raise FileHandoffError("invalid signed file URL expiry") from exc

    if expires == DURABLE_DOWNLOAD_EXPIRY:
        info = store.get_info(file_id)
        expected = _durable_download_signature(info)
        if not hmac.compare_digest(expected, signature):
            raise FileHandoffError("invalid durable file URL signature")
        return

    current = int(time.time()) if now is None else int(now)
    if expires <= current:
        raise FileHandoffError("signed file URL has expired")
    if expires - current > MAX_DOWNLOAD_TTL_SECONDS:
        raise FileHandoffError("signed file URL exceeds the maximum allowed TTL")
    expected = _download_signature(file_id, expires)
    if not hmac.compare_digest(expected, signature):
        raise FileHandoffError("invalid signed file URL signature")


def stored_file_resource_result(store: FileStore, file_id: str, transport: str = "auto") -> CallToolResult:
    mode = (transport or "auto").strip().lower()
    if mode not in {"auto", "http", "mcp"}:
        raise FileHandoffError("transport must be one of: auto, http, mcp")

    info = store.get_info(file_id)
    if mode == "auto":
        mode = "http" if _public_base_url(required=False) else "mcp"

    if mode == "http":
        uri = build_signed_file_url(store, file_id)
    else:
        uri = f"postmaster://files/{quote(file_id, safe='')}"

    description = str(info.get("description") or "").strip() or "Postmaster stored file"
    link = ResourceLink(
        type="resource_link",
        uri=uri,
        name=str(info.get("filename") or file_id),
        description=description,
        mime_type=str(info.get("media_type") or "application/octet-stream"),
        size=int(info.get("size_bytes") or 0),
    )
    return CallToolResult(content=[link])


def read_stored_file_resource(store: FileStore, file_id: str) -> bytes:
    """Return verified original bytes; MCP SDK performs protocol Base64 encoding for BlobResourceContents."""
    return store.raw_bytes(file_id)[1]


def _parse_range(value: str, size: int) -> tuple[int, int]:
    if size <= 0 or not value.startswith("bytes="):
        raise FileHandoffError("invalid byte range")
    spec = value[6:].strip()
    if not spec or "," in spec or "-" not in spec:
        raise FileHandoffError("only one byte range is supported")
    first, last = spec.split("-", 1)
    try:
        if first == "":
            suffix = int(last)
            if suffix <= 0:
                raise ValueError
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(first)
            if start < 0 or start >= size:
                raise ValueError
            if last == "":
                end = size - 1
            else:
                end = int(last)
                if end < start:
                    raise ValueError
                end = min(end, size - 1)
    except ValueError as exc:
        raise FileHandoffError("invalid byte range") from exc
    return start, end


def _stream_blob(path: Path, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                raise FileHandoffError("stored blob ended before the expected metadata size")
            remaining -= len(chunk)
            yield chunk


def _download_headers(info: dict, size: int) -> dict[str, str]:
    filename = quote(str(info.get("filename") or "download.bin"), safe="")
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store, max-age=0",
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
    }


def stored_file_http_response(request: Request, store: FileStore, *, require_signature: bool = True) -> Response:
    file_id = str(request.path_params.get("file_id", "")).strip()
    if require_signature:
        try:
            _validate_signed_request(
                store,
                file_id,
                str(request.query_params.get("expires") or ""),
                str(request.query_params.get("sig") or ""),
            )
        except FileStoreError as exc:
            # FileHandoffError is a FileStoreError subclass. A deleted durable record
            # returns 404; malformed/forged capabilities remain a generic 403.
            if str(exc) == "stored file not found":
                return PlainTextResponse(
                    "stored file not found",
                    status_code=404,
                    headers={"Cache-Control": "private, no-store, max-age=0", "X-Content-Type-Options": "nosniff"},
                )
            return PlainTextResponse(
                "Forbidden",
                status_code=403,
                headers={"Cache-Control": "private, no-store, max-age=0", "X-Content-Type-Options": "nosniff"},
            )

    try:
        info, path = store.resolve_blob(file_id)
    except FileStoreError as exc:
        return PlainTextResponse(
            str(exc),
            status_code=404,
            headers={"Cache-Control": "private, no-store, max-age=0", "X-Content-Type-Options": "nosniff"},
        )

    size = int(info.get("size_bytes") or 0)
    media_type = str(info.get("media_type") or "application/octet-stream")
    headers = _download_headers(info, size)
    start = 0
    end = size - 1
    status_code = 200
    requested_range = (request.headers.get("range") or "").strip()
    if requested_range:
        try:
            start, end = _parse_range(requested_range, size)
        except FileHandoffError:
            headers["Content-Range"] = f"bytes */{size}"
            headers["Content-Length"] = "0"
            return Response(status_code=416, headers=headers)
        status_code = 206
        length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(length)
    else:
        length = size

    if request.method.upper() == "HEAD":
        return Response(status_code=status_code, media_type=media_type, headers=headers)

    return StreamingResponse(
        _stream_blob(path, start, length),
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )
