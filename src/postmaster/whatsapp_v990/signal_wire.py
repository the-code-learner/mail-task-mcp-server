from __future__ import annotations

"""Clean-room Signal v3 wire messages used by the current WhatsApp libsignal path.

This module deliberately implements only the stable, observable wire envelope used by
WhatsApp's current ``libsignal`` repository: Signal/Whisper messages and
PreKeyWhisper messages. It does not claim to be a full Signal session implementation.
"""

from dataclasses import dataclass
import hashlib
import hmac

from .crypto import signal_public_key
from .proto import ProtoError, decode_fields, field_bytes, field_varint


SIGNAL_V3 = 3
SIGNAL_MAC_LENGTH = 8


class SignalWireError(ValueError):
    pass


def _version_byte(message_version: int = SIGNAL_V3, current_version: int = SIGNAL_V3) -> int:
    if not 0 <= int(message_version) <= 15 or not 0 <= int(current_version) <= 15:
        raise SignalWireError("Signal version nibbles must fit four bits")
    return ((int(message_version) & 0xF) << 4) | (int(current_version) & 0xF)


def _parse_version(value: int, *, current_version: int = SIGNAL_V3) -> int:
    high, low = (value >> 4) & 0xF, value & 0xF
    if low != current_version:
        raise SignalWireError(f"Unexpected Signal current-version nibble {low}")
    if high < SIGNAL_V3:
        raise SignalWireError(f"Legacy Signal message version {high}")
    if high > current_version:
        raise SignalWireError(f"Unsupported Signal message version {high}")
    return high


def _field_map(data: bytes) -> dict[int, list[int | bytes]]:
    try:
        fields = decode_fields(data)
    except ProtoError as exc:
        raise SignalWireError("Invalid Signal protobuf encoding") from exc
    out: dict[int, list[int | bytes]] = {}
    for field in fields:
        out.setdefault(field.number, []).append(field.value)
    return out


def _required_bytes(fields: dict[int, list[int | bytes]], number: int, name: str) -> bytes:
    values = fields.get(number)
    if not values or not isinstance(values[-1], bytes):
        raise SignalWireError(f"Missing Signal field {name}")
    return bytes(values[-1])


def _required_int(fields: dict[int, list[int | bytes]], number: int, name: str) -> int:
    values = fields.get(number)
    if not values or not isinstance(values[-1], int):
        raise SignalWireError(f"Missing Signal field {name}")
    value = int(values[-1])
    if not 0 <= value <= 0xFFFFFFFF:
        raise SignalWireError(f"Signal field {name} is outside uint32 range")
    return value


def _optional_int(fields: dict[int, list[int | bytes]], number: int) -> int | None:
    values = fields.get(number)
    if not values:
        return None
    if not isinstance(values[-1], int):
        raise SignalWireError(f"Signal field {number} must be varint")
    value = int(values[-1])
    if not 0 <= value <= 0xFFFFFFFF:
        raise SignalWireError(f"Signal field {number} is outside uint32 range")
    return value


def signal_message_mac(
    *,
    mac_key: bytes,
    sender_identity_key: bytes,
    receiver_identity_key: bytes,
    serialized_without_mac: bytes,
) -> bytes:
    if len(mac_key) != 32:
        raise SignalWireError("Signal MAC key must be 32 bytes")
    sender = signal_public_key(sender_identity_key)
    receiver = signal_public_key(receiver_identity_key)
    return hmac.new(bytes(mac_key), sender + receiver + bytes(serialized_without_mac), hashlib.sha256).digest()[:SIGNAL_MAC_LENGTH]


@dataclass(frozen=True, slots=True)
class SignalMessageV3:
    ratchet_key: bytes
    counter: int
    previous_counter: int
    ciphertext: bytes
    mac: bytes = b""
    message_version: int = SIGNAL_V3

    def protobuf(self) -> bytes:
        ratchet = signal_public_key(self.ratchet_key)
        return b"".join(
            (
                field_bytes(1, ratchet),
                field_varint(2, self.counter),
                field_varint(3, self.previous_counter),
                field_bytes(4, self.ciphertext),
            )
        )

    def unsigned_bytes(self) -> bytes:
        return bytes((_version_byte(self.message_version),)) + self.protobuf()

    def serialize(
        self,
        *,
        mac_key: bytes,
        sender_identity_key: bytes,
        receiver_identity_key: bytes,
    ) -> bytes:
        unsigned = self.unsigned_bytes()
        mac = signal_message_mac(
            mac_key=mac_key,
            sender_identity_key=sender_identity_key,
            receiver_identity_key=receiver_identity_key,
            serialized_without_mac=unsigned,
        )
        return unsigned + mac

    @classmethod
    def parse(cls, raw: bytes) -> "SignalMessageV3":
        data = bytes(raw)
        if len(data) < 1 + SIGNAL_MAC_LENGTH:
            raise SignalWireError("Signal message is too short")
        version = _parse_version(data[0])
        proto = data[1:-SIGNAL_MAC_LENGTH]
        mac = data[-SIGNAL_MAC_LENGTH:]
        fields = _field_map(proto)
        ratchet = _required_bytes(fields, 1, "ratchetKey")
        if len(ratchet) != 33 or ratchet[0] != 5:
            raise SignalWireError("Signal ratchet key must use 0x05-prefixed Curve25519 encoding")
        return cls(
            ratchet_key=ratchet,
            counter=_required_int(fields, 2, "counter"),
            previous_counter=_optional_int(fields, 3) or 0,
            ciphertext=_required_bytes(fields, 4, "ciphertext"),
            mac=mac,
            message_version=version,
        )

    def verify_mac(self, *, mac_key: bytes, sender_identity_key: bytes, receiver_identity_key: bytes) -> bool:
        expected = signal_message_mac(
            mac_key=mac_key,
            sender_identity_key=sender_identity_key,
            receiver_identity_key=receiver_identity_key,
            serialized_without_mac=self.unsigned_bytes(),
        )
        return hmac.compare_digest(self.mac, expected)


@dataclass(frozen=True, slots=True)
class PreKeyWhisperMessageV3:
    registration_id: int
    pre_key_id: int | None
    signed_pre_key_id: int
    base_key: bytes
    identity_key: bytes
    message: bytes
    message_version: int = SIGNAL_V3

    def protobuf(self) -> bytes:
        parts: list[bytes] = []
        if self.pre_key_id is not None:
            parts.append(field_varint(1, self.pre_key_id))
        parts.extend(
            (
                field_bytes(2, signal_public_key(self.base_key)),
                field_bytes(3, signal_public_key(self.identity_key)),
                field_bytes(4, self.message),
                field_varint(5, self.registration_id),
                field_varint(6, self.signed_pre_key_id),
            )
        )
        return b"".join(parts)

    def serialize(self) -> bytes:
        return bytes((_version_byte(self.message_version),)) + self.protobuf()

    @classmethod
    def parse(cls, raw: bytes) -> "PreKeyWhisperMessageV3":
        data = bytes(raw)
        if len(data) < 2:
            raise SignalWireError("PreKeyWhisper message is too short")
        version = _parse_version(data[0])
        fields = _field_map(data[1:])
        base = _required_bytes(fields, 2, "baseKey")
        ident = _required_bytes(fields, 3, "identityKey")
        if len(base) != 33 or base[0] != 5 or len(ident) != 33 or ident[0] != 5:
            raise SignalWireError("PreKeyWhisper keys must use 0x05-prefixed Curve25519 encoding")
        return cls(
            registration_id=_required_int(fields, 5, "registrationId"),
            pre_key_id=_optional_int(fields, 1),
            signed_pre_key_id=_required_int(fields, 6, "signedPreKeyId"),
            base_key=base,
            identity_key=ident,
            message=_required_bytes(fields, 4, "message"),
            message_version=version,
        )
