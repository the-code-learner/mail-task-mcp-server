from __future__ import annotations

"""Minimal WhatsApp 1:1 message protobuf and WABinary stanza builders."""

import base64
import hashlib
import os
import time
from typing import Iterable

from .binary import BinaryNode
from .jid import parse_jid
from .proto import field_bytes, field_message


class WhatsAppMessageError(ValueError):
    pass


def generate_message_id_v2(
    user_jid: str | None = None,
    *,
    now_seconds: int | None = None,
    random16: bytes | None = None,
) -> str:
    data = bytearray(44)
    timestamp = int(time.time() if now_seconds is None else now_seconds)
    if timestamp < 0:
        raise WhatsAppMessageError("Message timestamp must be non-negative")
    data[:8] = timestamp.to_bytes(8, "big", signed=False)
    if user_jid:
        user = parse_jid(user_jid).user.encode("utf-8")
        identity = user + b"@c.us"
        data[8:8 + min(20, len(identity))] = identity[:20]
    rnd = bytes(random16 if random16 is not None else os.urandom(16))
    if len(rnd) != 16:
        raise WhatsAppMessageError("Message ID entropy must be 16 bytes")
    data[28:44] = rnd
    digest = hashlib.sha256(data).hexdigest().upper()
    return "3EB0" + digest[:18]


def participant_hash_v2(participants: Iterable[str]) -> str:
    normalized = sorted(str(x) for x in participants if str(x))
    digest = hashlib.sha256("".join(normalized).encode("utf-8")).digest()
    return "2:" + base64.b64encode(digest).decode("ascii")[:6]


def encode_text_message(text: str) -> bytes:
    value = str(text)
    if not value:
        raise WhatsAppMessageError("WhatsApp text message cannot be empty")
    # WAProto.Message.conversation = field 1.
    return field_bytes(1, value)


def encode_device_sent_message(destination_jid: str, message: bytes, *, phash: str | None = None) -> bytes:
    destination = str(parse_jid(destination_jid))
    fields = [field_bytes(1, destination), field_bytes(2, bytes(message))]
    if phash:
        fields.append(field_bytes(3, str(phash)))
    # WAProto.Message.deviceSentMessage = field 31.
    return field_message(31, fields)


def encrypted_participant_node(jid: str, *, ciphertext_type: str, ciphertext: bytes, extra_attrs: dict[str, str] | None = None) -> BinaryNode:
    target = str(parse_jid(jid))
    kind = str(ciphertext_type)
    if kind not in {"msg", "pkmsg"}:
        raise WhatsAppMessageError("Direct Signal ciphertext type must be msg or pkmsg")
    attrs = {"v": "2", "type": kind}
    attrs.update({str(k): str(v) for k, v in (extra_attrs or {}).items()})
    return BinaryNode("to", {"jid": target}, [BinaryNode("enc", attrs, bytes(ciphertext))])


def build_direct_message_stanza(
    *,
    destination_jid: str,
    message_id: str,
    participants: Iterable[BinaryNode],
    device_identity: bytes | None = None,
    message_type: str = "text",
    additional_attrs: dict[str, str] | None = None,
) -> BinaryNode:
    destination = str(parse_jid(destination_jid).normalized_user())
    nodes = list(participants)
    if not nodes:
        raise WhatsAppMessageError("Direct WhatsApp message requires at least one encrypted participant")
    participant_jids = [str(node.attrs.get("jid") or "") for node in nodes]
    if any(not jid for jid in participant_jids):
        raise WhatsAppMessageError("Encrypted participant is missing jid")
    attrs = {
        "id": str(message_id),
        "to": destination,
        "type": str(message_type),
        "phash": participant_hash_v2(participant_jids),
    }
    attrs.update({str(k): str(v) for k, v in (additional_attrs or {}).items()})
    content: list[BinaryNode] = [BinaryNode("participants", {}, nodes)]
    if device_identity is not None:
        content.append(BinaryNode("device-identity", {}, bytes(device_identity)))
    return BinaryNode("message", attrs, content)


__all__ = [
    "WhatsAppMessageError", "generate_message_id_v2", "participant_hash_v2",
    "encode_text_message", "encode_device_sent_message", "encrypted_participant_node",
    "build_direct_message_stanza",
]
