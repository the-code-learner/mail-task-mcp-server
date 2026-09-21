from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


DEFAULT_WA_WEBSOCKET_URL = "wss://web.whatsapp.com/ws/chat"
DEFAULT_WA_ORIGIN = "https://web.whatsapp.com"


class WebSocketDriverError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WebSocketDriverConfig:
    url: str = DEFAULT_WA_WEBSOCKET_URL
    origin: str = DEFAULT_WA_ORIGIN
    open_timeout: float = 20.0
    close_timeout: float = 10.0
    max_size: int | None = None


async def open_whatsapp_websocket(
    config: WebSocketDriverConfig | None = None,
    *,
    connect_impl: Callable[..., Awaitable[Any]] | None = None,
) -> Any:
    """Open the raw WA WebSocket using a lazy/optional websockets dependency.

    This function deliberately stops at the transport boundary: Noise XX and WhatsApp binary
    protocol validation happen above it. Tests inject connect_impl so no external network is used.
    """
    cfg = config or WebSocketDriverConfig()
    if not str(cfg.url).startswith("wss://"):
        raise WebSocketDriverError("WhatsApp WebSocket URL must use wss://")
    connector = connect_impl
    if connector is None:
        try:
            from websockets.asyncio.client import connect as connector  # type: ignore
        except Exception as exc:
            raise WebSocketDriverError("Python websockets driver is unavailable") from exc
    try:
        return await connector(
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
        available = True
    except Exception:
        available = False
    return {
        "endpoint": DEFAULT_WA_WEBSOCKET_URL,
        "origin": DEFAULT_WA_ORIGIN,
        "driver_available": available,
        "noise_required": True,
        "protocol_authenticated": False,
    }


__all__ = [
    "DEFAULT_WA_WEBSOCKET_URL", "DEFAULT_WA_ORIGIN", "WebSocketDriverError", "WebSocketDriverConfig",
    "open_whatsapp_websocket", "driver_status",
]
