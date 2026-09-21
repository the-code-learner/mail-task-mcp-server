from __future__ import annotations

"""Controlled-account WhatsApp v9.9 interoperability harness for GitHub Actions.

Secrets are supplied only through the workflow environment. The script never prints or writes
QR refs, JIDs, private keys, auth database bytes, auth key material, downloaded media bytes, or
stored-file payloads to the evidence artifact.

The full acceptance profile restores a previously paired controlled test account and exercises:
- authenticated reconnect and pre-key maintenance,
- participating-group listing,
- direct multi-device text and media send,
- group SenderKey text and media send,
- remote receipt observation,
- inbound direct text decrypt,
- inbound group SenderKey text decrypt,
- inbound group media decrypt/download and canonical Stored File handoff.

The test peer is instructed in-band using random one-run tokens. This harness never changes source
acceptance flags automatically; a successful run is evidence for a human-reviewed release gate.
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
from postmaster.whatsapp_v990.jid import parse_jid
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
    target_group_visible: bool = False

    direct_text_send_ok: bool = False
    direct_text_server_ack: bool = False
    direct_text_device_fanout: int | None = None
    direct_text_remote_device_targets: int | None = None
    direct_text_own_device_targets: int | None = None
    direct_text_used_prekey_message: bool | None = None
    direct_text_read_receipt_emitted: bool = False

    direct_media_send_ok: bool = False
    direct_media_server_ack: bool = False
    direct_media_device_fanout: int | None = None
    direct_media_read_receipt_emitted: bool = False

    group_text_send_ok: bool = False
    group_text_server_ack: bool = False
    group_text_device_fanout: int | None = None
    group_text_sender_key_recipients: int | None = None
    group_text_read_receipt_emitted: bool = False

    group_media_send_ok: bool = False
    group_media_server_ack: bool = False
    group_media_device_fanout: int | None = None
    group_media_sender_key_recipients: int | None = None
    group_media_read_receipt_emitted: bool = False

    remote_receipt_observed: bool = False
    inbound_direct_echo_observed: bool = False
    inbound_group_echo_observed: bool = False
    inbound_group_media_observed: bool = False
    inbound_group_media_stored_file_observed: bool = False
    inbound_stored_file_count: int = 0

    protocol_interop_observed: bool = False
    signal_multidevice_observed: bool = False
    group_sender_key_interop_observed: bool = False
    media_interop_observed: bool = False
    receipt_policy_observed: bool = False
    controlled_account_interop_observed: bool = False


class HarnessFileStore:
    """In-memory Stored File boundary for a one-run controlled probe.

    Outbound fixture bytes and inbound downloaded bytes stay in process memory only. Evidence
    contains counts/booleans, never media bytes, filenames supplied by the peer, or file IDs.
    """

    def __init__(self) -> None:
        self._files: dict[str, tuple[dict[str, Any], bytes]] = {
            "controlled-interop-document": (
                {"id": "controlled-interop-document", "filename": "postmaster-v990-controlled.txt", "media_type": "text/plain"},
                b"Postmaster MCP v9.9 controlled WhatsApp media interoperability probe.\n",
            )
        }
        self.inbound_saved = 0

    def raw_bytes(self, file_id: str):
        if file_id not in self._files:
            raise KeyError(file_id)
        info, data = self._files[file_id]
        return dict(info), bytes(data)

    def save_bytes(
        self,
        *,
        owner_id: str,
        filename: str,
        data: bytes,
        project_id: str | None = None,
        media_type: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        fid = file_id or ("controlled-inbound-" + secrets.token_hex(8))
        info = {
            "id": fid,
            "owner_id": str(owner_id),
            "project_id": str(project_id) if project_id else None,
            "filename": str(filename),
            "media_type": str(media_type or "application/octet-stream"),
            "size_bytes": len(bytes(data)),
            "description": str(description or ""),
            "tags": list(tags or []),
        }
        self._files[fid] = (dict(info), bytes(data))
        self.inbound_saved += 1
        return dict(info)


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
            if (
                str(row.get("message_id") or "") == message_id
                and str(row.get("source") or "") == "remote"
            ):
                return True
        await asyncio.sleep(1.0)
    return False


async def _wait_for_message(
    events: WhatsAppEventStore,
    *,
    jid: str,
    token: str,
    kind: str,
    timeout: float,
) -> dict[str, Any] | None:
    wanted_jid = str(parse_jid(jid))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for row in events.list_messages(jid=wanted_jid, limit=500):
            if str(row.get("direction") or "") != "in":
                continue
            if str(row.get("kind") or "") != kind:
                continue
            if token not in str(row.get("text") or ""):
                continue
            return row
        await asyncio.sleep(1.0)
    return None


def _int_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


def _assert_no_local_read_receipt(label: str, result: dict[str, Any]) -> None:
    if bool(result.get("read_receipt_emitted")):
        raise HarnessError(f"{label} unexpectedly emitted a local WhatsApp read receipt")


async def main() -> int:
    auth_db_bytes = _decode_secret("POSTMASTER_WA_AUTH_DB_B64")
    auth_key_bytes = _decode_secret("POSTMASTER_WA_AUTH_KEY_B64")
    if len(auth_key_bytes) != 32:
        raise HarnessError("POSTMASTER_WA_AUTH_KEY_B64 must decode to exactly 32 bytes")

    test_jid = str(parse_jid(_required("POSTMASTER_WA_TEST_JID")).normalized_user())
    test_group_jid = str(parse_jid(_required("POSTMASTER_WA_TEST_GROUP_JID")))
    if not parse_jid(test_group_jid).is_group:
        raise HarnessError("POSTMASTER_WA_TEST_GROUP_JID must be a g.us group JID")

    require_receipt = _bool("POSTMASTER_WA_REQUIRE_REMOTE_RECEIPT", True)
    require_direct_echo = _bool("POSTMASTER_WA_REQUIRE_INBOUND_ECHO", True)
    require_group_echo = _bool("POSTMASTER_WA_REQUIRE_GROUP_ECHO", True)
    require_inbound_media = _bool("POSTMASTER_WA_REQUIRE_INBOUND_MEDIA", True)
    run_media = _bool("POSTMASTER_WA_RUN_MEDIA", True)
    timeout = float(os.environ.get("POSTMASTER_WA_OBSERVE_TIMEOUT_SECONDS") or "120")
    timeout = min(max(timeout, 10.0), 300.0)
    evidence_path = Path(
        os.environ.get("POSTMASTER_WA_EVIDENCE_PATH")
        or "whatsapp-v990-controlled-evidence.json"
    )

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
        files = HarnessFileStore()
        adapter = CurrentProtocolAdapter(
            auth,
            on_message=lambda **kwargs: events.record_message(**kwargs),
            on_receipt=lambda **kwargs: events.record_receipt(**kwargs),
            file_store=files,
            file_owner_id="controlled-interop",
        )

        try:
            connected = dict(await adapter.reconnect())
            evidence.reconnect_ok = bool(connected.get("connected"))
            evidence.prekey_server_count = _int_or_none(connected.get("server_pre_key_count"))
            if not evidence.reconnect_ok:
                raise HarnessError("Controlled WhatsApp session did not reconnect")

            groups = [dict(item) for item in await adapter.list_groups()]
            evidence.group_listing_ok = True
            evidence.group_count = len(groups)
            evidence.target_group_visible = any(
                str(parse_jid(str(item.get("id") or ""))) == test_group_jid
                for item in groups
                if item.get("id")
            )
            if not evidence.target_group_visible:
                raise HarnessError("Controlled test group is not visible to the paired companion")

            direct_token = "postmaster-direct-" + secrets.token_hex(12)
            direct = dict(
                await adapter.send_text(
                    jid=test_jid,
                    text=f"ECHO_DIRECT:{direct_token}",
                )
            )
            evidence.direct_text_send_ok = bool(direct.get("ok"))
            evidence.direct_text_server_ack = bool(direct.get("server_ack"))
            evidence.direct_text_device_fanout = _int_or_none(direct.get("device_fanout"))
            evidence.direct_text_remote_device_targets = _int_or_none(
                direct.get("remote_device_targets")
            )
            evidence.direct_text_own_device_targets = _int_or_none(
                direct.get("own_device_targets")
            )
            evidence.direct_text_used_prekey_message = bool(direct.get("used_prekey_message"))
            evidence.direct_text_read_receipt_emitted = bool(
                direct.get("read_receipt_emitted")
            )
            _assert_no_local_read_receipt("Controlled direct text send", direct)
            direct_message_id = str(direct.get("message_id") or "")
            if (
                not evidence.direct_text_send_ok
                or not evidence.direct_text_server_ack
                or not direct_message_id
            ):
                raise HarnessError(
                    "Controlled direct-text send did not receive a clean server ack"
                )

            if run_media:
                direct_media = dict(
                    await adapter.send_media(
                        jid=test_jid,
                        stored_file_id="controlled-interop-document",
                        caption="Postmaster v9.9 controlled direct media probe",
                    )
                )
                evidence.direct_media_send_ok = bool(direct_media.get("ok"))
                evidence.direct_media_server_ack = bool(direct_media.get("server_ack"))
                evidence.direct_media_device_fanout = _int_or_none(
                    direct_media.get("device_fanout")
                )
                evidence.direct_media_read_receipt_emitted = bool(
                    direct_media.get("read_receipt_emitted")
                )
                _assert_no_local_read_receipt(
                    "Controlled direct media send",
                    direct_media,
                )
                if (
                    not evidence.direct_media_send_ok
                    or not evidence.direct_media_server_ack
                ):
                    raise HarnessError(
                        "Controlled direct-media send did not receive a clean server ack"
                    )

            group_token = "postmaster-group-" + secrets.token_hex(12)
            group_text = dict(
                await adapter.send_text(
                    jid=test_group_jid,
                    text=f"ECHO_GROUP:{group_token}",
                )
            )
            evidence.group_text_send_ok = bool(group_text.get("ok"))
            evidence.group_text_server_ack = bool(group_text.get("server_ack"))
            evidence.group_text_device_fanout = _int_or_none(
                group_text.get("device_fanout")
            )
            evidence.group_text_sender_key_recipients = _int_or_none(
                group_text.get("sender_key_recipients")
            )
            evidence.group_text_read_receipt_emitted = bool(
                group_text.get("read_receipt_emitted")
            )
            _assert_no_local_read_receipt("Controlled group text send", group_text)
            if (
                not evidence.group_text_send_ok
                or not evidence.group_text_server_ack
            ):
                raise HarnessError(
                    "Controlled group-text send did not receive a clean server ack"
                )

            media_token = "postmaster-media-" + secrets.token_hex(12)
            await adapter.send_text(
                jid=test_group_jid,
                text=(
                    "CONTROLLED_MEDIA_REQUEST:"
                    + media_token
                    + " — send one small media attachment to this group with this exact token in its caption."
                ),
            )

            if run_media:
                group_media = dict(
                    await adapter.send_media(
                        jid=test_group_jid,
                        stored_file_id="controlled-interop-document",
                        caption="Postmaster v9.9 controlled group media probe",
                    )
                )
                evidence.group_media_send_ok = bool(group_media.get("ok"))
                evidence.group_media_server_ack = bool(group_media.get("server_ack"))
                evidence.group_media_device_fanout = _int_or_none(
                    group_media.get("device_fanout")
                )
                evidence.group_media_sender_key_recipients = _int_or_none(
                    group_media.get("sender_key_recipients")
                )
                evidence.group_media_read_receipt_emitted = bool(
                    group_media.get("read_receipt_emitted")
                )
                _assert_no_local_read_receipt(
                    "Controlled group media send",
                    group_media,
                )
                if (
                    not evidence.group_media_send_ok
                    or not evidence.group_media_server_ack
                ):
                    raise HarnessError(
                        "Controlled group-media send did not receive a clean server ack"
                    )

            receipt_coro = (
                _wait_for_receipt(events, direct_message_id, timeout)
                if require_receipt
                else asyncio.sleep(0, result=False)
            )
            direct_echo_coro = (
                _wait_for_message(
                    events,
                    jid=test_jid,
                    token=direct_token,
                    kind="text",
                    timeout=timeout,
                )
                if require_direct_echo
                else asyncio.sleep(0, result=None)
            )
            group_echo_coro = (
                _wait_for_message(
                    events,
                    jid=test_group_jid,
                    token=group_token,
                    kind="text",
                    timeout=timeout,
                )
                if require_group_echo
                else asyncio.sleep(0, result=None)
            )
            group_media_coro = (
                _wait_for_message(
                    events,
                    jid=test_group_jid,
                    token=media_token,
                    kind="media",
                    timeout=timeout,
                )
                if require_inbound_media
                else asyncio.sleep(0, result=None)
            )

            (
                receipt_observed,
                direct_echo,
                group_echo,
                group_media_in,
            ) = await asyncio.gather(
                receipt_coro,
                direct_echo_coro,
                group_echo_coro,
                group_media_coro,
            )
            evidence.remote_receipt_observed = bool(receipt_observed)
            evidence.inbound_direct_echo_observed = direct_echo is not None
            evidence.inbound_group_echo_observed = group_echo is not None
            evidence.inbound_group_media_observed = group_media_in is not None
            evidence.inbound_group_media_stored_file_observed = bool(
                group_media_in and group_media_in.get("stored_file_id")
            )
            evidence.inbound_stored_file_count = files.inbound_saved

            evidence.receipt_policy_observed = bool(
                not evidence.direct_text_read_receipt_emitted
                and not evidence.direct_media_read_receipt_emitted
                and not evidence.group_text_read_receipt_emitted
                and not evidence.group_media_read_receipt_emitted
            )
            evidence.protocol_interop_observed = bool(
                evidence.reconnect_ok
                and evidence.group_listing_ok
                and evidence.target_group_visible
                and evidence.direct_text_server_ack
                and evidence.group_text_server_ack
                and (not run_media or evidence.direct_media_server_ack)
                and (not run_media or evidence.group_media_server_ack)
            )
            evidence.signal_multidevice_observed = bool(
                evidence.direct_text_server_ack
                and (evidence.direct_text_device_fanout or 0) > 0
                and (evidence.direct_text_remote_device_targets or 0) > 0
                and evidence.inbound_direct_echo_observed
            )
            evidence.group_sender_key_interop_observed = bool(
                evidence.group_text_server_ack
                and (evidence.group_text_device_fanout or 0) > 0
                and evidence.inbound_group_echo_observed
            )
            evidence.media_interop_observed = bool(
                run_media
                and evidence.direct_media_server_ack
                and evidence.group_media_server_ack
                and evidence.inbound_group_media_observed
                and evidence.inbound_group_media_stored_file_observed
                and evidence.inbound_stored_file_count > 0
            )
            evidence.controlled_account_interop_observed = bool(
                evidence.protocol_interop_observed
                and evidence.signal_multidevice_observed
                and evidence.group_sender_key_interop_observed
                and evidence.media_interop_observed
                and evidence.receipt_policy_observed
                and evidence.remote_receipt_observed
            )

            if require_receipt and not evidence.remote_receipt_observed:
                raise HarnessError(
                    "No remote receipt for the controlled direct text was observed before timeout"
                )
            if require_direct_echo and not evidence.inbound_direct_echo_observed:
                raise HarnessError(
                    "No inbound controlled direct echo was observed before timeout"
                )
            if require_group_echo and not evidence.inbound_group_echo_observed:
                raise HarnessError(
                    "No inbound controlled group echo was observed before timeout"
                )
            if require_inbound_media and not evidence.inbound_group_media_stored_file_observed:
                raise HarnessError(
                    "No inbound controlled group media was decrypted and stored before timeout"
                )
            if not evidence.receipt_policy_observed:
                raise HarnessError(
                    "A non-reply controlled send unexpectedly emitted a local read receipt"
                )
            if not evidence.controlled_account_interop_observed:
                raise HarnessError(
                    "Controlled-account full acceptance conditions were not all satisfied"
                )
        finally:
            await adapter.close()

    evidence_path.write_text(
        json.dumps(asdict(evidence), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "Controlled WhatsApp interoperability evidence written without account identifiers, "
        "key material or media payloads."
    )
    print(f"protocol_interop_observed={evidence.protocol_interop_observed}")
    print(f"signal_multidevice_observed={evidence.signal_multidevice_observed}")
    print(f"group_sender_key_interop_observed={evidence.group_sender_key_interop_observed}")
    print(f"media_interop_observed={evidence.media_interop_observed}")
    print(f"receipt_policy_observed={evidence.receipt_policy_observed}")
    print(
        "controlled_account_interop_observed="
        f"{evidence.controlled_account_interop_observed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
