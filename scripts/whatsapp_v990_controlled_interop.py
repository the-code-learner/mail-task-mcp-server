from __future__ import annotations

"""Controlled-account WhatsApp v9.9 interoperability harness for GitHub Actions.

Secrets are supplied only through the workflow environment. The script never prints or writes
QR refs, JIDs, private keys, auth database bytes, or auth key material to the evidence artifact.
It restores a previously paired *controlled test account* session, reconnects explicitly, and
can exercise direct text, media, group listing, remote receipts and inbound echo.

This harness does not mutate source acceptance flags. A human must review the resulting evidence
before any release flag is changed.
"""

import asyncio
import base64
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
from typing import Any

from postmaster.whatsapp_v990.adapter import CurrentProtocolAdapter
from postmaster.whatsapp_v990.service import WhatsAppEventStore
from postmaster.whatsapp_v990.store import EncryptedAuthStore


class HarnessError(RuntimeError):
    pass


@dataclass
class Evidence:
    reconnect_ok: bool = False
    prekey_server_count: int | None = None
    group_listing_ok: bool = False
    group_count: int | None = None
    text_send_ok: bool = False
    text_server_ack: bool = False
    text_device_fanout: int | None = None
    text_remote_device_targets: int | None = None
    text_own_device_targets: int | None = None
    text_used_prekey_message: bool | None = None
    media_send_ok: bool = False
    media_server_ack: bool = False
    media_device_fanout: int | None = None
    remote_receipt_observed: bool = False
    inbound_echo_observed: bool = False
    local_read_receipt_emitted: bool = False
    protocol_interop_observed: bool = False
    signal_multidevice_observed: bool = False
    controlled_account_interop_observed: bool = False


class HarnessFileStore:
    def __init__(self) -> None:
        self._files = {
            "controlled-interop-document": (
                {"filename": "postmaster-v990-controlled.txt", "media_type": "text/plain"},
                b"Postmaster MCP v9.9 controlled WhatsApp media interoperability probe.\n",
            )
        }

    def raw_bytes(self, file_id: str):
        if file_id not in self._files:
            raise KeyError(file_id)
        info, data = self._files[file_id]
        return dict(info), bytes(data)


def _required(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise HarnessError(f"Required controlled-interop environment value is missing: {name}")
    return value


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _decode_secret(name: str) -> bytes:
    value = _required(name)
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise HarnessError(f"{name} is not valid Base64") from exc
    if not raw:
        raise HarnessError(f"{name} decoded to empty bytes")
    return raw


def _write_secret(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise HarnessError("Controlled auth secret file permissions are broader than 0600")


async def _wait_for_receipt(events: WhatsAppEventStore, message_id: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for row in events.list_receipts(limit=500):
            if str(row.get("message_id") or "") == message_id and str(row.get("source") or "") == "remote":
                return True
        await asyncio.sleep(1.0)
    return False


async def _wait_for_echo(events: WhatsAppEventStore, token: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for row in events.list_messages(limit=500):
            if str(row.get("direction") or "") == "in" and token in str(row.get("text") or ""):
                return True
        await asyncio.sleep(1.0)
    return False


async def main() -> int:
    auth_db_bytes = _decode_secret("POSTMASTER_WA_AUTH_DB_B64")
    auth_key_bytes = _decode_secret("POSTMASTER_WA_AUTH_KEY_B64")
    if len(auth_key_bytes) != 32:
        raise HarnessError("POSTMASTER_WA_AUTH_KEY_B64 must decode to exactly 32 bytes")

    test_jid = _required("POSTMASTER_WA_TEST_JID")
    require_receipt = _bool("POSTMASTER_WA_REQUIRE_REMOTE_RECEIPT", True)
    require_echo = _bool("POSTMASTER_WA_REQUIRE_INBOUND_ECHO", True)
    run_media = _bool("POSTMASTER_WA_RUN_MEDIA", True)
    timeout = float(os.environ.get("POSTMASTER_WA_OBSERVE_TIMEOUT_SECONDS") or "90")
    timeout = min(max(timeout, 10.0), 240.0)
    evidence_path = Path(os.environ.get("POSTMASTER_WA_EVIDENCE_PATH") or "whatsapp-v990-controlled-evidence.json")

    evidence = Evidence()
    with tempfile.TemporaryDirectory(prefix="postmaster-wa-controlled-") as td:
        root = Path(td)
        auth_db = root / "auth.db"
        auth_key = root / "auth.key"
        event_db = root / "events.db"
        _write_secret(auth_db, auth_db_bytes)
        _write_secret(auth_key, auth_key_bytes)

        auth = EncryptedAuthStore(str(auth_db), key_path=str(auth_key))
        events = WhatsAppEventStore(str(event_db))
        adapter = CurrentProtocolAdapter(
            auth,
            on_message=lambda **kwargs: events.record_message(**kwargs),
            on_receipt=lambda **kwargs: events.record_receipt(**kwargs),
            file_store=HarnessFileStore(),
        )

        try:
            connected = dict(await adapter.reconnect())
            evidence.reconnect_ok = bool(connected.get("connected"))
            evidence.prekey_server_count = int(connected.get("server_pre_key_count")) if connected.get("server_pre_key_count") is not None else None
            if not evidence.reconnect_ok:
                raise HarnessError("Controlled WhatsApp session did not reconnect")

            groups = await adapter.list_groups()
            evidence.group_listing_ok = True
            evidence.group_count = len(groups)

            token = "postmaster-v990-" + secrets.token_hex(12)
            text = dict(await adapter.send_text(jid=test_jid, text=token))
            evidence.text_send_ok = bool(text.get("ok"))
            evidence.text_server_ack = bool(text.get("server_ack"))
            evidence.text_device_fanout = int(text.get("device_fanout")) if text.get("device_fanout") is not None else None
            evidence.text_remote_device_targets = int(text.get("remote_device_targets")) if text.get("remote_device_targets") is not None else None
            evidence.text_own_device_targets = int(text.get("own_device_targets")) if text.get("own_device_targets") is not None else None
            evidence.text_used_prekey_message = bool(text.get("used_prekey_message"))
            message_id = str(text.get("message_id") or "")
            if not evidence.text_send_ok or not evidence.text_server_ack or not message_id:
                raise HarnessError("Controlled direct-text send did not receive a clean server ack")

            if run_media:
                media = dict(
                    await adapter.send_media(
                        jid=test_jid,
                        stored_file_id="controlled-interop-document",
                        caption="Postmaster v9.9 controlled media probe",
                    )
                )
                evidence.media_send_ok = bool(media.get("ok"))
                evidence.media_server_ack = bool(media.get("server_ack"))
                evidence.media_device_fanout = int(media.get("device_fanout")) if media.get("device_fanout") is not None else None
                if not evidence.media_send_ok or not evidence.media_server_ack:
                    raise HarnessError("Controlled media send did not receive a clean server ack")

            evidence.remote_receipt_observed = await _wait_for_receipt(events, message_id, timeout) if require_receipt else False
            evidence.inbound_echo_observed = await _wait_for_echo(events, token, timeout) if require_echo else False

            evidence.protocol_interop_observed = bool(
                evidence.reconnect_ok
                and evidence.group_listing_ok
                and evidence.text_server_ack
                and (not run_media or evidence.media_server_ack)
            )
            evidence.signal_multidevice_observed = bool(
                evidence.text_server_ack
                and (evidence.text_device_fanout or 0) > 0
                and (evidence.text_remote_device_targets or 0) > 0
                and (not require_echo or evidence.inbound_echo_observed)
            )
            evidence.controlled_account_interop_observed = bool(
                evidence.protocol_interop_observed
                and evidence.signal_multidevice_observed
                and (not require_receipt or evidence.remote_receipt_observed)
                and (not require_echo or evidence.inbound_echo_observed)
            )

            if require_receipt and not evidence.remote_receipt_observed:
                raise HarnessError("No remote receipt for the controlled text message was observed before timeout")
            if require_echo and not evidence.inbound_echo_observed:
                raise HarnessError("No inbound controlled-account echo was observed before timeout")
            if not evidence.controlled_account_interop_observed:
                raise HarnessError("Controlled-account acceptance conditions were not all satisfied")
        finally:
            await adapter.close()

    evidence_path.write_text(json.dumps(asdict(evidence), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("Controlled WhatsApp interoperability evidence written without account identifiers or key material.")
    print(f"protocol_interop_observed={evidence.protocol_interop_observed}")
    print(f"signal_multidevice_observed={evidence.signal_multidevice_observed}")
    print(f"controlled_account_interop_observed={evidence.controlled_account_interop_observed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
