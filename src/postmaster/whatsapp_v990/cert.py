from __future__ import annotations

"""Clean-room WhatsApp Noise certificate-chain parsing and verification."""

from dataclasses import dataclass
import time

from .crypto import WA_CERT_PUBLIC_KEY, xeddsa_verify
from .proto import ProtoError, decode_fields


class NoiseCertificateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NoiseCertificateDetails:
    serial: int | None
    issuer_serial: int | None
    key: bytes | None
    not_before: int | None
    not_after: int | None


@dataclass(frozen=True, slots=True)
class VerifiedNoiseCertificateChain:
    leaf: NoiseCertificateDetails
    intermediate: NoiseCertificateDetails
    chain_signatures_verified: bool = True
    root_serial: int = 0


def _field_bytes(raw: bytes, number: int, *, required: bool = True) -> bytes | None:
    try:
        fields = decode_fields(bytes(raw))
    except ProtoError as exc:
        raise NoiseCertificateError("Invalid Noise certificate protobuf") from exc
    values = [field.value for field in fields if field.number == number and isinstance(field.value, bytes)]
    if not values:
        if required:
            raise NoiseCertificateError(f"Missing Noise certificate bytes field {number}")
        return None
    return bytes(values[-1])


def _field_int(raw: bytes, number: int) -> int | None:
    try:
        fields = decode_fields(bytes(raw))
    except ProtoError as exc:
        raise NoiseCertificateError("Invalid Noise certificate details protobuf") from exc
    values = [field.value for field in fields if field.number == number and isinstance(field.value, int)]
    return int(values[-1]) if values else None


def _certificate(raw: bytes) -> tuple[bytes, bytes]:
    details = _field_bytes(raw, 1)
    signature = _field_bytes(raw, 2)
    assert details is not None and signature is not None
    if len(signature) != 64:
        raise NoiseCertificateError("Noise certificate signature must be 64 bytes")
    return details, signature


def decode_noise_certificate_details(raw: bytes) -> NoiseCertificateDetails:
    key = _field_bytes(raw, 3, required=False)
    if key is not None and len(key) != 32:
        raise NoiseCertificateError("Noise certificate Curve25519 key must be 32 bytes")
    return NoiseCertificateDetails(
        serial=_field_int(raw, 1),
        issuer_serial=_field_int(raw, 2),
        key=key,
        not_before=_field_int(raw, 4),
        not_after=_field_int(raw, 5),
    )


def _check_validity(details: NoiseCertificateDetails, *, now: int) -> None:
    if details.not_before is not None and now < details.not_before:
        raise NoiseCertificateError("Noise certificate is not valid yet")
    if details.not_after is not None and now > details.not_after:
        raise NoiseCertificateError("Noise certificate is expired")


def verify_noise_certificate_chain(
    raw: bytes,
    *,
    root_public_key: bytes = WA_CERT_PUBLIC_KEY,
    root_serial: int = 0,
    now: int | None = None,
) -> VerifiedNoiseCertificateChain:
    """Verify WhatsApp's leaf -> intermediate -> pinned root Noise certificate chain.

    The root key/serial defaults are the public WhatsApp long-term Noise trust anchor.
    Callers may inject another root only for deterministic unit tests.
    """
    leaf_raw = _field_bytes(raw, 1)
    intermediate_raw = _field_bytes(raw, 2)
    assert leaf_raw is not None and intermediate_raw is not None

    leaf_details_raw, leaf_signature = _certificate(leaf_raw)
    intermediate_details_raw, intermediate_signature = _certificate(intermediate_raw)

    leaf = decode_noise_certificate_details(leaf_details_raw)
    intermediate = decode_noise_certificate_details(intermediate_details_raw)
    if intermediate.issuer_serial != int(root_serial):
        raise NoiseCertificateError(
            f"Noise intermediate issuer serial {intermediate.issuer_serial!r} does not match pinned root {root_serial}"
        )
    if intermediate.key is None:
        raise NoiseCertificateError("Noise intermediate certificate has no public key")

    if not xeddsa_verify(root_public_key, intermediate_details_raw, intermediate_signature):
        raise NoiseCertificateError("Noise intermediate certificate signature verification failed")
    if not xeddsa_verify(intermediate.key, leaf_details_raw, leaf_signature):
        raise NoiseCertificateError("Noise leaf certificate signature verification failed")

    current = int(time.time()) if now is None else int(now)
    _check_validity(intermediate, now=current)
    _check_validity(leaf, now=current)
    return VerifiedNoiseCertificateChain(
        leaf=leaf,
        intermediate=intermediate,
        chain_signatures_verified=True,
        root_serial=int(root_serial),
    )


__all__ = [
    "NoiseCertificateError",
    "NoiseCertificateDetails",
    "VerifiedNoiseCertificateChain",
    "decode_noise_certificate_details",
    "verify_noise_certificate_chain",
]
