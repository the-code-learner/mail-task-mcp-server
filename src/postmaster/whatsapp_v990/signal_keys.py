from __future__ import annotations

"""Clean-room Signal-v3 key bundle and X3DH-compatible derivation helpers.

These helpers match the Curve25519/XEdDSA bundle shape used by WhatsApp's current
libsignal path. They are protocol primitives, not a claim of live server interoperability.
"""

from dataclasses import dataclass
import hmac
import hashlib
import os

from .crypto import (
    CurveKeyPair,
    curve_shared_key,
    generate_curve_keypair,
    hkdf_sha256,
    signal_public_key,
    xeddsa_sign,
    xeddsa_verify,
)


class SignalKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SignedPreKey:
    key_id: int
    key_pair: CurveKeyPair
    signature: bytes


@dataclass(frozen=True, slots=True)
class SignalPreKeyBundle:
    registration_id: int
    identity_key: bytes
    signed_pre_key_id: int
    signed_pre_key: bytes
    signed_pre_key_signature: bytes
    pre_key_id: int | None = None
    pre_key: bytes | None = None


def generate_registration_id(random2: bytes | None = None) -> int:
    raw = bytes(random2 if random2 is not None else os.urandom(2))
    if len(raw) != 2:
        raise SignalKeyError("Signal registration-id entropy must be exactly two bytes")
    return int.from_bytes(raw, "little") & 0x3FFF


def generate_signed_pre_key(identity: CurveKeyPair, key_id: int, *, pre_key: CurveKeyPair | None = None, random64: bytes | None = None) -> SignedPreKey:
    if not 0 <= int(key_id) <= 0xFFFFFF:
        raise SignalKeyError("WhatsApp signed-prekey id must fit 24 bits")
    pair = pre_key or generate_curve_keypair()
    signature = xeddsa_sign(identity.private, signal_public_key(pair.public), random64=random64)
    return SignedPreKey(key_id=int(key_id), key_pair=pair, signature=signature)


def verify_signed_pre_key(identity_public: bytes, signed_pre_key_public: bytes, signature: bytes) -> bool:
    return xeddsa_verify(identity_public, signal_public_key(signed_pre_key_public), signature)


def derive_x3dh_initiator(
    *,
    our_identity_private: bytes,
    our_base_private: bytes,
    their_identity_public: bytes,
    their_signed_pre_key_public: bytes,
    their_one_time_pre_key_public: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Derive the legacy Signal-v3 initial root/chain pair for the initiator."""
    pieces = [
        b"\xFF" * 32,
        curve_shared_key(our_identity_private, _raw_pub(their_signed_pre_key_public)),
        curve_shared_key(our_base_private, _raw_pub(their_identity_public)),
        curve_shared_key(our_base_private, _raw_pub(their_signed_pre_key_public)),
    ]
    if their_one_time_pre_key_public is not None:
        pieces.append(curve_shared_key(our_base_private, _raw_pub(their_one_time_pre_key_public)))
    material = hkdf_sha256(b"".join(pieces), 64, salt=None, info=b"WhisperText")
    return material[:32], material[32:]


def derive_x3dh_responder(
    *,
    our_identity_private: bytes,
    our_signed_pre_key_private: bytes,
    their_identity_public: bytes,
    their_base_public: bytes,
    our_one_time_pre_key_private: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Responder mirror of :func:`derive_x3dh_initiator`."""
    pieces = [
        b"\xFF" * 32,
        curve_shared_key(our_signed_pre_key_private, _raw_pub(their_identity_public)),
        curve_shared_key(our_identity_private, _raw_pub(their_base_public)),
        curve_shared_key(our_signed_pre_key_private, _raw_pub(their_base_public)),
    ]
    if our_one_time_pre_key_private is not None:
        pieces.append(curve_shared_key(our_one_time_pre_key_private, _raw_pub(their_base_public)))
    material = hkdf_sha256(b"".join(pieces), 64, salt=None, info=b"WhisperText")
    return material[:32], material[32:]


def chain_message_seed(chain_key: bytes) -> bytes:
    if len(chain_key) != 32:
        raise SignalKeyError("Signal chain key must be 32 bytes")
    return hmac.new(bytes(chain_key), b"\x01", hashlib.sha256).digest()


def next_chain_key(chain_key: bytes) -> bytes:
    if len(chain_key) != 32:
        raise SignalKeyError("Signal chain key must be 32 bytes")
    return hmac.new(bytes(chain_key), b"\x02", hashlib.sha256).digest()


def derive_message_keys(chain_key: bytes) -> tuple[bytes, bytes, bytes]:
    seed = chain_message_seed(chain_key)
    material = hkdf_sha256(seed, 80, salt=None, info=b"WhisperMessageKeys")
    return material[:32], material[32:64], material[64:80]


def _raw_pub(public: bytes) -> bytes:
    raw = bytes(public)
    if len(raw) == 33 and raw[0] == 5:
        raw = raw[1:]
    if len(raw) != 32:
        raise SignalKeyError("Signal Curve25519 public key must be 32 bytes or 0x05-prefixed 33 bytes")
    return raw
