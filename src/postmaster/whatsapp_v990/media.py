from __future__ import annotations

"""Clean-room WhatsApp media upload/download and minimal media protobuf helpers."""

import base64
from dataclasses import dataclass
import json
import time
from typing import Any
from urllib.parse import quote

import httpx

from .binary import BinaryNode
from .crypto import decrypt_media, encrypt_media
from .proto import ProtoError, decode_fields, field_bytes, field_message, field_varint


class WhatsAppMediaError(RuntimeError):
    pass


MEDIA_PATH_MAP = {
    "image": "/mms/image",
    "video": "/mms/video",
    "audio": "/mms/audio",
    "document": "/mms/document",
    "sticker": "/mms/image",
}
OUTER_MESSAGE_FIELD = {"image": 3, "document": 7, "audio": 8, "video": 9, "sticker": 26}


@dataclass(frozen=True, slots=True)
class MediaHost:
    hostname: str
    max_content_length_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class MediaConn:
    auth: str
    ttl: int
    hosts: tuple[MediaHost, ...]


@dataclass(frozen=True, slots=True)
class MediaUpload:
    media_type: str
    url: str | None
    direct_path: str | None
    media_key: bytes
    file_sha256: bytes
    file_enc_sha256: bytes
    file_length: int
    mimetype: str
    filename: str | None = None
    caption: str = ""
    media_key_timestamp: int = 0


@dataclass(frozen=True, slots=True)
class MediaDescriptor:
    media_type: str
    url: str | None
    direct_path: str | None
    media_key: bytes
    file_sha256: bytes
    file_enc_sha256: bytes
    file_length: int
    mimetype: str
    filename: str | None = None
    caption: str = ""


def infer_media_type(*, mimetype: str, filename: str = "") -> str:
    mime = str(mimetype or "").split(";", 1)[0].strip().lower()
    name = str(filename or "").lower()
    if mime == "image/webp" or name.endswith(".webp"):
        return "sticker"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


def build_media_conn_query() -> BinaryNode:
    return BinaryNode(
        "iq",
        {"type": "set", "xmlns": "w:m", "to": "s.whatsapp.net"},
        [BinaryNode("media_conn")],
    )


def parse_media_conn(node: BinaryNode) -> MediaConn:
    media = node.child("media_conn") if node.tag == "iq" else None
    if media is None:
        raise WhatsAppMediaError("WhatsApp media_conn response is missing media_conn")
    auth = str(media.attrs.get("auth") or "").strip()
    if not auth:
        raise WhatsAppMediaError("WhatsApp media_conn response has no auth token")
    try:
        ttl = int(media.attrs.get("ttl") or 0)
    except ValueError as exc:
        raise WhatsAppMediaError("WhatsApp media_conn ttl is invalid") from exc
    hosts: list[MediaHost] = []
    for child in media.children("host"):
        hostname = str(child.attrs.get("hostname") or "").strip()
        if not hostname or "/" in hostname or ":" in hostname:
            continue
        raw_limit = child.attrs.get("maxContentLengthBytes")
        try:
            limit = int(raw_limit) if raw_limit not in (None, "") else None
        except (TypeError, ValueError):
            limit = None
        hosts.append(MediaHost(hostname, limit))
    if not hosts:
        raise WhatsAppMediaError("WhatsApp media_conn response has no usable upload hosts")
    return MediaConn(auth=auth, ttl=max(0, ttl), hosts=tuple(hosts))


def upload_token(file_enc_sha256: bytes) -> str:
    # URL-safe unpadded Base64, matching the current WhatsApp Web upload token.
    value = base64.b64encode(bytes(file_enc_sha256)).decode("ascii")
    value = value.replace("+", "-").replace("/", "_").rstrip("=")
    return quote(value, safe="-_")


async def upload_media_bytes(
    plaintext: bytes,
    *,
    media_type: str,
    mimetype: str,
    filename: str | None,
    caption: str = "",
    media_conn: MediaConn,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[MediaUpload, bytes]:
    kind = str(media_type).lower()
    if kind not in MEDIA_PATH_MAP:
        raise WhatsAppMediaError(f"Unsupported WhatsApp media type: {media_type}")
    data = bytes(plaintext)
    encrypted = encrypt_media(data, kind)
    token = upload_token(encrypted["file_enc_sha256"])
    own_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=True,
        headers={"Origin": "https://web.whatsapp.com"},
    )
    last_error: Exception | None = None
    try:
        for host in media_conn.hosts:
            if host.max_content_length_bytes is not None and len(data) > host.max_content_length_bytes:
                last_error = WhatsAppMediaError(
                    f"Stored File exceeds WhatsApp upload host limit ({host.max_content_length_bytes} bytes)"
                )
                continue
            url = (
                f"https://{host.hostname}{MEDIA_PATH_MAP[kind]}/{token}"
                f"?auth={quote(media_conn.auth, safe='')}&token={token}"
            )
            try:
                response = await http.post(
                    url,
                    content=encrypted["encrypted"],
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Origin": "https://web.whatsapp.com",
                    },
                )
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict):
                    raise WhatsAppMediaError("WhatsApp media upload returned non-object JSON")
                media_url = str(value.get("url") or "").strip() or None
                direct_path = str(value.get("direct_path") or "").strip() or None
                if not media_url and not direct_path:
                    raise WhatsAppMediaError("WhatsApp media upload returned no URL/direct path")
                return (
                    MediaUpload(
                        media_type=kind,
                        url=media_url,
                        direct_path=direct_path,
                        media_key=encrypted["media_key"],
                        file_sha256=encrypted["file_sha256"],
                        file_enc_sha256=encrypted["file_enc_sha256"],
                        file_length=len(data),
                        mimetype=str(mimetype or "application/octet-stream"),
                        filename=str(filename) if filename else None,
                        caption=str(caption or ""),
                        media_key_timestamp=int(time.time()),
                    ),
                    encrypted["encrypted"],
                )
            except Exception as exc:
                last_error = exc
        raise WhatsAppMediaError(f"WhatsApp media upload failed on all hosts: {last_error}")
    finally:
        if own_client:
            await http.aclose()


def encode_media_message(upload: MediaUpload) -> bytes:
    kind = upload.media_type
    if kind not in OUTER_MESSAGE_FIELD:
        raise WhatsAppMediaError(f"Unsupported media protobuf type: {kind}")
    common_url = [field_bytes(1, upload.url)] if upload.url else []

    if kind == "image":
        inner = common_url + [
            field_bytes(2, upload.mimetype),
            *([field_bytes(3, upload.caption)] if upload.caption else []),
            field_bytes(4, upload.file_sha256),
            field_varint(5, upload.file_length),
            field_bytes(8, upload.media_key),
            field_bytes(9, upload.file_enc_sha256),
            *([field_bytes(11, upload.direct_path)] if upload.direct_path else []),
            field_varint(12, upload.media_key_timestamp),
        ]
    elif kind == "document":
        inner = common_url + [
            field_bytes(2, upload.mimetype),
            field_bytes(4, upload.file_sha256),
            field_varint(5, upload.file_length),
            field_bytes(7, upload.media_key),
            *([field_bytes(8, upload.filename)] if upload.filename else []),
            field_bytes(9, upload.file_enc_sha256),
            *([field_bytes(10, upload.direct_path)] if upload.direct_path else []),
            field_varint(11, upload.media_key_timestamp),
            *([field_bytes(20, upload.caption)] if upload.caption else []),
        ]
    elif kind == "audio":
        inner = common_url + [
            field_bytes(2, upload.mimetype),
            field_bytes(3, upload.file_sha256),
            field_varint(4, upload.file_length),
            field_bytes(7, upload.media_key),
            field_bytes(8, upload.file_enc_sha256),
            *([field_bytes(9, upload.direct_path)] if upload.direct_path else []),
            field_varint(10, upload.media_key_timestamp),
        ]
    elif kind == "video":
        inner = common_url + [
            field_bytes(2, upload.mimetype),
            field_bytes(3, upload.file_sha256),
            field_varint(4, upload.file_length),
            field_bytes(6, upload.media_key),
            *([field_bytes(7, upload.caption)] if upload.caption else []),
            field_bytes(11, upload.file_enc_sha256),
            *([field_bytes(13, upload.direct_path)] if upload.direct_path else []),
            field_varint(14, upload.media_key_timestamp),
        ]
    else:  # sticker
        inner = common_url + [
            field_bytes(2, upload.file_sha256),
            field_bytes(3, upload.file_enc_sha256),
            field_bytes(4, upload.media_key),
            field_bytes(5, upload.mimetype),
            *([field_bytes(8, upload.direct_path)] if upload.direct_path else []),
            field_varint(9, upload.file_length),
            field_varint(10, upload.media_key_timestamp),
        ]
    return field_message(OUTER_MESSAGE_FIELD[kind], inner)


def _field_map(raw: bytes) -> dict[int, list[int | bytes]]:
    try:
        fields = decode_fields(raw)
    except ProtoError as exc:
        raise WhatsAppMediaError("Invalid WhatsApp media protobuf") from exc
    out: dict[int, list[int | bytes]] = {}
    for field in fields:
        out.setdefault(field.number, []).append(field.value)
    return out


def _bytes(values: dict[int, list[int | bytes]], number: int) -> bytes | None:
    found = [value for value in values.get(number, []) if isinstance(value, bytes)]
    return bytes(found[-1]) if found else None


def _integer(values: dict[int, list[int | bytes]], number: int) -> int | None:
    found = [value for value in values.get(number, []) if isinstance(value, int)]
    return int(found[-1]) if found else None


def _text(values: dict[int, list[int | bytes]], number: int) -> str | None:
    raw = _bytes(values, number)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WhatsAppMediaError("WhatsApp media protobuf string is not UTF-8") from exc


def decode_media_descriptor(message: bytes) -> MediaDescriptor | None:
    outer = _field_map(bytes(message))
    reverse = {value: key for key, value in OUTER_MESSAGE_FIELD.items()}
    selected: tuple[str, bytes] | None = None
    for field_number, kind in reverse.items():
        nested = _bytes(outer, field_number)
        if nested is not None:
            selected = (kind, nested)
            break
    if selected is None:
        return None
    kind, nested = selected
    values = _field_map(nested)
    if kind == "image":
        sha, length, key, enc, direct, caption, filename = 4, 5, 8, 9, 11, 3, None
    elif kind == "document":
        sha, length, key, enc, direct, caption, filename = 4, 5, 7, 9, 10, 20, 8
    elif kind == "audio":
        sha, length, key, enc, direct, caption, filename = 3, 4, 7, 8, 9, None, None
    elif kind == "video":
        sha, length, key, enc, direct, caption, filename = 3, 4, 6, 11, 13, 7, None
    else:
        sha, length, key, enc, direct, caption, filename = 2, 9, 4, 3, 8, None, None
    media_key = _bytes(values, key)
    file_sha = _bytes(values, sha)
    file_enc_sha = _bytes(values, enc)
    file_length = _integer(values, length)
    if media_key is None or file_sha is None or file_enc_sha is None or file_length is None:
        raise WhatsAppMediaError("WhatsApp media message is missing cryptographic metadata")
    return MediaDescriptor(
        media_type=kind,
        url=_text(values, 1),
        direct_path=_text(values, direct),
        media_key=media_key,
        file_sha256=file_sha,
        file_enc_sha256=file_enc_sha,
        file_length=file_length,
        mimetype=_text(values, 2 if kind != "sticker" else 5) or "application/octet-stream",
        filename=_text(values, filename) if filename else None,
        caption=_text(values, caption) if caption else "",
    )


async def download_media(
    descriptor: MediaDescriptor,
    *,
    client: httpx.AsyncClient | None = None,
    default_host: str = "mmg.whatsapp.net",
    timeout_seconds: float = 30.0,
) -> bytes:
    if descriptor.direct_path:
        url = f"https://{default_host}{descriptor.direct_path}"
    elif descriptor.url:
        url = descriptor.url
    else:
        raise WhatsAppMediaError("WhatsApp media message has no URL/direct path")
    if not url.startswith("https://"):
        raise WhatsAppMediaError("WhatsApp media download URL must use HTTPS")
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds), follow_redirects=True)
    try:
        response = await http.get(url, headers={"Origin": "https://web.whatsapp.com"})
        response.raise_for_status()
        encrypted = bytes(response.content)
        if __import__("hashlib").sha256(encrypted).digest() != descriptor.file_enc_sha256:
            raise WhatsAppMediaError("Downloaded WhatsApp media encrypted SHA-256 mismatch")
        plaintext = decrypt_media(
            encrypted,
            descriptor.media_type,
            descriptor.media_key,
            expected_file_sha256=descriptor.file_sha256,
        )
        if len(plaintext) != descriptor.file_length:
            raise WhatsAppMediaError("Downloaded WhatsApp media length mismatch")
        return plaintext
    finally:
        if own_client:
            await http.aclose()


__all__ = [
    "WhatsAppMediaError", "MediaHost", "MediaConn", "MediaUpload", "MediaDescriptor",
    "infer_media_type", "build_media_conn_query", "parse_media_conn", "upload_token",
    "upload_media_bytes", "encode_media_message", "decode_media_descriptor", "download_media",
]
