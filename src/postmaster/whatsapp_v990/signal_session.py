from __future__ import annotations

"""Clean-room legacy Signal-v3 Double Ratchet session used by WhatsApp Web.

This implements the X3DH + Whisper v3 session semantics needed by the current WhatsApp
wire envelope. State is serializable for encrypted-at-rest persistence; network/device
discovery is intentionally outside this module.
"""

from dataclasses import dataclass, field
import base64
import hashlib
import hmac
from typing import Any, Mapping

from .crypto import CurveKeyPair, aes_cbc_decrypt, aes_cbc_encrypt, curve_shared_key, generate_curve_keypair, hkdf_sha256
from .signal_keys import (
    SignalPreKeyBundle,
    SignedPreKey,
    derive_x3dh_initiator,
    derive_x3dh_responder,
    verify_signed_pre_key,
)
from .signal_wire import PreKeyWhisperMessageV3, SignalMessageV3


class SignalSessionError(ValueError):
    pass


MAX_FUTURE_MESSAGES = 2000
MAX_STORED_MESSAGE_KEYS = 2000


def _raw_public(value: bytes) -> bytes:
    raw = bytes(value)
    if len(raw) == 33 and raw[0] == 5:
        raw = raw[1:]
    if len(raw) != 32:
        raise SignalSessionError("Signal Curve25519 public key must be 32 bytes or 0x05-prefixed 33 bytes")
    return raw


def _b64(raw: bytes) -> str:
    return base64.b64encode(bytes(raw)).decode("ascii")


def _unb64(value: Any, *, length: int | None = None) -> bytes:
    try:
        raw = base64.b64decode(str(value), validate=True)
    except Exception as exc:
        raise SignalSessionError("Invalid Base64 in Signal session state") from exc
    if length is not None and len(raw) != length:
        raise SignalSessionError(f"Signal session value must be {length} bytes")
    return raw


def _root_chain(root_key: bytes, our_private: bytes, their_public: bytes) -> tuple[bytes, bytes]:
    if len(root_key) != 32:
        raise SignalSessionError("Signal root key must be 32 bytes")
    shared = curve_shared_key(our_private, _raw_public(their_public))
    material = hkdf_sha256(shared, 64, salt=root_key, info=b"WhisperRatchet")
    return material[:32], material[32:]


def _message_seed(chain_key: bytes) -> bytes:
    return hmac.new(bytes(chain_key), b"\x01", hashlib.sha256).digest()


def _next_chain_key(chain_key: bytes) -> bytes:
    return hmac.new(bytes(chain_key), b"\x02", hashlib.sha256).digest()


def _message_keys_from_seed(seed: bytes) -> tuple[bytes, bytes, bytes]:
    if len(seed) != 32:
        raise SignalSessionError("Signal message seed must be 32 bytes")
    material = hkdf_sha256(seed, 80, salt=bytes(32), info=b"WhisperMessageKeys")
    return material[:32], material[32:64], material[64:80]


@dataclass(slots=True)
class ChainState:
    key: bytes | None
    counter: int = -1
    skipped: dict[int, bytes] = field(default_factory=dict)
    sending: bool = False

    def fill_to(self, target: int) -> None:
        if target < 0:
            raise SignalSessionError("Signal message counter cannot be negative")
        if self.counter >= target:
            return
        if self.key is None:
            raise SignalSessionError("Signal chain is closed")
        if target - self.counter > MAX_FUTURE_MESSAGES:
            raise SignalSessionError("Signal message is over 2000 counters into the future")
        while self.counter < target:
            assert self.key is not None
            next_counter = self.counter + 1
            self.skipped[next_counter] = _message_seed(self.key)
            self.key = _next_chain_key(self.key)
            self.counter = next_counter
            if len(self.skipped) > MAX_STORED_MESSAGE_KEYS:
                oldest = min(self.skipped)
                del self.skipped[oldest]

    def take_seed(self, counter: int) -> bytes:
        self.fill_to(counter)
        try:
            return self.skipped.pop(counter)
        except KeyError as exc:
            raise SignalSessionError("Signal message key was already used or never derived") from exc

    def close(self) -> None:
        self.key = None

    def to_json(self) -> dict[str, Any]:
        return {
            "key": _b64(self.key) if self.key is not None else None,
            "counter": self.counter,
            "skipped": {str(k): _b64(v) for k, v in self.skipped.items()},
            "sending": self.sending,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "ChainState":
        key = _unb64(value["key"], length=32) if value.get("key") else None
        skipped = {int(k): _unb64(v, length=32) for k, v in dict(value.get("skipped") or {}).items()}
        return cls(key=key, counter=int(value.get("counter", -1)), skipped=skipped, sending=bool(value.get("sending")))


@dataclass(slots=True)
class PendingPreKey:
    base_key: bytes
    signed_pre_key_id: int
    pre_key_id: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {"base_key": _b64(self.base_key), "signed_pre_key_id": self.signed_pre_key_id, "pre_key_id": self.pre_key_id}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "PendingPreKey":
        return cls(_unb64(value["base_key"], length=32), int(value["signed_pre_key_id"]), int(value["pre_key_id"]) if value.get("pre_key_id") is not None else None)


@dataclass(slots=True)
class SignalSession:
    registration_id: int
    local_registration_id: int
    local_identity: CurveKeyPair
    remote_identity: bytes
    root_key: bytes
    ratchet_key: CurveKeyPair
    last_remote_ratchet: bytes
    previous_counter: int = 0
    chains: dict[str, ChainState] = field(default_factory=dict)
    pending_pre_key: PendingPreKey | None = None
    base_key: bytes = b""

    @staticmethod
    def _chain_id(public: bytes) -> str:
        return _raw_public(public).hex()

    def chain(self, public: bytes) -> ChainState | None:
        return self.chains.get(self._chain_id(public))

    def set_chain(self, public: bytes, chain: ChainState) -> None:
        self.chains[self._chain_id(public)] = chain

    def delete_chain(self, public: bytes) -> None:
        self.chains.pop(self._chain_id(public), None)

    def _create_chain(self, remote_public: bytes, *, sending: bool) -> None:
        root, chain_key = _root_chain(self.root_key, self.ratchet_key.private, remote_public)
        self.root_key = root
        key_id = self.ratchet_key.public if sending else _raw_public(remote_public)
        self.set_chain(key_id, ChainState(chain_key, counter=-1, sending=sending))

    def maybe_step_ratchet(self, remote_public: bytes, previous_counter: int) -> None:
        remote = _raw_public(remote_public)
        if self.chain(remote) is not None:
            return
        previous = self.chain(self.last_remote_ratchet)
        if previous is not None:
            previous.fill_to(previous_counter)
            previous.close()

        # Receiving chain: DH(current local ratchet, new remote ratchet).
        self._create_chain(remote, sending=False)

        old_sending = self.chain(self.ratchet_key.public)
        if old_sending is not None:
            self.previous_counter = old_sending.counter
            self.delete_chain(self.ratchet_key.public)

        # Rotate our ratchet and derive the next sending chain from the new remote key.
        self.ratchet_key = generate_curve_keypair()
        self._create_chain(remote, sending=True)
        self.last_remote_ratchet = remote

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        chain = self.chain(self.ratchet_key.public)
        if chain is None or not chain.sending:
            raise SignalSessionError("Signal session has no sending chain")
        counter = chain.counter + 1
        seed = chain.take_seed(counter)
        cipher_key, mac_key, iv = _message_keys_from_seed(seed)
        message = SignalMessageV3(
            ratchet_key=self.ratchet_key.public,
            counter=counter,
            previous_counter=max(0, self.previous_counter),
            ciphertext=aes_cbc_encrypt(cipher_key, iv, bytes(plaintext)),
        )
        serialized = message.serialize(
            mac_key=mac_key,
            sender_identity_key=self.local_identity.public,
            receiver_identity_key=self.remote_identity,
        )
        if self.pending_pre_key is None:
            return "msg", serialized
        pending = self.pending_pre_key
        wrapped = PreKeyWhisperMessageV3(
            registration_id=self.local_registration_id,
            pre_key_id=pending.pre_key_id,
            signed_pre_key_id=pending.signed_pre_key_id,
            base_key=pending.base_key,
            identity_key=self.local_identity.public,
            message=serialized,
        ).serialize()
        return "pkmsg", wrapped

    def decrypt_signal(self, raw: bytes) -> bytes:
        message = SignalMessageV3.parse(raw)
        self.maybe_step_ratchet(message.ratchet_key, message.previous_counter)
        chain = self.chain(message.ratchet_key)
        if chain is None or chain.sending:
            raise SignalSessionError("Signal session has no receiving chain for message ratchet key")
        seed = chain.take_seed(message.counter)
        cipher_key, mac_key, iv = _message_keys_from_seed(seed)
        if not message.verify_mac(
            mac_key=mac_key,
            sender_identity_key=self.remote_identity,
            receiver_identity_key=self.local_identity.public,
        ):
            raise SignalSessionError("Signal message MAC mismatch")
        try:
            plaintext = aes_cbc_decrypt(cipher_key, iv, message.ciphertext)
        except Exception as exc:
            raise SignalSessionError("Signal message decryption failed") from exc
        self.pending_pre_key = None
        return plaintext

    def to_json(self) -> dict[str, Any]:
        return {
            "registration_id": self.registration_id,
            "local_registration_id": self.local_registration_id,
            "local_identity_private": _b64(self.local_identity.private),
            "local_identity_public": _b64(self.local_identity.public),
            "remote_identity": _b64(_raw_public(self.remote_identity)),
            "root_key": _b64(self.root_key),
            "ratchet_private": _b64(self.ratchet_key.private),
            "ratchet_public": _b64(self.ratchet_key.public),
            "last_remote_ratchet": _b64(_raw_public(self.last_remote_ratchet)),
            "previous_counter": self.previous_counter,
            "chains": {k: v.to_json() for k, v in self.chains.items()},
            "pending_pre_key": self.pending_pre_key.to_json() if self.pending_pre_key else None,
            "base_key": _b64(_raw_public(self.base_key)) if self.base_key else "",
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "SignalSession":
        local = CurveKeyPair(_unb64(value["local_identity_private"], length=32), _unb64(value["local_identity_public"], length=32))
        ratchet = CurveKeyPair(_unb64(value["ratchet_private"], length=32), _unb64(value["ratchet_public"], length=32))
        pending_raw = value.get("pending_pre_key")
        return cls(
            registration_id=int(value["registration_id"]),
            local_registration_id=int(value.get("local_registration_id", 0)),
            local_identity=local,
            remote_identity=_unb64(value["remote_identity"], length=32),
            root_key=_unb64(value["root_key"], length=32),
            ratchet_key=ratchet,
            last_remote_ratchet=_unb64(value["last_remote_ratchet"], length=32),
            previous_counter=int(value.get("previous_counter", 0)),
            chains={str(k): ChainState.from_json(v) for k, v in dict(value.get("chains") or {}).items()},
            pending_pre_key=PendingPreKey.from_json(pending_raw) if isinstance(pending_raw, Mapping) else None,
            base_key=_unb64(value["base_key"], length=32) if value.get("base_key") else b"",
        )


def initialize_outgoing_session(
    *,
    our_identity: CurveKeyPair,
    bundle: SignalPreKeyBundle,
    our_registration_id: int,
    base_key: CurveKeyPair | None = None,
    ratchet_key: CurveKeyPair | None = None,
) -> SignalSession:
    if not verify_signed_pre_key(bundle.identity_key, bundle.signed_pre_key, bundle.signed_pre_key_signature):
        raise SignalSessionError("Remote Signal signed pre-key signature is invalid")
    base = base_key or generate_curve_keypair()
    root, _initial_chain = derive_x3dh_initiator(
        our_identity_private=our_identity.private,
        our_base_private=base.private,
        their_identity_public=bundle.identity_key,
        their_signed_pre_key_public=bundle.signed_pre_key,
        their_one_time_pre_key_public=bundle.pre_key,
    )
    local_ratchet = ratchet_key or generate_curve_keypair()
    session = SignalSession(
        registration_id=int(bundle.registration_id),
        local_registration_id=int(our_registration_id),
        local_identity=our_identity,
        remote_identity=_raw_public(bundle.identity_key),
        root_key=root,
        ratchet_key=local_ratchet,
        last_remote_ratchet=_raw_public(bundle.signed_pre_key),
        base_key=base.public,
        pending_pre_key=PendingPreKey(base.public, int(bundle.signed_pre_key_id), bundle.pre_key_id),
    )
    session._create_chain(bundle.signed_pre_key, sending=True)
    return session


def initialize_incoming_session(
    *,
    our_identity: CurveKeyPair,
    our_signed_pre_key: SignedPreKey,
    message: PreKeyWhisperMessageV3,
    our_one_time_pre_key: CurveKeyPair | None = None,
    our_registration_id: int = 0,
) -> SignalSession:
    if message.signed_pre_key_id != our_signed_pre_key.key_id:
        raise SignalSessionError("Incoming Signal pre-key message references an unknown signed pre-key")
    if message.pre_key_id is not None and our_one_time_pre_key is None:
        raise SignalSessionError("Incoming Signal pre-key message requires a one-time pre-key")
    root, _initial_chain = derive_x3dh_responder(
        our_identity_private=our_identity.private,
        our_signed_pre_key_private=our_signed_pre_key.key_pair.private,
        their_identity_public=message.identity_key,
        their_base_public=message.base_key,
        our_one_time_pre_key_private=our_one_time_pre_key.private if our_one_time_pre_key else None,
    )
    return SignalSession(
        registration_id=int(message.registration_id),
        local_registration_id=int(our_registration_id),
        local_identity=our_identity,
        remote_identity=_raw_public(message.identity_key),
        root_key=root,
        ratchet_key=our_signed_pre_key.key_pair,
        last_remote_ratchet=_raw_public(message.base_key),
        base_key=_raw_public(message.base_key),
    )


def decrypt_prekey_message(
    raw: bytes,
    *,
    our_identity: CurveKeyPair,
    our_signed_pre_key: SignedPreKey,
    our_one_time_pre_key: CurveKeyPair | None = None,
    our_registration_id: int = 0,
) -> tuple[SignalSession, bytes, int | None]:
    envelope = PreKeyWhisperMessageV3.parse(raw)
    session = initialize_incoming_session(
        our_identity=our_identity,
        our_signed_pre_key=our_signed_pre_key,
        message=envelope,
        our_one_time_pre_key=our_one_time_pre_key,
        our_registration_id=our_registration_id,
    )
    plaintext = session.decrypt_signal(envelope.message)
    return session, plaintext, envelope.pre_key_id


class EncryptedSignalSessionStore:
    """Persist Signal session JSON inside EncryptedAuthStore without exposing private material."""

    def __init__(self, encrypted_store: Any):
        self.store = encrypted_store

    @staticmethod
    def _name(address: str) -> str:
        clean = str(address or "").strip()
        if not clean:
            raise SignalSessionError("Signal session address is required")
        return clean

    def load(self, address: str) -> SignalSession | None:
        raw = self.store.get_json("signal-session", self._name(address))
        return SignalSession.from_json(raw) if raw else None

    def save(self, address: str, session: SignalSession) -> None:
        self.store.put_json("signal-session", self._name(address), session.to_json())

    def delete(self, address: str) -> bool:
        return bool(self.store.delete("signal-session", self._name(address)))


__all__ = [
    "SignalSessionError", "ChainState", "PendingPreKey", "SignalSession",
    "initialize_outgoing_session", "initialize_incoming_session", "decrypt_prekey_message",
    "EncryptedSignalSessionStore", "MAX_FUTURE_MESSAGES",
]
