from __future__ import annotations

"""Clean-room Signal SenderKey v3 primitives for WhatsApp group messaging.

Wire format and derivation follow the current public Signal/WhatsApp sender-key protocol:
- version byte 0x33,
- HMAC-SHA256 chain/message seeds,
- HKDF-SHA256 info "WhisperGroup",
- AES-CBC with PKCS#7,
- XEdDSA signatures,
- bounded skipped-message-key cache.

State is persisted through Postmaster's encrypted auth store and never logged.
"""

from dataclasses import dataclass, field
import base64
import hashlib
import hmac
import os
import secrets
from typing import Any

from .crypto import (
    CurveKeyPair,
    aes_cbc_decrypt,
    aes_cbc_encrypt,
    generate_curve_keypair,
    hkdf_sha256,
    signal_public_key,
    xeddsa_sign,
    xeddsa_verify,
)
from .proto import ProtoError, decode_fields, field_bytes, field_message, field_varint
from .store import EncryptedAuthStore

SENDER_KEY_VERSION = 3
SENDER_KEY_VERSION_BYTE = 0x33
MAX_SENDER_MESSAGE_KEYS = 2000
MAX_SENDER_KEY_STATES = 5


class SenderKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SenderMessageKey:
    iteration: int
    seed: bytes

    @property
    def material(self) -> bytes:
        if len(self.seed) != 32:
            raise SenderKeyError("Sender message-key seed must be 32 bytes")
        return hkdf_sha256(self.seed, 64, salt=bytes(32), info=b"WhisperGroup")

    @property
    def iv(self) -> bytes:
        return self.material[:16]

    @property
    def cipher_key(self) -> bytes:
        return self.material[16:48]


@dataclass(slots=True)
class SenderKeyState:
    key_id: int
    iteration: int
    chain_key: bytes
    signing_public: bytes
    signing_private: bytes | None = None
    skipped: dict[int, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= int(self.key_id) <= 0x7FFFFFFF:
            raise SenderKeyError("Sender-key id must fit signed 31 bits")
        if int(self.iteration) < 0:
            raise SenderKeyError("Sender-key iteration must be non-negative")
        if len(self.chain_key) != 32:
            raise SenderKeyError("Sender chain key must be 32 bytes")
        public = bytes(self.signing_public)
        if len(public) == 33 and public[0] == 5:
            public = public[1:]
        if len(public) != 32:
            raise SenderKeyError("Sender signing public key must be 32 bytes or 0x05-prefixed 33 bytes")
        self.signing_public = public
        if self.signing_private is not None and len(self.signing_private) != 32:
            raise SenderKeyError("Sender signing private key must be 32 bytes")


@dataclass(frozen=True, slots=True)
class SenderKeyDistribution:
    key_id: int
    iteration: int
    chain_key: bytes
    signing_public: bytes

    def serialize(self) -> bytes:
        public = signal_public_key(self.signing_public)
        body = b"".join(
            (
                field_varint(1, self.key_id),
                field_varint(2, self.iteration),
                field_bytes(3, self.chain_key),
                field_bytes(4, public),
            )
        )
        return bytes((SENDER_KEY_VERSION_BYTE,)) + body

    @classmethod
    def parse(cls, raw: bytes) -> "SenderKeyDistribution":
        data = bytes(raw)
        if len(data) < 2 or data[0] != SENDER_KEY_VERSION_BYTE:
            raise SenderKeyError("Unsupported sender-key distribution version")
        fields = _field_map(data[1:])
        key_id = _required_int(fields, 1, "id")
        iteration = _required_int(fields, 2, "iteration")
        chain = _required_bytes(fields, 3, "chainKey")
        public = _required_bytes(fields, 4, "signingKey")
        if len(chain) != 32:
            raise SenderKeyError("Sender-key distribution chain key must be 32 bytes")
        if len(public) == 33 and public[0] == 5:
            public = public[1:]
        if len(public) != 32:
            raise SenderKeyError("Sender-key distribution signing key has invalid length")
        return cls(key_id, iteration, chain, public)


@dataclass(frozen=True, slots=True)
class SenderKeyMessage:
    key_id: int
    iteration: int
    ciphertext: bytes
    signature: bytes

    @property
    def unsigned(self) -> bytes:
        body = b"".join(
            (
                field_varint(1, self.key_id),
                field_varint(2, self.iteration),
                field_bytes(3, self.ciphertext),
            )
        )
        return bytes((SENDER_KEY_VERSION_BYTE,)) + body

    def serialize(self) -> bytes:
        if len(self.signature) != 64:
            raise SenderKeyError("Sender-key message signature must be 64 bytes")
        return self.unsigned + self.signature

    @classmethod
    def create(cls, *, key_id: int, iteration: int, ciphertext: bytes, signing_private: bytes) -> "SenderKeyMessage":
        placeholder = cls(key_id, iteration, bytes(ciphertext), b"")
        signature = xeddsa_sign(signing_private, placeholder.unsigned)
        return cls(key_id, iteration, bytes(ciphertext), signature)

    @classmethod
    def parse(cls, raw: bytes) -> "SenderKeyMessage":
        data = bytes(raw)
        if len(data) < 1 + 64 or data[0] != SENDER_KEY_VERSION_BYTE:
            raise SenderKeyError("Unsupported or truncated sender-key message")
        body, signature = data[1:-64], data[-64:]
        fields = _field_map(body)
        return cls(
            _required_int(fields, 1, "id"),
            _required_int(fields, 2, "iteration"),
            _required_bytes(fields, 3, "ciphertext"),
            signature,
        )

    def verify(self, signing_public: bytes) -> bool:
        return xeddsa_verify(signing_public, self.unsigned, self.signature)


def _field_map(raw: bytes) -> dict[int, list[int | bytes]]:
    try:
        fields = decode_fields(bytes(raw))
    except ProtoError as exc:
        raise SenderKeyError("Invalid sender-key protobuf") from exc
    out: dict[int, list[int | bytes]] = {}
    for item in fields:
        out.setdefault(item.number, []).append(item.value)
    return out


def _required_int(fields: dict[int, list[int | bytes]], number: int, name: str) -> int:
    values = fields.get(number) or []
    if not values or not isinstance(values[-1], int):
        raise SenderKeyError(f"Missing sender-key integer field {name}")
    return int(values[-1])


def _required_bytes(fields: dict[int, list[int | bytes]], number: int, name: str) -> bytes:
    values = fields.get(number) or []
    if not values or not isinstance(values[-1], bytes):
        raise SenderKeyError(f"Missing sender-key bytes field {name}")
    return bytes(values[-1])


def _derive(seed: bytes, selector: bytes) -> bytes:
    if len(seed) != 32:
        raise SenderKeyError("Sender chain key must be 32 bytes")
    return hmac.new(seed, selector, hashlib.sha256).digest()


def _message_seed(chain_key: bytes) -> bytes:
    return _derive(chain_key, b"\x01")


def _next_chain_key(chain_key: bytes) -> bytes:
    return _derive(chain_key, b"\x02")


def _cache_skipped(state: SenderKeyState, iteration: int, seed: bytes) -> None:
    state.skipped[int(iteration)] = bytes(seed)
    while len(state.skipped) > MAX_SENDER_MESSAGE_KEYS:
        oldest = min(state.skipped)
        state.skipped.pop(oldest, None)


def _message_key_for(state: SenderKeyState, iteration: int) -> SenderMessageKey:
    target = int(iteration)
    if target < 0:
        raise SenderKeyError("Sender-key message iteration must be non-negative")
    current = int(state.iteration)
    if current > target:
        seed = state.skipped.pop(target, None)
        if seed is None:
            raise SenderKeyError(f"Received sender-key message with old counter {target}; current is {current}")
        return SenderMessageKey(target, seed)
    if target - current > MAX_SENDER_MESSAGE_KEYS:
        raise SenderKeyError("Sender-key message is over 2000 iterations into the future")

    while current < target:
        _cache_skipped(state, current, _message_seed(state.chain_key))
        state.chain_key = _next_chain_key(state.chain_key)
        current += 1
        state.iteration = current

    seed = _message_seed(state.chain_key)
    state.chain_key = _next_chain_key(state.chain_key)
    state.iteration = target + 1
    return SenderMessageKey(target, seed)


def new_sender_key_state() -> SenderKeyState:
    pair = generate_curve_keypair()
    return SenderKeyState(
        key_id=secrets.randbelow(0x7FFFFFFF),
        iteration=0,
        chain_key=os.urandom(32),
        signing_public=pair.public,
        signing_private=pair.private,
    )


class EncryptedSenderKeyStore:
    def __init__(self, auth: EncryptedAuthStore):
        self.auth = auth

    @staticmethod
    def name(group_id: str, author_jid: str) -> str:
        return f"{group_id}::{author_jid}"

    def load(self, group_id: str, author_jid: str) -> SenderKeyState | None:
        value = self.auth.get_json("signal-sender-key", self.name(group_id, author_jid))
        if not value:
            return None

        def raw(name: str, *, optional: bool = False) -> bytes | None:
            item = value.get(name)
            if item in (None, "") and optional:
                return None
            try:
                return base64.b64decode(str(item), validate=True)
            except Exception as exc:
                raise SenderKeyError(f"Stored sender-key field {name} is invalid") from exc

        skipped: dict[int, bytes] = {}
        for item in value.get("skipped") or []:
            if not isinstance(item, dict):
                continue
            try:
                skipped[int(item["iteration"])] = base64.b64decode(str(item["seed"]), validate=True)
            except Exception as exc:
                raise SenderKeyError("Stored skipped sender-key message key is invalid") from exc

        return SenderKeyState(
            key_id=int(value["key_id"]),
            iteration=int(value["iteration"]),
            chain_key=raw("chain_key") or b"",
            signing_public=raw("signing_public") or b"",
            signing_private=raw("signing_private", optional=True),
            skipped=skipped,
        )

    def save(self, group_id: str, author_jid: str, state: SenderKeyState) -> None:
        def b64(value: bytes | None) -> str | None:
            return base64.b64encode(value).decode("ascii") if value is not None else None
        self.auth.put_json(
            "signal-sender-key",
            self.name(group_id, author_jid),
            {
                "key_id": state.key_id,
                "iteration": state.iteration,
                "chain_key": b64(state.chain_key),
                "signing_public": b64(state.signing_public),
                "signing_private": b64(state.signing_private),
                "skipped": [
                    {"iteration": iteration, "seed": b64(seed)}
                    for iteration, seed in sorted(state.skipped.items())
                ],
            },
        )

    def ensure_own(self, group_id: str, author_jid: str) -> SenderKeyState:
        state = self.load(group_id, author_jid)
        if state is None:
            state = new_sender_key_state()
            self.save(group_id, author_jid, state)
        if state.signing_private is None:
            raise SenderKeyError("Stored sender-key state is receive-only and cannot encrypt")
        return state

    def distribution(self, group_id: str, author_jid: str) -> bytes:
        state = self.ensure_own(group_id, author_jid)
        return SenderKeyDistribution(
            state.key_id,
            state.iteration,
            state.chain_key,
            state.signing_public,
        ).serialize()

    def process_distribution(self, group_id: str, author_jid: str, raw: bytes) -> SenderKeyState:
        incoming = SenderKeyDistribution.parse(raw)
        state = SenderKeyState(
            key_id=incoming.key_id,
            iteration=incoming.iteration,
            chain_key=incoming.chain_key,
            signing_public=incoming.signing_public,
            signing_private=None,
        )
        self.save(group_id, author_jid, state)
        return state

    def encrypt(self, group_id: str, author_jid: str, plaintext: bytes) -> bytes:
        state = self.ensure_own(group_id, author_jid)
        # Mirror the current libsignal sender-key iteration behavior used by WA Web.
        target_iteration = 0 if state.iteration == 0 else state.iteration + 1
        message_key = _message_key_for(state, target_iteration)
        ciphertext = aes_cbc_encrypt(message_key.cipher_key, message_key.iv, bytes(plaintext))
        assert state.signing_private is not None
        message = SenderKeyMessage.create(
            key_id=state.key_id,
            iteration=message_key.iteration,
            ciphertext=ciphertext,
            signing_private=state.signing_private,
        )
        self.save(group_id, author_jid, state)
        return message.serialize()

    def decrypt(self, group_id: str, author_jid: str, raw: bytes) -> bytes:
        message = SenderKeyMessage.parse(raw)
        state = self.load(group_id, author_jid)
        if state is None or state.key_id != message.key_id:
            raise SenderKeyError("No matching sender-key state for group message")
        if not message.verify(state.signing_public):
            raise SenderKeyError("Invalid sender-key message signature")
        message_key = _message_key_for(state, message.iteration)
        try:
            plaintext = aes_cbc_decrypt(message_key.cipher_key, message_key.iv, message.ciphertext)
        except Exception as exc:
            raise SenderKeyError("Invalid sender-key ciphertext") from exc
        self.save(group_id, author_jid, state)
        return plaintext


def encode_sender_key_distribution_message(group_id: str, distribution: bytes) -> bytes:
    """Encode WAProto.Message.senderKeyDistributionMessage (Message field 2)."""
    nested = b"".join((field_bytes(1, str(group_id)), field_bytes(2, bytes(distribution))))
    return field_message(2, [nested])


def decode_sender_key_distribution_message(message: bytes) -> tuple[str, bytes] | None:
    try:
        fields = decode_fields(bytes(message))
    except ProtoError as exc:
        raise SenderKeyError("Invalid WhatsApp Message protobuf") from exc
    candidates = [field.value for field in fields if field.number == 2 and isinstance(field.value, bytes)]
    if not candidates:
        return None
    nested = _field_map(bytes(candidates[-1]))
    group_raw = _required_bytes(nested, 1, "groupId")
    distribution = _required_bytes(nested, 2, "axolotlSenderKeyDistributionMessage")
    try:
        group_id = group_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SenderKeyError("Sender-key group id is not UTF-8") from exc
    return group_id, distribution


__all__ = [
    "SENDER_KEY_VERSION",
    "SENDER_KEY_VERSION_BYTE",
    "SenderKeyError",
    "SenderMessageKey",
    "SenderKeyState",
    "SenderKeyDistribution",
    "SenderKeyMessage",
    "EncryptedSenderKeyStore",
    "new_sender_key_state",
    "encode_sender_key_distribution_message",
    "decode_sender_key_distribution_message",
]
