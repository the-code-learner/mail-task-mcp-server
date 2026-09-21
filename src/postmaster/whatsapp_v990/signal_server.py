from __future__ import annotations

"""WhatsApp Signal key-bundle XMPP/WABinary helpers.

All functions here are pure protocol transforms: no network I/O and no account side effects.
"""

from dataclasses import dataclass
from typing import Iterable, Mapping

from .binary import BinaryNode
from .crypto import CurveKeyPair
from .signal_keys import SignalPreKeyBundle, SignedPreKey

KEY_BUNDLE_TYPE = b"\x05"


class SignalServerError(ValueError):
    pass


def encode_uint_be(value: int, width: int | None = None) -> bytes:
    value = int(value)
    if value < 0:
        raise SignalServerError("Signal integer must be non-negative")
    if width is None:
        width = max(1, (value.bit_length() + 7) // 8)
    if width <= 0 or value >= (1 << (width * 8)):
        raise SignalServerError("Signal integer does not fit requested width")
    return value.to_bytes(width, "big")


def decode_uint_be(raw: bytes, *, width: int | None = None) -> int:
    value = bytes(raw)
    if width is not None and len(value) != width:
        raise SignalServerError(f"Signal integer must be {width} bytes")
    if not value:
        raise SignalServerError("Signal integer cannot be empty")
    return int.from_bytes(value, "big")


def _bytes_content(node: BinaryNode | None, label: str) -> bytes:
    if node is None or not isinstance(node.content, bytes):
        raise SignalServerError(f"Missing Signal {label}")
    return bytes(node.content)


def xmpp_pre_key(key_id: int, pair: CurveKeyPair) -> BinaryNode:
    return BinaryNode("key", {}, [
        BinaryNode("id", {}, encode_uint_be(key_id, 3)),
        BinaryNode("value", {}, pair.public),
    ])


def xmpp_signed_pre_key(value: SignedPreKey) -> BinaryNode:
    return BinaryNode("skey", {}, [
        BinaryNode("id", {}, encode_uint_be(value.key_id, 3)),
        BinaryNode("value", {}, value.key_pair.public),
        BinaryNode("signature", {}, value.signature),
    ])


def build_prekey_count_query(*, stanza_id: str | None = None) -> BinaryNode:
    attrs = {"xmlns": "encrypt", "type": "get", "to": "s.whatsapp.net"}
    if stanza_id:
        attrs["id"] = stanza_id
    return BinaryNode("iq", attrs, [BinaryNode("count")])


def parse_prekey_count(response: BinaryNode) -> int:
    count = response.child("count")
    if count is None:
        raise SignalServerError("Signal pre-key count response has no count node")
    try:
        value = int(count.attrs.get("value", ""))
    except ValueError as exc:
        raise SignalServerError("Signal pre-key count is not an integer") from exc
    if value < 0:
        raise SignalServerError("Signal pre-key count cannot be negative")
    return value


def build_prekey_upload(
    *,
    registration_id: int,
    identity_public: bytes,
    signed_pre_key: SignedPreKey,
    pre_keys: Mapping[int, CurveKeyPair],
    stanza_id: str | None = None,
) -> BinaryNode:
    identity = bytes(identity_public)
    if len(identity) != 32:
        raise SignalServerError("Signal identity public key must be 32 bytes")
    attrs = {"xmlns": "encrypt", "type": "set", "to": "s.whatsapp.net"}
    if stanza_id:
        attrs["id"] = stanza_id
    ordered = [xmpp_pre_key(key_id, pre_keys[key_id]) for key_id in sorted(pre_keys)]
    return BinaryNode("iq", attrs, [
        BinaryNode("registration", {}, encode_uint_be(registration_id)),
        BinaryNode("type", {}, KEY_BUNDLE_TYPE),
        BinaryNode("identity", {}, identity),
        BinaryNode("list", {}, ordered),
        xmpp_signed_pre_key(signed_pre_key),
    ])


def build_session_query(jids: Iterable[str], *, force_identity: bool = False, stanza_id: str | None = None) -> BinaryNode:
    users = []
    seen = set()
    for jid in jids:
        value = str(jid or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        attrs = {"jid": value}
        if force_identity:
            attrs["reason"] = "identity"
        users.append(BinaryNode("user", attrs))
    if not users:
        raise SignalServerError("At least one JID is required for Signal session fetch")
    attrs = {"xmlns": "encrypt", "type": "get", "to": "s.whatsapp.net"}
    if stanza_id:
        attrs["id"] = stanza_id
    return BinaryNode("iq", attrs, [BinaryNode("key", {}, users)])


def _extract_key(node: BinaryNode | None, *, signed: bool) -> tuple[int, bytes, bytes | None]:
    if node is None:
        raise SignalServerError("Missing Signal key node")
    key_id = decode_uint_be(_bytes_content(node.child("id"), "key id"), width=3)
    public = _bytes_content(node.child("value"), "key value")
    if len(public) != 32:
        raise SignalServerError("Signal pre-key public key must be 32 bytes")
    signature = _bytes_content(node.child("signature"), "signed pre-key signature") if signed else None
    if signature is not None and len(signature) != 64:
        raise SignalServerError("Signal signed pre-key signature must be 64 bytes")
    return key_id, public, signature


def parse_session_bundles(response: BinaryNode) -> dict[str, SignalPreKeyBundle]:
    listing = response.child("list")
    if listing is None:
        raise SignalServerError("Signal session response has no list node")
    bundles: dict[str, SignalPreKeyBundle] = {}
    for user in listing.children("user"):
        jid = str(user.attrs.get("jid") or "").strip()
        if not jid:
            raise SignalServerError("Signal session user has no JID")
        error = user.child("error")
        if error is not None:
            continue
        registration = decode_uint_be(_bytes_content(user.child("registration"), "registration"))
        type_node = _bytes_content(user.child("type"), "key bundle type")
        if type_node != KEY_BUNDLE_TYPE:
            raise SignalServerError("Unsupported Signal key bundle type")
        identity = _bytes_content(user.child("identity"), "identity")
        if len(identity) != 32:
            raise SignalServerError("Signal identity key must be 32 bytes")
        signed_id, signed_public, signature = _extract_key(user.child("skey"), signed=True)
        pre_node = user.child("key")
        pre_id: int | None = None
        pre_public: bytes | None = None
        if pre_node is not None:
            pre_id, pre_public, _ = _extract_key(pre_node, signed=False)
        bundles[jid] = SignalPreKeyBundle(
            registration_id=registration,
            identity_key=identity,
            signed_pre_key_id=signed_id,
            signed_pre_key=signed_public,
            signed_pre_key_signature=signature or b"",
            pre_key_id=pre_id,
            pre_key=pre_public,
        )
    return bundles


__all__ = [
    "KEY_BUNDLE_TYPE", "SignalServerError", "encode_uint_be", "decode_uint_be",
    "xmpp_pre_key", "xmpp_signed_pre_key", "build_prekey_count_query", "parse_prekey_count",
    "build_prekey_upload", "build_session_query", "parse_session_bundles",
]
