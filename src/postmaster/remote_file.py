from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from typing import NotRequired, TypedDict
from urllib.parse import urljoin, urlsplit

import httpx


class RemoteFileError(RuntimeError):
    pass


class OpenAIFile(TypedDict):
    """ChatGPT file-param object. download_url and file_id are always present."""

    download_url: str
    file_id: str
    mime_type: NotRequired[str]
    file_name: NotRequired[str]


@dataclass(frozen=True)
class DownloadedFile:
    data: bytes
    response_media_type: str | None


def _positive_int_env(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RemoteFileError(f"{name} must be an integer") from exc
    return max(1, min(value, maximum))


def remote_timeout_seconds() -> int:
    return _positive_int_env("FILE_STORE_REMOTE_TIMEOUT_SECONDS", 30, 120)


def remote_max_redirects() -> int:
    return _positive_int_env("FILE_STORE_REMOTE_MAX_REDIRECTS", 3, 5)


def remote_max_batch_files() -> int:
    return _positive_int_env("FILE_STORE_REMOTE_MAX_BATCH_FILES", 20, 100)


def _resolved_addresses(hostname: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RemoteFileError(f"could not resolve remote file host: {hostname}") from exc
    addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    if not addresses:
        raise RemoteFileError(f"remote file host resolved to no addresses: {hostname}")
    return addresses


def validate_public_https_url(url: str) -> None:
    raw = str(url or "").strip()
    if not raw or len(raw) > 8192:
        raise RemoteFileError("remote file download_url is empty or too long")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https":
        raise RemoteFileError("remote file download_url must use HTTPS")
    if parsed.username or parsed.password:
        raise RemoteFileError("remote file download_url must not contain credentials")
    if not parsed.hostname:
        raise RemoteFileError("remote file download_url has no hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RemoteFileError("remote file download_url has an invalid port") from exc
    if port not in (None, 443):
        raise RemoteFileError("remote file download_url must use HTTPS port 443")
    addresses = _resolved_addresses(parsed.hostname)
    blocked = sorted(str(addr) for addr in addresses if not addr.is_global)
    if blocked:
        raise RemoteFileError(f"remote file host resolves to a non-public address: {', '.join(blocked)}")


def filename_for_openai_file(source: OpenAIFile) -> str:
    name = str(source.get("file_name") or "").strip()
    if name:
        return name
    file_id = str(source.get("file_id") or "").strip()
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", file_id).strip("._")[:96] or "chatgpt-file"
    return f"{safe_id}.bin"


def download_openai_file(
    source: OpenAIFile,
    *,
    max_bytes: int,
    client: httpx.Client | None = None,
) -> DownloadedFile:
    """Download one ChatGPT-authorized file while enforcing size, redirect and SSRF limits."""
    if not isinstance(source, dict):
        raise RemoteFileError("file must be a ChatGPT file object")
    url = str(source.get("download_url") or "").strip()
    file_id = str(source.get("file_id") or "").strip()
    if not file_id or len(file_id) > 512:
        raise RemoteFileError("file_id is required and must be at most 512 characters")
    max_bytes = max(1, int(max_bytes))
    redirects_left = remote_max_redirects()
    own_client = client is None
    if own_client:
        timeout = httpx.Timeout(float(remote_timeout_seconds()))
        client = httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False)
    assert client is not None
    try:
        while True:
            validate_public_https_url(url)
            with client.stream(
                "GET",
                url,
                headers={"User-Agent": "Postmaster-MCP/9.2", "Accept": "*/*"},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise RemoteFileError("remote file redirect has no Location header")
                    if redirects_left <= 0:
                        raise RemoteFileError("remote file exceeded redirect limit")
                    redirects_left -= 1
                    url = urljoin(url, location)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise RemoteFileError(f"remote file download failed with HTTP {response.status_code}")
                declared = response.headers.get("content-length")
                if declared:
                    try:
                        declared_size = int(declared)
                    except ValueError:
                        declared_size = -1
                    if declared_size > max_bytes:
                        raise RemoteFileError(f"remote file exceeds configured file-size limit ({max_bytes} bytes)")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise RemoteFileError(f"remote file exceeds configured file-size limit ({max_bytes} bytes)")
                    chunks.append(chunk)
                media_type = response.headers.get("content-type")
                return DownloadedFile(data=b"".join(chunks), response_media_type=media_type)
    except httpx.HTTPError as exc:
        raise RemoteFileError(f"remote file download failed: {type(exc).__name__}") from exc
    finally:
        if own_client:
            client.close()
