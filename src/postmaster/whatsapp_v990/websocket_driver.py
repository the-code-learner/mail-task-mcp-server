from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import os
import ssl
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse


DEFAULT_WA_WEBSOCKET_URL = "wss://web.whatsapp.com/ws/chat"
DEFAULT_WA_ORIGIN = "https://web.whatsapp.com"
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_HTTP_HEADERS = 64 * 1024


class WebSocketDriverError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WebSocketDriverConfig:
    url: str = DEFAULT_WA_WEBSOCKET_URL
    origin: str = DEFAULT_WA_ORIGIN
    open_timeout: float = 20.0
    close_timeout: float = 10.0
    max_size: int | None = None
    prefer_native: bool = False


class NativeWebSocket:
    """Small RFC6455 client sufficient for the WhatsApp binary transport.

    Client frames are always masked as required by RFC6455. TLS certificate verification is
    performed by asyncio.open_connection's default SSL context. The reader handles ping/pong,
    close and fragmented text/binary messages; extensions are deliberately not negotiated.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, max_size: int | None = None):
        self.reader = reader
        self.writer = writer
        self.max_size = max_size
        self.closed = False
        self._send_lock = asyncio.Lock()
        self._recv_lock = asyncio.Lock()

    async def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        if self.closed and opcode != 0x8:
            raise WebSocketDriverError("WebSocket is closed")
        raw = bytes(payload)
        if opcode >= 0x8 and len(raw) > 125:
            raise WebSocketDriverError("WebSocket control frame exceeds 125 bytes")
        mask = os.urandom(4)
        first = 0x80 | (opcode & 0x0F)
        length = len(raw)
        if length < 126:
            header = bytes((first, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((first, 0x80 | 126)) + length.to_bytes(2, "big")
        else:
            if length >= (1 << 63):
                raise WebSocketDriverError("WebSocket payload is too large")
            header = bytes((first, 0x80 | 127)) + length.to_bytes(8, "big")
        masked = bytes(value ^ mask[i & 3] for i, value in enumerate(raw))
        async with self._send_lock:
            self.writer.write(header + mask + masked)
            await self.writer.drain()

    async def send(self, data: bytes) -> None:
        await self._send_frame(0x2, bytes(data))

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        try:
            head = await self.reader.readexactly(2)
        except (asyncio.IncompleteReadError, ConnectionError) as exc:
            self.closed = True
            raise WebSocketDriverError("WebSocket connection ended while reading frame header") from exc
        fin = bool(head[0] & 0x80)
        rsv = head[0] & 0x70
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if rsv:
            raise WebSocketDriverError("Unexpected WebSocket RSV bits without negotiated extension")
        if masked:
            raise WebSocketDriverError("Server WebSocket frames must not be masked")
        if length == 126:
            length = int.from_bytes(await self.reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await self.reader.readexactly(8), "big")
            if length >= (1 << 63):
                raise WebSocketDriverError("Invalid WebSocket 64-bit payload length")
        if opcode >= 0x8 and (not fin or length > 125):
            raise WebSocketDriverError("Invalid fragmented/oversized WebSocket control frame")
        if self.max_size is not None and length > self.max_size:
            raise WebSocketDriverError("WebSocket frame exceeds configured max_size")
        payload = await self.reader.readexactly(length)
        return fin, opcode, payload

    async def recv(self) -> bytes | str:
        async with self._recv_lock:
            message_opcode: int | None = None
            chunks: list[bytes] = []
            total = 0
            while True:
                fin, opcode, payload = await self._read_frame()
                if opcode == 0x8:
                    if not self.closed:
                        try:
                            await self._send_frame(0x8, payload[:125])
                        except Exception:
                            pass
                    self.closed = True
                    raise WebSocketDriverError("WebSocket peer closed the connection")
                if opcode == 0x9:
                    await self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode in (0x1, 0x2):
                    if message_opcode is not None:
                        raise WebSocketDriverError("New WebSocket data frame before fragmented message completed")
                    message_opcode = opcode
                    chunks = [payload]
                    total = len(payload)
                elif opcode == 0x0:
                    if message_opcode is None:
                        raise WebSocketDriverError("Unexpected WebSocket continuation frame")
                    chunks.append(payload)
                    total += len(payload)
                else:
                    raise WebSocketDriverError(f"Unsupported WebSocket opcode {opcode}")
                if self.max_size is not None and total > self.max_size:
                    raise WebSocketDriverError("WebSocket message exceeds configured max_size")
                if fin:
                    data = b"".join(chunks)
                    if message_opcode == 0x1:
                        try:
                            return data.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise WebSocketDriverError("Invalid UTF-8 WebSocket text frame") from exc
                    return data

    async def close(self) -> None:
        if self.closed:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
            return
        try:
            await self._send_frame(0x8, (1000).to_bytes(2, "big"))
        except Exception:
            pass
        self.closed = True
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass


async def _open_native(cfg: WebSocketDriverConfig) -> NativeWebSocket:
    parsed = urlparse(cfg.url)
    if parsed.scheme != "wss" or not parsed.hostname:
        raise WebSocketDriverError("WhatsApp WebSocket URL must use wss://")
    host = parsed.hostname
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    context = ssl.create_default_context()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context, server_hostname=host),
            timeout=cfg.open_timeout,
        )
    except Exception as exc:
        raise WebSocketDriverError(f"WhatsApp TLS connection failed: {type(exc).__name__}: {exc}") from exc

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    host_header = host if port == 443 else f"{host}:{port}"
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Origin: {cfg.origin}\r\n"
        "User-Agent: Postmaster-MCP-v9.9/clean-room-python\r\n"
        "\r\n"
    ).encode("ascii")
    writer.write(request)
    await writer.drain()
    try:
        header_block = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=cfg.open_timeout)
    except Exception as exc:
        writer.close()
        raise WebSocketDriverError("WhatsApp WebSocket HTTP upgrade did not complete") from exc
    if len(header_block) > _MAX_HTTP_HEADERS:
        writer.close()
        raise WebSocketDriverError("WhatsApp WebSocket HTTP headers exceed safety bound")
    text = header_block.decode("iso-8859-1", errors="replace")
    lines = text.split("\r\n")
    status = lines[0] if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    expected_accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
    if not status.startswith("HTTP/1.1 101"):
        writer.close()
        raise WebSocketDriverError(f"WhatsApp WebSocket upgrade rejected: {status or 'no status'}")
    if headers.get("upgrade", "").lower() != "websocket" or "upgrade" not in headers.get("connection", "").lower():
        writer.close()
        raise WebSocketDriverError("WhatsApp WebSocket upgrade headers are invalid")
    if headers.get("sec-websocket-accept", "") != expected_accept:
        writer.close()
        raise WebSocketDriverError("WhatsApp WebSocket Sec-WebSocket-Accept mismatch")
    return NativeWebSocket(reader, writer, max_size=cfg.max_size)


async def open_whatsapp_websocket(
    config: WebSocketDriverConfig | None = None,
    *,
    connect_impl: Callable[..., Awaitable[Any]] | None = None,
) -> Any:
    """Open the raw WhatsApp WebSocket using injected, native, or optional library transport."""
    cfg = config or WebSocketDriverConfig()
    if not str(cfg.url).startswith("wss://"):
        raise WebSocketDriverError("WhatsApp WebSocket URL must use wss://")
    if connect_impl is not None:
        try:
            return await connect_impl(
                cfg.url,
                origin=cfg.origin,
                open_timeout=cfg.open_timeout,
                close_timeout=cfg.close_timeout,
                max_size=cfg.max_size,
            )
        except Exception as exc:
            raise WebSocketDriverError(f"WhatsApp WebSocket connection failed: {type(exc).__name__}: {exc}") from exc

    if cfg.prefer_native:
        return await _open_native(cfg)

    try:
        from websockets.asyncio.client import connect
    except Exception:
        return await _open_native(cfg)
    try:
        return await connect(
            cfg.url,
            origin=cfg.origin,
            open_timeout=cfg.open_timeout,
            close_timeout=cfg.close_timeout,
            max_size=cfg.max_size,
        )
    except Exception as exc:
        raise WebSocketDriverError(f"WhatsApp WebSocket connection failed: {type(exc).__name__}: {exc}") from exc


def driver_status() -> dict[str, object]:
    try:
        import websockets  # noqa: F401
        library_available = True
    except Exception:
        library_available = False
    return {
        "endpoint": DEFAULT_WA_WEBSOCKET_URL,
        "origin": DEFAULT_WA_ORIGIN,
        "driver_available": True,
        "native_driver": True,
        "optional_websockets_library": library_available,
        "noise_required": True,
        "protocol_authenticated": False,
    }


__all__ = [
    "DEFAULT_WA_WEBSOCKET_URL", "DEFAULT_WA_ORIGIN", "WebSocketDriverError", "WebSocketDriverConfig",
    "NativeWebSocket", "open_whatsapp_websocket", "driver_status",
]
