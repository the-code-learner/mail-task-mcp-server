from __future__ import annotations

from typing import Any, Callable

from mcp.types import ToolAnnotations

from .whatsapp_v990.service import WhatsAppService, WhatsAppServiceError

MCP_WHATSAPP_COMMANDS_V990 = 8


def _ann(*, read_only: bool, idempotent: bool = False):
    return ToolAnnotations(read_only_hint=read_only, destructive_hint=False, idempotent_hint=idempotent, open_world_hint=False)


def install_whatsapp_runtime_v990(
    base: Any,
    core: Any,
    previous_runtime_status: Callable[[], Any],
    *,
    service: WhatsAppService | None = None,
    auth_db: str = "/data/whatsapp-v990-auth.db",
    event_db: str = "/data/whatsapp-v990-events.db",
    key_path: str = "/data/whatsapp-v990-auth.key",
) -> dict[str, Any]:
    """Install the v9.9 WhatsApp MCP boundary.

    The service defaults to the clean-room current-protocol adapter, which is explicit-action
    only: construction/status never opens the network. Pairing and reconnect require dedicated
    actions. Direct/group text, media and Stored File handoff are implemented, while stable
    readiness remains controlled-account gated.
    """
    wa_obj = service

    def get_service() -> WhatsAppService:
        nonlocal wa_obj
        if wa_obj is None:
            store_factory = getattr(base, "file_store", None)
            file_store = store_factory() if callable(store_factory) else None
            wa_obj = WhatsAppService.create(
                auth_db=auth_db,
                event_db=event_db,
                key_path=key_path,
                file_store=file_store,
            )
        return wa_obj

    def safe_sync(fn, *args, **kwargs):
        try: return fn(*args, **kwargs)
        except (WhatsAppServiceError, ValueError) as exc: return {"ok": False, "error": str(exc)}
        except Exception as exc: return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def safe_async(fn, *args, **kwargs):
        try: return await fn(*args, **kwargs)
        except (WhatsAppServiceError, ValueError) as exc: return {"ok": False, "error": str(exc)}
        except Exception as exc: return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def whatsapp_status():
        """Read-only. Show WhatsApp pairing/network/session status without exposing private keys or secrets."""
        return safe_sync(get_service().status)

    async def whatsapp_start_pairing():
        """WRITE ACTION. Explicitly start WhatsApp companion pairing and return the transient QR payload for WebGUI display."""
        return await safe_async(get_service().start_pairing)

    async def whatsapp_reconnect():
        """WRITE ACTION. Explicitly reconnect the persisted WhatsApp companion session; never auto-pairs a new device."""
        return await safe_async(get_service().reconnect)

    def whatsapp_list_messages(jid: str | None = None, limit: int = 100):
        """Read-only. Read locally stored WhatsApp messages. Reading never emits a WhatsApp read receipt."""
        return safe_sync(get_service().list_messages, jid=jid, limit=limit)

    async def whatsapp_send_text(jid: str, text: str, reply_to_message_id: str | None = None):
        """WRITE ACTION. Send one WhatsApp text/group message. A read receipt may be emitted only when this explicit send is a reply."""
        return await safe_async(get_service().send_text, jid=jid, text=text, reply_to_message_id=reply_to_message_id)

    async def whatsapp_send_media(jid: str, stored_file_id: str, caption: str = "", reply_to_message_id: str | None = None):
        """WRITE ACTION. Send WhatsApp media from a Postmaster Stored File id; do not route raw attachment Base64 through the MCP call."""
        return await safe_async(get_service().send_media, jid=jid, stored_file_id=stored_file_id, caption=caption, reply_to_message_id=reply_to_message_id)

    async def whatsapp_list_groups():
        """Read-only. List groups visible to the paired WhatsApp companion session."""
        return await safe_async(get_service().list_groups)

    def whatsapp_list_receipts(limit: int = 200):
        """Read-only. List remote receipts observed by Postmaster plus outbound-reply receipts emitted under the asymmetric policy."""
        return safe_sync(get_service().list_receipts, limit=limit)

    tools = (
        ("whatsapp_status", whatsapp_status, _ann(read_only=True, idempotent=True)),
        ("whatsapp_start_pairing", whatsapp_start_pairing, _ann(read_only=False)),
        ("whatsapp_reconnect", whatsapp_reconnect, _ann(read_only=False)),
        ("whatsapp_list_messages", whatsapp_list_messages, _ann(read_only=True, idempotent=True)),
        ("whatsapp_send_text", whatsapp_send_text, _ann(read_only=False)),
        ("whatsapp_send_media", whatsapp_send_media, _ann(read_only=False)),
        ("whatsapp_list_groups", whatsapp_list_groups, _ann(read_only=True, idempotent=True)),
        ("whatsapp_list_receipts", whatsapp_list_receipts, _ann(read_only=True, idempotent=True)),
    )
    for name, fn, annotations in tools:
        try: core.mcp.remove_tool(name)
        except Exception: pass
        core.mcp.add_tool(fn, name=name, annotations=annotations)
        setattr(core, name, fn); setattr(base, name, fn)

    old_status = previous_runtime_status
    def runtime_status():
        status = old_status()
        status = dict(status) if isinstance(status, dict) else {"ok": True}
        wa_status = wa_obj.status() if wa_obj is not None else {"network": {"configured": False, "connected": False}, "paired": False}
        status["whatsapp"] = {
            "clean_room_python": True,
            "tool_count": MCP_WHATSAPP_COMMANDS_V990,
            "network_adapter_configured": bool(wa_status.get("network", {}).get("configured")),
            "paired": bool(wa_status.get("paired")),
            "read_receipts_asymmetric": True,
            "local_read_emits_receipt": False,
            "auth_encrypted_at_rest": True,
            "private_material_exposed": False,
            "implementation_complete": True,
            "controlled_acceptance_required": True,
            "protocol_interop_verified": False,
            "signal_multidevice_verified": False,
            "controlled_account_interop_verified": False,
        }
        return status

    # Runtime status is a replacement, not an additional command.
    try: core.mcp.remove_tool("runtime_status")
    except Exception: pass
    core.mcp.add_tool(runtime_status, name="runtime_status", annotations=_ann(read_only=True, idempotent=True))
    setattr(core, "runtime_status", runtime_status); setattr(base, "runtime_status", runtime_status)
    base.whatsapp_service_v990 = get_service
    return {"service": wa_obj, "service_factory": get_service, "runtime_status": runtime_status, "tool_count": MCP_WHATSAPP_COMMANDS_V990}


__all__ = ["MCP_WHATSAPP_COMMANDS_V990", "install_whatsapp_runtime_v990"]
