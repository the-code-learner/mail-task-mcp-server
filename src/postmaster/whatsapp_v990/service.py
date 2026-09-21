from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping, Protocol

from .jid import parse_jid
from .receipts import receipt_for_event
from .store import EncryptedAuthStore


class WhatsAppServiceError(RuntimeError):
    pass


class WhatsAppAdapter(Protocol):
    async def start_pairing(self) -> Mapping[str, Any]: ...
    async def reconnect(self) -> Mapping[str, Any]: ...
    async def send_text(self, *, jid: str, text: str, reply_to_message_id: str | None, emit_read_receipt: bool) -> Mapping[str, Any]: ...
    async def send_media(self, *, jid: str, stored_file_id: str, caption: str, reply_to_message_id: str | None, emit_read_receipt: bool) -> Mapping[str, Any]: ...
    async def list_groups(self) -> list[Mapping[str, Any]]: ...
    def status(self) -> Mapping[str, Any]: ...


class UnavailableAdapter:
    """Safe default until the real current-protocol adapter is installed."""
    async def start_pairing(self): raise WhatsAppServiceError("WhatsApp network adapter is not configured")
    async def reconnect(self): raise WhatsAppServiceError("WhatsApp network adapter is not configured")
    async def send_text(self, **kwargs): raise WhatsAppServiceError("WhatsApp network adapter is not configured")
    async def send_media(self, **kwargs): raise WhatsAppServiceError("WhatsApp network adapter is not configured")
    async def list_groups(self): raise WhatsAppServiceError("WhatsApp network adapter is not configured")
    def status(self): return {"configured": False, "connected": False}


class WhatsAppEventStore:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with closing(self._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS whatsapp_messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT,
                    jid TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    stored_file_id TEXT,
                    reply_to_message_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_wa_messages_jid_id ON whatsapp_messages(jid,id DESC);
                CREATE TABLE IF NOT EXISTS whatsapp_receipts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT,
                    jid TEXT,
                    receipt_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

    def record_message(self, *, message_id: str | None, jid: str, direction: str, kind: str, text: str = "", stored_file_id: str | None = None, reply_to_message_id: str | None = None):
        with self._lock, closing(self._connect()) as conn:
            conn.execute("INSERT INTO whatsapp_messages(message_id,jid,direction,kind,text,stored_file_id,reply_to_message_id) VALUES(?,?,?,?,?,?,?)",
                         (message_id, jid, direction, kind, text, stored_file_id, reply_to_message_id))
            conn.commit()

    def list_messages(self, *, jid: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock, closing(self._connect()) as conn:
            if jid:
                rows = conn.execute("SELECT * FROM whatsapp_messages WHERE jid=? ORDER BY id DESC LIMIT ?", (jid, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM whatsapp_messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def record_receipt(self, *, message_id: str | None, jid: str | None, receipt_type: str, source: str):
        with self._lock, closing(self._connect()) as conn:
            conn.execute("INSERT INTO whatsapp_receipts(message_id,jid,receipt_type,source) VALUES(?,?,?,?)", (message_id,jid,receipt_type,source))
            conn.commit()

    def list_receipts(self, *, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM whatsapp_receipts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


@dataclass(slots=True)
class WhatsAppService:
    auth: EncryptedAuthStore
    events: WhatsAppEventStore
    adapter: WhatsAppAdapter

    @classmethod
    def create(
        cls,
        *,
        auth_db: str,
        event_db: str,
        key_path: str | None = None,
        adapter: WhatsAppAdapter | None = None,
        file_store: Any | None = None,
        file_owner_id: str | None = None,
        file_project_id: str | None = None,
    ) -> "WhatsAppService":
        auth = EncryptedAuthStore(auth_db, key_path=key_path)
        events = WhatsAppEventStore(event_db)
        if adapter is None:
            # CurrentProtocolAdapter is explicit-action only: construction/status never opens
            # a socket. Pairing/reconnect happen only through their dedicated MCP/WebGUI actions.
            # Incoming transport events are persisted locally without emitting read receipts.
            from .adapter import CurrentProtocolAdapter
            adapter = CurrentProtocolAdapter(
                auth,
                on_message=lambda **kwargs: events.record_message(**kwargs),
                on_receipt=lambda **kwargs: events.record_receipt(**kwargs),
                file_store=file_store,
                file_owner_id=file_owner_id or os.getenv("DEFAULT_OWNER_ID", "default"),
                file_project_id=file_project_id,
            )
        return cls(auth, events, adapter)

    def status(self) -> dict[str, Any]:
        network = dict(self.adapter.status())
        pairing = self.auth.get_json("runtime", "pairing") or {}
        return {
            "ok": True,
            "network": network,
            "paired": bool((self.auth.get_json("runtime", "identity") or {}).get("jid")),
            "pairing_qr_available": bool(pairing.get("qr")),
            "auth_store": self.auth.status(),
            "read_receipts": {"incoming_visible": True, "local_read_emits": False, "outbound_reply_may_emit": True},
        }

    async def start_pairing(self) -> dict[str, Any]:
        result = dict(await self.adapter.start_pairing())
        qr = result.get("qr")
        if qr:
            self.auth.put_json("runtime", "pairing", {"qr": str(qr), "expires_at": result.get("expires_at")})
        # Explicitly strip any private material the network adapter might accidentally return.
        safe = {k:v for k,v in result.items() if k not in {"private_key","identity_private_key","noise_private_key","secret"}}
        safe["private_material_exposed"] = False
        return safe

    async def reconnect(self) -> dict[str, Any]:
        return dict(await self.adapter.reconnect())

    def pairing_qr(self) -> str | None:
        state = self.auth.get_json("runtime", "pairing") or {}
        return str(state.get("qr")) if state.get("qr") else None

    def list_messages(self, *, jid: str | None = None, limit: int = 100) -> dict[str, Any]:
        if jid: jid = str(parse_jid(jid))
        return {"ok": True, "messages": self.events.list_messages(jid=jid, limit=limit), "read_receipt_emitted": False}

    async def send_text(self, *, jid: str, text: str, reply_to_message_id: str | None = None) -> dict[str, Any]:
        dest = str(parse_jid(jid))
        if not str(text): raise WhatsAppServiceError("WhatsApp text cannot be empty")
        decision = receipt_for_event(event="reply" if reply_to_message_id else "send", outbound_action=True, reply_to_message=bool(reply_to_message_id))
        result = dict(await self.adapter.send_text(jid=dest, text=str(text), reply_to_message_id=reply_to_message_id, emit_read_receipt=decision.emit))
        if result.get("ok", True):
            self.events.record_message(message_id=result.get("message_id"), jid=dest, direction="out", kind="text", text=str(text), reply_to_message_id=reply_to_message_id)
            if decision.emit:
                self.events.record_receipt(message_id=reply_to_message_id, jid=dest, receipt_type="read", source="outbound_reply")
        result["read_receipt_emitted"] = decision.emit
        result["read_receipt_reason"] = decision.reason
        return result

    async def send_media(self, *, jid: str, stored_file_id: str, caption: str = "", reply_to_message_id: str | None = None) -> dict[str, Any]:
        dest = str(parse_jid(jid))
        fid = str(stored_file_id or "").strip()
        if not fid: raise WhatsAppServiceError("stored_file_id is required; use Postmaster Stored File storage for WhatsApp media")
        decision = receipt_for_event(event="reply" if reply_to_message_id else "send", outbound_action=True, reply_to_message=bool(reply_to_message_id))
        result = dict(await self.adapter.send_media(jid=dest, stored_file_id=fid, caption=str(caption or ""), reply_to_message_id=reply_to_message_id, emit_read_receipt=decision.emit))
        if result.get("ok", True):
            self.events.record_message(message_id=result.get("message_id"), jid=dest, direction="out", kind="media", text=str(caption or ""), stored_file_id=fid, reply_to_message_id=reply_to_message_id)
            if decision.emit:
                self.events.record_receipt(message_id=reply_to_message_id, jid=dest, receipt_type="read", source="outbound_reply")
        result["read_receipt_emitted"] = decision.emit
        return result

    async def list_groups(self) -> dict[str, Any]:
        return {"ok": True, "groups": [dict(x) for x in await self.adapter.list_groups()]}

    def record_incoming_message(self, *, message_id: str | None, jid: str, text: str = "", kind: str = "text", stored_file_id: str | None = None):
        self.events.record_message(message_id=message_id, jid=str(parse_jid(jid)), direction="in", kind=kind, text=text, stored_file_id=stored_file_id)

    def record_incoming_receipt(self, *, message_id: str | None, jid: str | None, receipt_type: str):
        self.events.record_receipt(message_id=message_id, jid=str(parse_jid(jid)) if jid else None, receipt_type=str(receipt_type), source="remote")

    def list_receipts(self, *, limit: int = 200) -> dict[str, Any]:
        return {"ok": True, "receipts": self.events.list_receipts(limit=limit), "local_read_emits_receipt": False}
