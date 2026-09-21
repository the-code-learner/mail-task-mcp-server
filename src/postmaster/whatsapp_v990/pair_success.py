from __future__ import annotations

"""Clean-room WhatsApp companion pair-success verification and reply construction."""

from dataclasses import dataclass
import hmac
import hashlib

from .binary import BinaryNode
from .crypto import CurveKeyPair, xeddsa_sign, xeddsa_verify
from .proto import ProtoError, decode_fields, field_bytes, field_varint

WA_ADV_ACCOUNT_SIG_PREFIX = bytes((6, 0))
WA_ADV_DEVICE_SIG_PREFIX = bytes((6, 1))
WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX = bytes((6, 5))

ADV_ENCRYPTION_E2EE = 0
ADV_ENCRYPTION_HOSTED = 1
ADV_ENCRYPTION_NON_E2EE = 2


class PairSuccessError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ADVDeviceIdentity:
    raw_id: int | None = None
    timestamp: int | None = None
    key_index: int | None = None
    account_type: int | None = None
    device_type: int | None = None


@dataclass(frozen=True, slots=True)
class ADVSignedDeviceIdentity:
    details: bytes
    account_signature_key: bytes | None
    account_signature: bytes
    device_signature: bytes | None = None


@dataclass(frozen=True, slots=True)
class PairSuccessResult:
    reply: BinaryNode
    jid: str
    lid: str | None
    platform: str | None
    business_name: str | None
    account: ADVSignedDeviceIdentity
    device_identity: ADVDeviceIdentity
    signal_identity_key: bytes


def _values(raw: bytes, number: int) -> list[int | bytes]:
    try:
        return [field.value for field in decode_fields(bytes(raw)) if field.number == number]
    except ProtoError as exc:
        raise PairSuccessError("Invalid pair-success protobuf") from exc


def _last_bytes(raw: bytes, number: int, *, required: bool = False) -> bytes | None:
    values = [value for value in _values(raw, number) if isinstance(value, bytes)]
    if not values:
        if required:
            raise PairSuccessError(f"Missing pair-success bytes field {number}")
        return None
    return bytes(values[-1])


def _last_int(raw: bytes, number: int) -> int | None:
    values = [value for value in _values(raw, number) if isinstance(value, int)]
    return int(values[-1]) if values else None


def decode_adv_device_identity(raw: bytes) -> ADVDeviceIdentity:
    return ADVDeviceIdentity(
        raw_id=_last_int(raw, 1),
        timestamp=_last_int(raw, 2),
        key_index=_last_int(raw, 3),
        account_type=_last_int(raw, 4),
        device_type=_last_int(raw, 5),
    )


def decode_signed_device_identity(raw: bytes) -> ADVSignedDeviceIdentity:
    details = _last_bytes(raw, 1, required=True)
    account_signature = _last_bytes(raw, 3, required=True)
    assert details is not None and account_signature is not None
    account_signature_key = _last_bytes(raw, 2)
    device_signature = _last_bytes(raw, 4)
    if account_signature_key is not None and len(account_signature_key) != 32:
        raise PairSuccessError("ADV account signature key must be 32 bytes")
    if len(account_signature) != 64:
        raise PairSuccessError("ADV account signature must be 64 bytes")
    if device_signature is not None and len(device_signature) != 64:
        raise PairSuccessError("ADV device signature must be 64 bytes")
    return ADVSignedDeviceIdentity(
        details=details,
        account_signature_key=account_signature_key,
        account_signature=account_signature,
        device_signature=device_signature,
    )


def encode_signed_device_identity(value: ADVSignedDeviceIdentity, *, include_signature_key: bool) -> bytes:
    out = [field_bytes(1, value.details)]
    if include_signature_key and value.account_signature_key:
        out.append(field_bytes(2, value.account_signature_key))
    out.append(field_bytes(3, value.account_signature))
    if value.device_signature:
        out.append(field_bytes(4, value.device_signature))
    return b"".join(out)


def decode_signed_device_identity_hmac(raw: bytes) -> tuple[bytes, bytes, int | None]:
    details = _last_bytes(raw, 1, required=True)
    digest = _last_bytes(raw, 2, required=True)
    account_type = _last_int(raw, 3)
    assert details is not None and digest is not None
    if len(digest) != 32:
        raise PairSuccessError("ADV identity HMAC must be 32 bytes")
    return details, digest, account_type


def _child(node: BinaryNode | None, tag: str) -> BinaryNode | None:
    return node.child(tag) if node is not None else None


def configure_pair_success(
    stanza: BinaryNode,
    *,
    adv_secret_key: bytes,
    signed_identity_key: CurveKeyPair,
) -> PairSuccessResult:
    """Verify a live pair-success stanza and build the pair-device-sign IQ reply.

    No state is persisted here; callers persist the returned account/JID only after this function
    completes successfully.
    """
    if stanza.tag != "iq":
        raise PairSuccessError("pair-success must arrive inside an iq stanza")
    msg_id = str(stanza.attrs.get("id") or "").strip()
    if not msg_id:
        raise PairSuccessError("pair-success iq has no id")
    pair_success = _child(stanza, "pair-success")
    device_identity_node = _child(pair_success, "device-identity")
    device_node = _child(pair_success, "device")
    platform_node = _child(pair_success, "platform")
    business_node = _child(pair_success, "biz")
    if device_identity_node is None or device_node is None:
        raise PairSuccessError("pair-success is missing device-identity or device")
    if not isinstance(device_identity_node.content, (bytes, bytearray, memoryview)):
        raise PairSuccessError("pair-success device-identity content must be bytes")

    jid = str(device_node.attrs.get("jid") or "").strip()
    if not jid:
        raise PairSuccessError("pair-success device has no jid")
    lid = str(device_node.attrs.get("lid") or "").strip() or None

    wrapped_details, received_hmac, wrapper_account_type = decode_signed_device_identity_hmac(bytes(device_identity_node.content))
    hmac_prefix = WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX if wrapper_account_type == ADV_ENCRYPTION_HOSTED else b""
    expected_hmac = hmac.new(bytes(adv_secret_key), hmac_prefix + wrapped_details, hashlib.sha256).digest()
    if not hmac.compare_digest(received_hmac, expected_hmac):
        raise PairSuccessError("Invalid ADV account HMAC")

    account = decode_signed_device_identity(wrapped_details)
    if account.account_signature_key is None:
        raise PairSuccessError("ADV signed identity has no account signature key")
    device_identity = decode_adv_device_identity(account.details)
    account_prefix = WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX if device_identity.device_type == ADV_ENCRYPTION_HOSTED else WA_ADV_ACCOUNT_SIG_PREFIX
    account_message = account_prefix + account.details + signed_identity_key.public
    if not xeddsa_verify(account.account_signature_key, account_message, account.account_signature):
        raise PairSuccessError("ADV account signature verification failed")

    device_message = WA_ADV_DEVICE_SIG_PREFIX + account.details + signed_identity_key.public + account.account_signature_key
    device_signature = xeddsa_sign(signed_identity_key.private, device_message)
    signed_account = ADVSignedDeviceIdentity(
        details=account.details,
        account_signature_key=account.account_signature_key,
        account_signature=account.account_signature,
        device_signature=device_signature,
    )
    key_index = device_identity.key_index
    if key_index is None:
        raise PairSuccessError("ADV device identity has no key index")

    reply = BinaryNode(
        "iq",
        {"to": "s.whatsapp.net", "type": "result", "id": msg_id},
        [
            BinaryNode(
                "pair-device-sign",
                {},
                [
                    BinaryNode(
                        "device-identity",
                        {"key-index": str(key_index)},
                        encode_signed_device_identity(signed_account, include_signature_key=False),
                    )
                ],
            )
        ],
    )
    return PairSuccessResult(
        reply=reply,
        jid=jid,
        lid=lid,
        platform=str(platform_node.attrs.get("name") or "").strip() or None if platform_node else None,
        business_name=str(business_node.attrs.get("name") or "").strip() or None if business_node else None,
        account=signed_account,
        device_identity=device_identity,
        signal_identity_key=account.account_signature_key,
    )


__all__ = [
    "WA_ADV_ACCOUNT_SIG_PREFIX",
    "WA_ADV_DEVICE_SIG_PREFIX",
    "WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX",
    "ADV_ENCRYPTION_E2EE",
    "ADV_ENCRYPTION_HOSTED",
    "ADV_ENCRYPTION_NON_E2EE",
    "PairSuccessError",
    "ADVDeviceIdentity",
    "ADVSignedDeviceIdentity",
    "PairSuccessResult",
    "decode_adv_device_identity",
    "decode_signed_device_identity",
    "encode_signed_device_identity",
    "decode_signed_device_identity_hmac",
    "configure_pair_success",
]
