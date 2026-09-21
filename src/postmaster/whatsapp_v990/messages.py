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


def pad_random_max16(message: bytes, *, random1: bytes | None = None) -> bytes:
    """Apply WhatsApp's 1..16 byte message padding before Signal encryption."""
    entropy = bytes(random1 if random1 is not None else os.urandom(1))
    if len(entropy) != 1:
        raise WhatsAppMessageError("WhatsApp message padding entropy must be exactly one byte")
    pad_length = (entropy[0] & 0x0F) + 1
    return bytes(message) + bytes((pad_length,)) * pad_length


def unpad_random_max16(message: bytes) -> bytes:
    """Remove WhatsApp message padding after Signal decryption."""
    raw = bytes(message)
    if not raw:
        raise WhatsAppMessageError("Cannot unpad an empty WhatsApp message")
    pad_length = raw[-1]
    if pad_length < 1 or pad_length > 16 or pad_length > len(raw):
        raise WhatsAppMessageError("Invalid WhatsApp message padding")
    return raw[:-pad_length]


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


def encode_reply_text_message(
    text: str,
    *,
    stanza_id: str,
    participant: str,
    remote_jid: str | None = None,
    quoted_message: bytes | None = None,
) -> bytes:
    value = str(text)
    reply_id = str(stanza_id or "").strip()
    sender = str(parse_jid(participant))
    if not value:
        raise WhatsAppMessageError("WhatsApp reply text cannot be empty")
    if not reply_id:
        raise WhatsAppMessageError("WhatsApp reply requires quoted stanza id")
    context = [field_bytes(1, reply_id), field_bytes(2, sender)]
    if quoted_message is not None:
        context.append(field_bytes(3, bytes(quoted_message)))
    if remote_jid:
        context.append(field_bytes(4, str(parse_jid(remote_jid).normalized_user())))
    extended = [field_bytes(1, value), field_message(17, context)]
    # WAProto.Message.extendedTextMessage = field 6.
    return field_message(6, extended)


def build_read_receipt(*, destination_jid: str, message_id: str) -> BinaryNode:
    destination = str(parse_jid(destination_jid).normalized_user())
    mid = str(message_id or "").strip()
    if not mid:
        raise WhatsAppMessageError("WhatsApp read receipt requires message id")
    return BinaryNode("receipt", {"id": mid, "to": destination, "type": "read"})


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
    "WhatsAppMessageError", "pad_random_max16", "unpad_random_max16", "generate_message_id_v2", "participant_hash_v2",
    "encode_text_message", "encode_reply_text_message", "build_read_receipt",
    "encode_device_sent_message", "encrypted_participant_node", "build_direct_message_stanza",
]
