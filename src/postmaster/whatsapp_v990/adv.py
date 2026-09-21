from __future__ import annotations

"""WhatsApp ADV device-identity verification/signing for companion pairing.

The wire shapes are small protobuf messages from the public WhatsApp Web schema. This module
does not perform network I/O and never logs ADV secrets or private keys.
"""

from dataclasses import dataclass, replace
import base64
import hashlib
import hmac

from .crypto import CurveKeyPair, xeddsa_sign, xeddsa_verify
from .proto import ProtoError, decode_fields, field_bytes, field_varint

ADV_E2EE = 0
ADV_HOSTED = 1

WA_ADV_ACCOUNT_SIG_PREFIX = bytes((6, 0))
WA_ADV_DEVICE_SIG_PREFIX = bytes((6, 1))
WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX = bytes((6, 5))
WA_ADV_HOSTED_DEVICE_SIG_PREFIX = bytes((6, 6))


class ADVError(ValueError):
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
    account_signature: bytes | None
    device_signature: bytes | None = None


@dataclass(frozen=True, slots=True)
class ADVSignedDeviceIdentityHMAC:
    details: bytes
    hmac_value: bytes
    account_type: int | None = None


@dataclass(frozen=True, slots=True)
class VerifiedPairingIdentity:
    signed_identity: ADVSignedDeviceIdentity
    device_identity: ADVDeviceIdentity
    encoded_for_reply: bytes
    account_signature_verified: bool = True
    hmac_verified: bool = True


def _map(raw: bytes) -> dict[int, list[int | bytes]]:
    try:
        fields = decode_fields(bytes(raw))
    except ProtoError as exc:
        raise ADVError("Invalid ADV protobuf") from exc
    out: dict[int, list[int | bytes]] = {}
    for field in fields:
        out.setdefault(field.number, []).append(field.value)
    return out


def _last_bytes(fields: dict[int, list[int | bytes]], number: int, *, required: bool = False) -> bytes | None:
    values = fields.get(number) or []
    value = values[-1] if values else None
    if value is None:
        if required:
            raise ADVError(f"Missing ADV bytes field {number}")
        return None
    if not isinstance(value, bytes):
        raise ADVError(f"ADV field {number} must be bytes")
    return bytes(value)


def _last_int(fields: dict[int, list[int | bytes]], number: int) -> int | None:
    values = fields.get(number) or []
    value = values[-1] if values else None
    if value is None:
        return None
    if not isinstance(value, int):
        raise ADVError(f"ADV field {number} must be varint")
    return int(value)


def decode_adv_device_identity(raw: bytes) -> ADVDeviceIdentity:
    fields = _map(raw)
    return ADVDeviceIdentity(
        raw_id=_last_int(fields, 1),
        timestamp=_last_int(fields, 2),
        key_index=_last_int(fields, 3),
        account_type=_last_int(fields, 4),
        device_type=_last_int(fields, 5),
    )


def encode_adv_device_identity(value: ADVDeviceIdentity) -> bytes:
    parts: list[bytes] = []
    for number, item in (
        (1, value.raw_id),
        (2, value.timestamp),
        (3, value.key_index),
        (4, value.account_type),
        (5, value.device_type),
    ):
        if item is not None:
            parts.append(field_varint(number, int(item)))
    return b"".join(parts)


def decode_signed_device_identity(raw: bytes) -> ADVSignedDeviceIdentity:
    fields = _map(raw)
    details = _last_bytes(fields, 1, required=True)
    assert details is not None
    account_key = _last_bytes(fields, 2)
    account_signature = _last_bytes(fields, 3)
    device_signature = _last_bytes(fields, 4)
    if account_key is not None and len(account_key) not in (32, 33):
        raise ADVError("ADV account signature key must be Curve25519 public-key length")
    for label, signature in (("account", account_signature), ("device", device_signature)):
        if signature is not None and len(signature) != 64:
            raise ADVError(f"ADV {label} signature must be 64 bytes")
    return ADVSignedDeviceIdentity(details, account_key, account_signature, device_signature)


def encode_signed_device_identity(value: ADVSignedDeviceIdentity, *, include_signature_key: bool) -> bytes:
    parts = [field_bytes(1, value.details)]
    if include_signature_key and value.account_signature_key:
        parts.append(field_bytes(2, value.account_signature_key))
    if value.account_signature:
        parts.append(field_bytes(3, value.account_signature))
    if value.device_signature:
        parts.append(field_bytes(4, value.device_signature))
    return b"".join(parts)


def decode_signed_device_identity_hmac(raw: bytes) -> ADVSignedDeviceIdentityHMAC:
    fields = _map(raw)
    details = _last_bytes(fields, 1, required=True)
    mac = _last_bytes(fields, 2, required=True)
    assert details is not None and mac is not None
    if len(mac) != 32:
        raise ADVError("ADV device-identity HMAC must be 32 bytes")
    return ADVSignedDeviceIdentityHMAC(details=details, hmac_value=mac, account_type=_last_int(fields, 3))


def encode_signed_device_identity_hmac(value: ADVSignedDeviceIdentityHMAC) -> bytes:
    parts = [field_bytes(1, value.details), field_bytes(2, value.hmac_value)]
    if value.account_type is not None:
        parts.append(field_varint(3, int(value.account_type)))
    return b"".join(parts)


def _adv_secret(value: bytes | str) -> bytes:
    if isinstance(value, str):
        try:
            raw = base64.b64decode(value, validate=True)
        except Exception as exc:
            raise ADVError("ADV secret must be valid Base64") from exc
    else:
        raw = bytes(value)
    if len(raw) != 32:
        raise ADVError("ADV secret must decode to 32 bytes")
    return raw


def verify_and_sign_pair_success_identity(
    device_identity_hmac_blob: bytes,
    *,
    adv_secret_key: bytes | str,
    signed_identity_key: CurveKeyPair,
    random64: bytes | None = None,
) -> VerifiedPairingIdentity:
    """Verify a pair-success device identity and add our companion device signature.

    The returned encoding deliberately omits accountSignatureKey, matching the pair-device-sign
    reply contract. The account key remains available in the structured return value for Signal
    identity persistence.
    """
    outer = decode_signed_device_identity_hmac(device_identity_hmac_blob)
    secret = _adv_secret(adv_secret_key)
    hmac_prefix = WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX if outer.account_type == ADV_HOSTED else b""
    expected_mac = hmac.new(secret, hmac_prefix + outer.details, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_mac, outer.hmac_value):
        raise ADVError("Invalid ADV account HMAC")

    account = decode_signed_device_identity(outer.details)
    if account.account_signature_key is None or account.account_signature is None:
        raise ADVError("ADV signed account identity is missing signature material")

    device_details = decode_adv_device_identity(account.details)
    account_prefix = (
        WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX
        if device_details.device_type == ADV_HOSTED
        else WA_ADV_ACCOUNT_SIG_PREFIX
    )
    account_message = account_prefix + account.details + signed_identity_key.public
    if not xeddsa_verify(account.account_signature_key, account_message, account.account_signature):
        raise ADVError("Invalid ADV account signature")

    device_message = (
        WA_ADV_DEVICE_SIG_PREFIX
        + account.details
        + signed_identity_key.public
        + account.account_signature_key
    )
    device_signature = xeddsa_sign(signed_identity_key.private, device_message, random64=random64)
    signed = replace(account, device_signature=device_signature)
    encoded = encode_signed_device_identity(signed, include_signature_key=False)
    return VerifiedPairingIdentity(
        signed_identity=signed,
        device_identity=device_details,
        encoded_for_reply=encoded,
    )


__all__ = [
    "ADV_E2EE", "ADV_HOSTED",
    "WA_ADV_ACCOUNT_SIG_PREFIX", "WA_ADV_DEVICE_SIG_PREFIX",
    "WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX", "WA_ADV_HOSTED_DEVICE_SIG_PREFIX",
    "ADVError", "ADVDeviceIdentity", "ADVSignedDeviceIdentity", "ADVSignedDeviceIdentityHMAC",
    "VerifiedPairingIdentity", "decode_adv_device_identity", "encode_adv_device_identity",
    "decode_signed_device_identity", "encode_signed_device_identity",
    "decode_signed_device_identity_hmac", "encode_signed_device_identity_hmac",
    "verify_and_sign_pair_success_identity",
]
