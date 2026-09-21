from __future__ import annotations

import asyncio
from dataclasses import dataclass
import random
import time
from typing import Awaitable, Callable, Protocol


class TransportError(RuntimeError):
    pass


class WebSocketLike(Protocol):
    async def send(self, data: bytes) -> object: ...
    async def recv(self) -> bytes | str: ...
    async def close(self) -> object: ...


Connector = Callable[[], Awaitable[WebSocketLike]]


@dataclass(slots=True)
class ReconnectPolicy:
    base_seconds: float = 1.0
    max_seconds: float = 30.0
    factor: float = 1.7
    jitter: float = 0.2

    def delay(self, attempt: int, *, rand: Callable[[], float] = random.random) -> float:
        raw = min(self.max_seconds, self.base_seconds * (self.factor ** max(0, int(attempt))))
        spread = raw * max(0.0, self.jitter)
        return max(0.0, raw - spread + (2 * spread * rand()))


class ReconnectingTransport:
    """Async WebSocket lifecycle with injected connector and explicit stop semantics.

    Network/library choice stays outside this module, keeping the clean-room protocol core pure
    Python and avoiding a hard requirements.txt dependency.
    """

    def __init__(self, connector: Connector, *, policy: ReconnectPolicy | None = None):
        self.connector = connector
        self.policy = policy or ReconnectPolicy()
        self.socket: WebSocketLike | None = None
        self.connected = False
        self.connect_attempts = 0
        self.reconnects = 0
        self.last_error: str | None = None
        self.last_connected_at: float | None = None
        self._stop = False
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        async with self._lock:
            if self.connected and self.socket is not None: return
            self.connect_attempts += 1
            try:
                self.socket = await self.connector()
                self.connected = True
                self.last_error = None
                self.last_connected_at = time.time()
            except Exception as exc:
                self.connected = False; self.socket = None; self.last_error = f"{type(exc).__name__}: {exc}"
                raise TransportError(self.last_error) from exc

    async def ensure_connected(self, *, max_attempts: int | None = None, sleep: Callable[[float], Awaitable[object]] = asyncio.sleep) -> None:
        attempt = 0
        while not self._stop:
            try:
                await self.connect(); return
            except TransportError:
                if max_attempts is not None and attempt + 1 >= max_attempts: raise
                await sleep(self.policy.delay(attempt)); attempt += 1; self.reconnects += 1
        raise TransportError("Transport stopped")

    async def send(self, payload: bytes) -> None:
        if not self.connected or self.socket is None: raise TransportError("WhatsApp transport is not connected")
        try:
            await self.socket.send(bytes(payload))
        except Exception as exc:
            self.connected = False; self.last_error = f"{type(exc).__name__}: {exc}"
            raise TransportError(self.last_error) from exc

    async def recv(self) -> bytes:
        if not self.connected or self.socket is None: raise TransportError("WhatsApp transport is not connected")
        try:
            value = await self.socket.recv()
        except Exception as exc:
            self.connected = False; self.last_error = f"{type(exc).__name__}: {exc}"
            raise TransportError(self.last_error) from exc
        return value.encode("utf-8") if isinstance(value, str) else bytes(value)

    async def close(self) -> None:
        self._stop = True
        sock, self.socket = self.socket, None
        self.connected = False
        if sock is not None:
            await sock.close()

    def status(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "connect_attempts": self.connect_attempts,
            "reconnects": self.reconnects,
            "last_error": self.last_error,
            "last_connected_at": self.last_connected_at,
            "stopped": self._stop,
        }
