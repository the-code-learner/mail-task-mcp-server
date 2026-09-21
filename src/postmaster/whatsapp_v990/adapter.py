from __future__ import annotations

"""Current-protocol clean-room WhatsApp companion adapter.

Pairing and reconnect are explicit actions. Constructing the adapter never opens the network.
The Signal message layer intentionally remains fail-closed until its multi-device lifecycle is
implemented and accepted against a controlled account.
"""

import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
import secrets
from typing import Any, Awaitable, Callable, Mapping

from .adv import encode_signed_device_identity, verify_and_sign_pair_success_identity
from .binary import BinaryNode, BinaryNodeCodec
from .cert import verify_noise_certificate_chain
from .client_payload import RegistrationKeys, build_login_payload, build_registration_payload
from .crypto import CurveKeyPair, WhatsAppNoiseXX, frame_noise_payload, generate_curve_keypair, split_noise_frames
from .handshake import decode_handshake, encode_client_finish, encode_client_hello
from .jid import parse_jid
from .messages import (
    build_direct_message_stanza,
    build_read_receipt,
    encode_device_sent_message,
    encode_reply_text_message,
    encode_text_message,
    encrypted_participant_node,
    generate_message_id_v2,
    participant_hash_v2,
)
from .pairing import build_pairing_qr_data
from .signal_keys import SignedPreKey, generate_registration_id, generate_signed_pre_key
from .signal_server import (
    build_prekey_count_query,
    build_prekey_upload,
    build_session_query,
    parse_prekey_count,
    parse_session_bundles,
)
from .signal_session import EncryptedSignalSessionStore, initialize_outgoing_session
from .store import EncryptedAuthStore
from .usync import build_device_query, parse_device_result
from .tokens import CURRENT_TOKEN_TABLE
from .websocket_driver import WebSocketDriverConfig, open_whatsapp_websocket


class CurrentProtocolAdapterError(RuntimeError):
    pass


@dataclass(slots=True)
class ProtocolCredentials:
    noise: CurveKeyPair
    identity: CurveKeyPair
    signed_pre_key: SignedPreKey
    registration_id: int
    adv_secret_b64: str
    registered: bool = False
    jid: str | None = None
    lid: str | None = None
    platform: str | None = None
    account_identity_b64: str | None = None
    next_pre_key_id: int = 1
    first_unuploaded_pre_key_id: int = 1

    def to_json(self) -> dict[str, Any]:
        def b64(value: bytes) -> str:
            return base64.b64encode(value).decode("ascii")
        return {
            "noise_private": b64(self.noise.private),
            "noise_public": b64(self.noise.public),
            "identity_private": b64(self.identity.private),
            "identity_public": b64(self.identity.public),
            "signed_pre_key_private": b64(self.signed_pre_key.key_pair.private),
            "signed_pre_key_public": b64(self.signed_pre_key.key_pair.public),
            "signed_pre_key_signature": b64(self.signed_pre_key.signature),
            "signed_pre_key_id": self.signed_pre_key.key_id,
            "registration_id": self.registration_id,
            "adv_secret_b64": self.adv_secret_b64,
            "registered": self.registered,
            "jid": self.jid,
            "lid": self.lid,
            "platform": self.platform,
            "account_identity_b64": self.account_identity_b64,
            "next_pre_key_id": self.next_pre_key_id,
            "first_unuploaded_pre_key_id": self.first_unuploaded_pre_key_id,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "ProtocolCredentials":
        def raw(name: str) -> bytes:
            try:
                return base64.b64decode(str(value[name]), validate=True)
            except Exception as exc:
                raise CurrentProtocolAdapterError(f"Stored WhatsApp credential {name} is invalid") from exc
        noise = CurveKeyPair(raw("noise_private"), raw("noise_public"))
        identity = CurveKeyPair(raw("identity_private"), raw("identity_public"))
        spk_pair = CurveKeyPair(raw("signed_pre_key_private"), raw("signed_pre_key_public"))
        spk = SignedPreKey(int(value["signed_pre_key_id"]), spk_pair, raw("signed_pre_key_signature"))
        return cls(
            noise=noise,
            identity=identity,
            signed_pre_key=spk,
            registration_id=int(value["registration_id"]),
            adv_secret_b64=str(value["adv_secret_b64"]),
            registered=bool(value.get("registered")),
            jid=str(value.get("jid")) if value.get("jid") else None,
            lid=str(value.get("lid")) if value.get("lid") else None,
            platform=str(value.get("platform")) if value.get("platform") else None,
            account_identity_b64=str(value.get("account_identity_b64")) if value.get("account_identity_b64") else None,
            next_pre_key_id=int(value.get("next_pre_key_id", 1)),
            first_unuploaded_pre_key_id=int(value.get("first_unuploaded_pre_key_id", 1)),
        )


class WhatsAppWireSession:
    def __init__(self, ws: Any, transport: Any, *, codec: BinaryNodeCodec | None = None):
        self.ws = ws
        self.transport = transport
        self.codec = codec or BinaryNodeCodec(CURRENT_TOKEN_TABLE)
        self.pending = b""
        self.nodes: list[BinaryNode] = []
        self.closed = False
        self._send_lock = asyncio.Lock()
        self._query_lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        *,
        noise_static: CurveKeyPair,
        client_payload: bytes,
        websocket_config: WebSocketDriverConfig | None = None,
    ) -> "WhatsAppWireSession":
        ephemeral = generate_curve_keypair()
        noise = WhatsAppNoiseXX(ephemeral)
        ws = await open_whatsapp_websocket(websocket_config or WebSocketDriverConfig(prefer_native=True))
        try:
            await ws.send(frame_noise_payload(encode_client_hello(ephemeral.public), intro=b"WA\x06\x03"))
            pending = b""
            frames: list[bytes] = []
            while not frames:
                value = await asyncio.wait_for(ws.recv(), timeout=15)
                if isinstance(value, str):
                    raise CurrentProtocolAdapterError("WhatsApp returned text during Noise handshake")
                frames, pending = split_noise_frames(pending + bytes(value))
            if len(frames) != 1:
                raise CurrentProtocolAdapterError(f"Expected one Noise ServerHello frame, got {len(frames)}")
            server = decode_handshake(frames[0]).server_hello
            if server is None:
                raise CurrentProtocolAdapterError("WhatsApp did not return Noise ServerHello")
            encrypted_static, cert_chain = noise.process_server_hello(
                server_ephemeral=server.ephemeral,
                encrypted_static=server.static,
                encrypted_payload=server.payload,
                noise_static=noise_static,
            )
            verify_noise_certificate_chain(cert_chain)
            encrypted_payload = noise.encrypt(client_payload)
            await ws.send(frame_noise_payload(encode_client_finish(encrypted_static, encrypted_payload)))
            session = cls(ws, noise.finish())
            session.pending = pending
            return session
        except Exception:
            await ws.close()
            raise

    async def recv_node(self, *, timeout: float = 30.0) -> BinaryNode:
        if self.nodes:
            return self.nodes.pop(0)
        while not self.closed:
            value = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            if isinstance(value, str):
                raise CurrentProtocolAdapterError("WhatsApp returned an unexpected text WebSocket message")
            frames, self.pending = split_noise_frames(self.pending + bytes(value))
            for frame in frames:
                plaintext = self.transport.decrypt(frame)
                self.nodes.append(self.codec.decode(plaintext))
            if self.nodes:
                return self.nodes.pop(0)
        raise CurrentProtocolAdapterError("WhatsApp wire session is closed")

    async def send_node(self, node: BinaryNode) -> None:
        wire = self.codec.encode(node)
        encrypted = self.transport.encrypt(wire)
        async with self._send_lock:
            await self.ws.send(frame_noise_payload(encrypted))

    async def query(self, node: BinaryNode, *, timeout: float = 30.0) -> BinaryNode:
        if node.tag != "iq":
            raise CurrentProtocolAdapterError("Correlated WhatsApp query must be an iq node")
        if not node.attrs.get("id"):
            node.attrs["id"] = "pm-" + secrets.token_hex(8)
        stanza_id = node.attrs["id"]
        deferred: list[BinaryNode] = []
        async with self._query_lock:
            await self.send_node(node)
            try:
                async with asyncio.timeout(timeout):
                    while True:
                        current = await self.recv_node(timeout=timeout)
                        if current.attrs.get("id") == stanza_id:
                            return current
                        deferred.append(current)
            finally:
                if deferred:
                    self.nodes = deferred + self.nodes

    async def close(self) -> None:
        self.closed = True
        await self.ws.close()


SessionOpener = Callable[..., Awaitable[WhatsAppWireSession]]


def _new_credentials() -> ProtocolCredentials:
    identity = generate_curve_keypair()
    return ProtocolCredentials(
        noise=generate_curve_keypair(),
        identity=identity,
        signed_pre_key=generate_signed_pre_key(identity, 1),
        registration_id=generate_registration_id(),
        adv_secret_b64=base64.b64encode(os.urandom(32)).decode("ascii"),
    )


def _expires(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


class CurrentProtocolAdapter:
    def __init__(
        self,
        auth: EncryptedAuthStore,
        *,
        session_opener: SessionOpener = WhatsAppWireSession.open,
    ):
        self.auth = auth
        self.session_opener = session_opener
        self.session: WhatsAppWireSession | None = None
        self._pair_task: asyncio.Task | None = None
        self._qr_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._last_error: str | None = None
        self._connected = False

    def _load(self) -> ProtocolCredentials | None:
        value = self.auth.get_json("protocol", "credentials")
        return ProtocolCredentials.from_json(value) if value else None

    def _save(self, creds: ProtocolCredentials) -> None:
        self.auth.put_json("protocol", "credentials", creds.to_json())

    def _store_pre_key(self, key_id: int, pair: CurveKeyPair) -> None:
        self.auth.put_json("signal-pre-key", str(int(key_id)), {
            "private": base64.b64encode(pair.private).decode("ascii"),
            "public": base64.b64encode(pair.public).decode("ascii"),
        })

    def _load_pre_key(self, key_id: int) -> CurveKeyPair | None:
        value = self.auth.get_json("signal-pre-key", str(int(key_id)))
        if not value:
            return None
        try:
            private = base64.b64decode(str(value["private"]), validate=True)
            public = base64.b64decode(str(value["public"]), validate=True)
        except Exception as exc:
            raise CurrentProtocolAdapterError("Stored Signal pre-key is corrupt") from exc
        if len(private) != 32 or len(public) != 32:
            raise CurrentProtocolAdapterError("Stored Signal pre-key has invalid length")
        return CurveKeyPair(private, public)

    def _pending_pre_keys(self, creds: ProtocolCredentials, count: int) -> dict[int, CurveKeyPair]:
        start = int(creds.first_unuploaded_pre_key_id)
        target = start + int(count)
        while creds.next_pre_key_id < target:
            key_id = int(creds.next_pre_key_id)
            self._store_pre_key(key_id, generate_curve_keypair())
            creds.next_pre_key_id = key_id + 1
            self._save(creds)
        result: dict[int, CurveKeyPair] = {}
        for key_id in range(start, target):
            pair = self._load_pre_key(key_id)
            if pair is None:
                pair = generate_curve_keypair()
                self._store_pre_key(key_id, pair)
            result[key_id] = pair
        return result

    async def _ensure_server_pre_keys(self, session: WhatsAppWireSession, creds: ProtocolCredentials) -> int:
        count_response = await session.query(build_prekey_count_query(), timeout=30)
        server_count = parse_prekey_count(count_response)
        upload_count = 812 if server_count == 0 else (5 if server_count <= 5 else 0)
        if upload_count == 0:
            return server_count
        start = int(creds.first_unuploaded_pre_key_id)
        keys = self._pending_pre_keys(creds, upload_count)
        response = await session.query(
            build_prekey_upload(
                registration_id=creds.registration_id,
                identity_public=creds.identity.public,
                signed_pre_key=creds.signed_pre_key,
                pre_keys=keys,
            ),
            timeout=60,
        )
        if response.attrs.get("type") == "error" or response.child("error") is not None:
            raise CurrentProtocolAdapterError("WhatsApp rejected Signal pre-key upload")
        creds.first_unuploaded_pre_key_id = start + upload_count
        self._save(creds)
        return server_count + upload_count

    async def _stop_tasks(self) -> None:
        current = asyncio.current_task()
        for task in (self._pair_task, self._qr_task, self._keepalive_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        self._pair_task = None
        self._qr_task = None
        self._keepalive_task = None
        if self.session is not None:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None
        self._connected = False

    async def close(self) -> None:
        await self._stop_tasks()

    async def _registration_session(self, creds: ProtocolCredentials) -> WhatsAppWireSession:
        payload = build_registration_payload(
            RegistrationKeys(
                registration_id=creds.registration_id,
                identity_public_key=creds.identity.public,
                signed_pre_key=creds.signed_pre_key,
            )
        )
        return await self.session_opener(noise_static=creds.noise, client_payload=payload)

    async def _login_session(self, creds: ProtocolCredentials) -> WhatsAppWireSession:
        if not creds.jid:
            raise CurrentProtocolAdapterError("Stored WhatsApp session has no paired JID")
        jid = parse_jid(creds.jid)
        try:
            username = int(jid.user)
        except ValueError as exc:
            raise CurrentProtocolAdapterError("Stored WhatsApp JID user is not numeric") from exc
        payload = build_login_payload(username=username, device=int(jid.device or 0))
        return await self.session_opener(noise_static=creds.noise, client_payload=payload)

    @staticmethod
    def _pair_device(node: BinaryNode) -> BinaryNode | None:
        return node.child("pair-device") if node.tag == "iq" else None

    async def start_pairing(self) -> Mapping[str, Any]:
        await self._stop_tasks()
        creds = _new_credentials()
        self._save(creds)
        session = await self._registration_session(creds)
        self.session = session

        pair_iq: BinaryNode | None = None
        pair_device: BinaryNode | None = None
        for _ in range(24):
            node = await session.recv_node(timeout=20)
            candidate = self._pair_device(node)
            if candidate is not None:
                pair_iq, pair_device = node, candidate
                break
        if pair_iq is None or pair_device is None:
            await self._stop_tasks()
            raise CurrentProtocolAdapterError("WhatsApp did not provide pair-device QR references")

        stanza_id = pair_iq.attrs.get("id")
        if not stanza_id:
            await self._stop_tasks()
            raise CurrentProtocolAdapterError("pair-device stanza has no id")
        await session.send_node(BinaryNode("iq", {"to": "s.whatsapp.net", "type": "result", "id": stanza_id}))

        refs: list[str] = []
        for node in pair_device.children("ref"):
            value = node.content
            if isinstance(value, bytes):
                try:
                    text = value.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            elif isinstance(value, str):
                text = value
            else:
                continue
            if text:
                refs.append(text)
        if not refs:
            await self._stop_tasks()
            raise CurrentProtocolAdapterError("pair-device stanza contained no usable QR refs")

        def qr_for(ref: str) -> str:
            return build_pairing_qr_data(
                ref,
                creds.noise.public,
                creds.identity.public,
                creds.adv_secret_b64,
                os_name="Linux",
                browser_name="Chrome",
            )

        first_qr = qr_for(refs[0])
        self.auth.put_json("runtime", "pairing", {"qr": first_qr, "expires_at": _expires(60)})
        self._pair_task = asyncio.create_task(self._pair_success_loop(creds, session))
        self._qr_task = asyncio.create_task(self._rotate_qr(refs[1:], qr_for))
        self._keepalive_task = asyncio.create_task(self._keepalive(session))
        return {
            "ok": True,
            "qr": first_qr,
            "expires_at": _expires(60),
            "paired": False,
            "network_adapter": "clean_room_current_protocol",
            "private_material_exposed": False,
        }

    async def _rotate_qr(self, refs: list[str], qr_for: Callable[[str], str]) -> None:
        try:
            await asyncio.sleep(60)
            for ref in refs:
                qr = qr_for(ref)
                self.auth.put_json("runtime", "pairing", {"qr": qr, "expires_at": _expires(20)})
                await asyncio.sleep(20)
        except asyncio.CancelledError:
            return

    async def _pair_success_loop(self, creds: ProtocolCredentials, session: WhatsAppWireSession) -> None:
        try:
            while True:
                node = await session.recv_node(timeout=90)
                if node.tag in {"failure", "stream:error"}:
                    raise CurrentProtocolAdapterError(f"WhatsApp pairing failed with {node.tag}")
                pair_success = node.child("pair-success") if node.tag == "iq" else None
                if pair_success is None:
                    continue
                identity_node = pair_success.child("device-identity")
                device_node = pair_success.child("device")
                platform_node = pair_success.child("platform")
                if identity_node is None or not isinstance(identity_node.content, bytes) or device_node is None:
                    raise CurrentProtocolAdapterError("pair-success missing device identity/device")
                verified = verify_and_sign_pair_success_identity(
                    identity_node.content,
                    adv_secret_key=creds.adv_secret_b64,
                    signed_identity_key=creds.identity,
                )
                jid = device_node.attrs.get("jid")
                lid = device_node.attrs.get("lid")
                if not jid:
                    raise CurrentProtocolAdapterError("pair-success device has no JID")
                key_index = verified.device_identity.key_index
                if key_index is None:
                    raise CurrentProtocolAdapterError("pair-success device identity has no key index")
                reply = BinaryNode(
                    "iq",
                    {"to": "s.whatsapp.net", "type": "result", "id": node.attrs.get("id", "")},
                    [
                        BinaryNode(
                            "pair-device-sign",
                            {},
                            [
                                BinaryNode(
                                    "device-identity",
                                    {"key-index": str(key_index)},
                                    verified.encoded_for_reply,
                                )
                            ],
                        )
                    ],
                )
                await session.send_node(reply)
                creds.registered = True
                creds.jid = jid
                creds.lid = lid
                creds.platform = platform_node.attrs.get("name") if platform_node is not None else None
                creds.account_identity_b64 = base64.b64encode(
                    encode_signed_device_identity(verified.signed_identity, include_signature_key=True)
                ).decode("ascii")
                self._save(creds)
                self.auth.put_json("runtime", "identity", {"jid": jid, "lid": lid, "platform": creds.platform})
                self.auth.delete("runtime", "pairing")
                self._connected = False
                # WhatsApp normally asks the newly paired companion to restart/login.
                return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._connected = False

    async def _keepalive(self, session: WhatsAppWireSession) -> None:
        try:
            while not session.closed:
                await asyncio.sleep(20)
                await session.send_node(
                    BinaryNode(
                        "iq",
                        {"to": "s.whatsapp.net", "type": "get", "xmlns": "w:p", "id": "pm-" + secrets.token_hex(6)},
                        [BinaryNode("ping")],
                    )
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._connected = False

    async def reconnect(self) -> Mapping[str, Any]:
        await self._stop_tasks()
        creds = self._load()
        if creds is None or not creds.registered or not creds.jid:
            raise CurrentProtocolAdapterError("No paired WhatsApp session is stored")
        session = await self._login_session(creds)
        self.session = session
        for _ in range(32):
            node = await session.recv_node(timeout=20)
            if node.tag == "success":
                pre_key_count = await self._ensure_server_pre_keys(session, creds)
                self._connected = True
                self._keepalive_task = asyncio.create_task(self._keepalive(session))
                return {"ok": True, "connected": True, "paired": True, "server_pre_key_count": pre_key_count}
            if node.tag in {"failure", "stream:error"}:
                await self._stop_tasks()
                raise CurrentProtocolAdapterError(f"WhatsApp login failed with {node.tag}")
        await self._stop_tasks()
        raise CurrentProtocolAdapterError("WhatsApp login did not reach success state")

    def _require_live_session(self) -> tuple[WhatsAppWireSession, ProtocolCredentials]:
        creds = self._load()
        if creds is None or not creds.registered or not creds.jid:
            raise CurrentProtocolAdapterError("No paired WhatsApp session is stored")
        if not self._connected or self.session is None or self.session.closed:
            raise CurrentProtocolAdapterError("WhatsApp companion session is not connected; reconnect explicitly first")
        return self.session, creds

    async def _ensure_signal_sessions(
        self,
        wire: WhatsAppWireSession,
        creds: ProtocolCredentials,
        addresses: list[str],
    ) -> EncryptedSignalSessionStore:
        store = EncryptedSignalSessionStore(self.auth)
        missing = [address for address in dict.fromkeys(addresses) if store.load(address) is None]
        if not missing:
            return store
        response = await wire.query(build_session_query(missing), timeout=30)
        bundles = parse_session_bundles(response)
        for address in missing:
            bundle = bundles.get(address)
            if bundle is None:
                # Servers should echo the requested device JID exactly. Keep a conservative
                # normalized fallback for equivalent textual encodings only.
                target = str(parse_jid(address))
                bundle = next(
                    (value for key, value in bundles.items() if str(parse_jid(key)) == target),
                    None,
                )
            if bundle is None:
                raise CurrentProtocolAdapterError(f"WhatsApp returned no Signal pre-key bundle for {address}")
            signal = initialize_outgoing_session(
                our_identity=creds.identity,
                bundle=bundle,
                our_registration_id=creds.registration_id,
            )
            store.save(address, signal)
        return store

    async def _wait_message_ack(
        self,
        wire: WhatsAppWireSession,
        message_id: str,
        *,
        timeout: float = 30.0,
    ) -> BinaryNode:
        deferred: list[BinaryNode] = []
        try:
            async with asyncio.timeout(timeout):
                while True:
                    node = await wire.recv_node(timeout=timeout)
                    if node.tag == "ack" and node.attrs.get("id") == message_id:
                        if node.attrs.get("error") not in (None, "", "0"):
                            raise CurrentProtocolAdapterError(
                                f"WhatsApp rejected message {message_id} with error {node.attrs.get('error')}"
                            )
                        return node
                    if node.tag in {"failure", "stream:error"}:
                        raise CurrentProtocolAdapterError(f"WhatsApp send failed with {node.tag}")
                    deferred.append(node)
        finally:
            if deferred:
                wire.nodes = deferred + wire.nodes

    async def send_text(
        self,
        *,
        jid: str,
        text: str,
        reply_to_message_id: str | None = None,
        emit_read_receipt: bool = False,
    ) -> Mapping[str, Any]:
        """Send a direct 1:1 text through current USync + Signal multi-device fan-out.

        Group sender-key distribution is deliberately separate and remains fail-closed.
        """
        wire, creds = self._require_live_session()
        destination = parse_jid(jid).normalized_user()
        if destination.is_group:
            raise CurrentProtocolAdapterError("WhatsApp group send is not acceptance-complete")
        if destination.is_broadcast or destination.is_newsletter:
            raise CurrentProtocolAdapterError("WhatsApp broadcast/newsletter send is not supported by this v9.9 direct-send path")
        value = str(text)
        if not value:
            raise CurrentProtocolAdapterError("WhatsApp text cannot be empty")

        own_pn = parse_jid(creds.jid)
        own_lid = parse_jid(creds.lid) if creds.lid else None
        sender_identity = own_lid.normalized_user() if destination.is_lid and own_lid is not None else own_pn.normalized_user()
        usync = await wire.query(
            build_device_query([str(sender_identity), str(destination)], context="message"),
            timeout=30,
        )
        discovered = parse_device_result(
            usync,
            own_jid=creds.lid if destination.is_lid and creds.lid else creds.jid,
            prefer_lid=True,
        )

        own_users = {own_pn.user}
        if own_lid is not None:
            own_users.add(own_lid.user)
        own_device = int(own_pn.device or 0)
        targets = []
        for target in discovered:
            parsed = parse_jid(target.jid)
            # LID migration can change the user part, so parse_device_result's own_jid filter
            # alone cannot always recognize the current companion. Exclude it explicitly.
            if parsed.user in own_users and int(parsed.device or 0) == own_device:
                continue
            targets.append(target)
        if not targets:
            raise CurrentProtocolAdapterError("WhatsApp USync returned no target devices for direct send")

        target_jids = [target.jid for target in targets]
        sessions = await self._ensure_signal_sessions(wire, creds, target_jids)
        message_id = generate_message_id_v2(creds.jid)
        if reply_to_message_id:
            plain = encode_reply_text_message(
                value,
                stanza_id=str(reply_to_message_id),
                participant=str(destination),
                remote_jid=str(destination),
            )
        else:
            plain = encode_text_message(value)
        phash = participant_hash_v2(target_jids)
        dsm = encode_device_sent_message(str(destination), plain, phash=phash)

        participants: list[BinaryNode] = []
        include_device_identity = False
        own_count = 0
        remote_count = 0
        for target in targets:
            target_jid = target.jid
            target_user = parse_jid(target_jid).user
            is_own = target_user in own_users
            signal = sessions.load(target_jid)
            if signal is None:
                raise CurrentProtocolAdapterError(f"Signal session disappeared for {target_jid}")
            ciphertext_type, ciphertext = signal.encrypt(dsm if is_own else plain)
            sessions.save(target_jid, signal)
            participants.append(
                encrypted_participant_node(
                    target_jid,
                    ciphertext_type=ciphertext_type,
                    ciphertext=ciphertext,
                )
            )
            include_device_identity = include_device_identity or ciphertext_type == "pkmsg"
            if is_own:
                own_count += 1
            else:
                remote_count += 1

        device_identity: bytes | None = None
        if include_device_identity:
            if not creds.account_identity_b64:
                raise CurrentProtocolAdapterError("Paired WhatsApp credentials have no signed device identity")
            try:
                device_identity = base64.b64decode(creds.account_identity_b64, validate=True)
            except Exception as exc:
                raise CurrentProtocolAdapterError("Stored WhatsApp signed device identity is corrupt") from exc

        stanza = build_direct_message_stanza(
            destination_jid=str(destination),
            message_id=message_id,
            participants=participants,
            device_identity=device_identity,
            message_type="text",
        )
        await wire.send_node(stanza)
        ack = await self._wait_message_ack(wire, message_id, timeout=30)

        receipt_emitted = False
        if emit_read_receipt and reply_to_message_id:
            await wire.send_node(
                build_read_receipt(
                    destination_jid=str(destination),
                    message_id=str(reply_to_message_id),
                )
            )
            receipt_emitted = True

        return {
            "ok": True,
            "message_id": message_id,
            "to": str(destination),
            "server_ack": True,
            "ack_class": ack.attrs.get("class"),
            "device_fanout": len(participants),
            "own_device_targets": own_count,
            "remote_device_targets": remote_count,
            "used_prekey_message": include_device_identity,
            "read_receipt_emitted": receipt_emitted,
        }

    async def send_media(self, **kwargs) -> Mapping[str, Any]:
        raise CurrentProtocolAdapterError("WhatsApp media send layer is not acceptance-complete")

    async def list_groups(self) -> list[Mapping[str, Any]]:
        raise CurrentProtocolAdapterError("WhatsApp group synchronization is not acceptance-complete")

    def status(self) -> Mapping[str, Any]:
        creds = self._load()
        pairing = self.auth.get_json("runtime", "pairing") or {}
        return {
            "configured": True,
            "connected": self._connected,
            "paired": bool(creds and creds.registered and creds.jid),
            "pairing_pending": bool(pairing.get("qr")),
            "native_websocket": True,
            "signal_send_ready": True,
            "media_ready": False,
            "groups_ready": False,
            "last_error": self._last_error,
        }


__all__ = [
    "CurrentProtocolAdapterError", "ProtocolCredentials", "WhatsAppWireSession", "CurrentProtocolAdapter"
]
