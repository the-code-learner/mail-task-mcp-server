from __future__ import annotations

"""Clean-room WhatsApp/Signal cryptographic primitives for Postmaster v9.9.

Only public protocol specifications are used here.  The module intentionally keeps the
WhatsApp-specific policy thin: Curve25519/XEdDSA, Noise XX helpers, and media key derivation.
Long-lived secrets are never logged or returned by the public MCP surface.
"""

from dataclasses import dataclass
import hashlib
import hmac
import os
from typing import Final

from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

P: Final[int] = 2**255 - 19
Q: Final[int] = 2**252 + 27742317777372353535851937790883648493
D: Final[int] = (-121665 * pow(121666, P - 2, P)) % P
SQRT_M1: Final[int] = pow(2, (P - 1) // 4, P)
NOISE_MODE: Final[bytes] = b"Noise_XX_25519_AESGCM_SHA256\x00\x00\x00\x00"
NOISE_WA_HEADER: Final[bytes] = bytes((87, 65, 6, 3))
WA_CERT_PUBLIC_KEY: Final[bytes] = bytes.fromhex("142375574d0a587166aae71ebe516437c4a28b73e3695c6ce1f7f9545da8ee6b")


class CryptoError(ValueError):
    pass


@dataclass(frozen=True)
class CurveKeyPair:
    private: bytes
    public: bytes


def clamp_curve25519_scalar(raw: bytes) -> bytes:
    if len(raw) != 32:
        raise CryptoError("Curve25519 private key must be 32 bytes")
    b = bytearray(raw)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return bytes(b)


def generate_curve_keypair() -> CurveKeyPair:
    private = x25519.X25519PrivateKey.generate()
    priv = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return CurveKeyPair(private=priv, public=pub)


def curve_public_from_private(private: bytes) -> bytes:
    key = x25519.X25519PrivateKey.from_private_bytes(private)
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def curve_shared_key(private: bytes, public: bytes) -> bytes:
    if len(private) != 32 or len(public) != 32:
        raise CryptoError("Curve25519 keys must be 32 bytes")
    return x25519.X25519PrivateKey.from_private_bytes(private).exchange(x25519.X25519PublicKey.from_public_bytes(public))


def signal_public_key(public: bytes) -> bytes:
    if len(public) == 33 and public[0] == 5:
        return public
    if len(public) != 32:
        raise CryptoError("Signal Curve25519 public key must be 32 bytes")
    return b"\x05" + public


Point = tuple[int, int]
IDENTITY: Final[Point] = (0, 1)


def _inv(x: int) -> int:
    return pow(x % P, P - 2, P) if x % P else 0


def _sqrt(v: int) -> int | None:
    v %= P
    x = pow(v, (P + 3) // 8, P)
    if (x * x - v) % P:
        x = (x * SQRT_M1) % P
    if (x * x - v) % P:
        return None
    return x


def _point_from_y(y: int, sign: int) -> Point:
    if not 0 <= y < P:
        raise CryptoError("Invalid Edwards y coordinate")
    y2 = y * y % P
    x2 = (y2 - 1) * _inv(1 + D * y2) % P
    x = _sqrt(x2)
    if x is None:
        raise CryptoError("Point is not on Curve25519 Edwards form")
    if (x & 1) != (sign & 1):
        x = (-x) % P
    point = (x, y)
    if not _on_curve(point):
        raise CryptoError("Decoded point is not on curve")
    return point


def _on_curve(point: Point) -> bool:
    x, y = point
    return (-x * x + y * y - 1 - D * x * x * y * y) % P == 0


def _point_add(a: Point, b: Point) -> Point:
    x1, y1 = a
    x2, y2 = b
    t = D * x1 * x2 * y1 * y2 % P
    den_x = _inv(1 + t)
    den_y = _inv(1 - t)
    x3 = (x1 * y2 + y1 * x2) * den_x % P
    y3 = (y1 * y2 + x1 * x2) * den_y % P
    return x3, y3


def _point_neg(point: Point) -> Point:
    return (-point[0]) % P, point[1]


def _scalar_mult(k: int, point: Point) -> Point:
    k %= Q
    acc = IDENTITY
    cur = point
    while k:
        if k & 1:
            acc = _point_add(acc, cur)
        cur = _point_add(cur, cur)
        k >>= 1
    return acc


def _encode_point(point: Point) -> bytes:
    x, y = point
    raw = bytearray(int(y).to_bytes(32, "little"))
    raw[31] = (raw[31] & 0x7F) | ((x & 1) << 7)
    return bytes(raw)


def _decode_point(raw: bytes) -> Point:
    if len(raw) != 32:
        raise CryptoError("Compressed Edwards point must be 32 bytes")
    value = int.from_bytes(raw, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    return _point_from_y(y, sign)


def _convert_montgomery_u(public: bytes) -> Point:
    if len(public) == 33 and public[0] == 5:
        public = public[1:]
    if len(public) != 32:
        raise CryptoError("Montgomery public key must be 32 bytes")
    u = int.from_bytes(public, "little") & ((1 << 255) - 1)
    y = (u - 1) * _inv(u + 1) % P
    return _point_from_y(y, 0)


BASE_POINT: Final[Point] = _convert_montgomery_u((9).to_bytes(32, "little"))


def _xeddsa_keypair(private: bytes) -> tuple[Point, int]:
    scalar_bytes = clamp_curve25519_scalar(private)
    k = int.from_bytes(scalar_bytes, "little") % Q
    e = _scalar_mult(k, BASE_POINT)
    a = (-k) % Q if (e[0] & 1) else k
    A = (e[0] if not (e[0] & 1) else (-e[0]) % P, e[1])
    if A[0] & 1:
        raise CryptoError("Failed to normalize XEdDSA public key")
    return A, a


def _hash_int(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _hash_i(index: int, data: bytes) -> int:
    if not 0 <= index < 256:
        raise CryptoError("XEdDSA domain index must fit one byte")
    prefix = ((1 << 256) - 1 - index).to_bytes(32, "little")
    return _hash_int(prefix + data)


def xeddsa_sign(private: bytes, message: bytes, *, random64: bytes | None = None) -> bytes:
    if len(private) != 32:
        raise CryptoError("XEdDSA private key must be 32 bytes")
    z = random64 if random64 is not None else os.urandom(64)
    if len(z) != 64:
        raise CryptoError("XEdDSA randomizer must be 64 bytes")
    A, a = _xeddsa_keypair(private)
    a_bytes = a.to_bytes(32, "little")
    r = _hash_i(1, a_bytes + bytes(message) + z) % Q
    R = _scalar_mult(r, BASE_POINT)
    Rb = _encode_point(R)
    Ab = _encode_point(A)
    h = _hash_int(Rb + Ab + bytes(message)) % Q
    s = (r + h * a) % Q
    return Rb + s.to_bytes(32, "little")


def xeddsa_public_edwards(public: bytes) -> bytes:
    return _encode_point(_convert_montgomery_u(public))


def xeddsa_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    try:
        if len(signature) != 64:
            return False
        A = _convert_montgomery_u(public)
        Rb = signature[:32]
        R = _decode_point(Rb)
        s = int.from_bytes(signature[32:], "little")
        if s >= Q:
            return False
        Ab = _encode_point(A)
        h = _hash_int(Rb + Ab + bytes(message)) % Q
        check = _point_add(_scalar_mult(s, BASE_POINT), _point_neg(_scalar_mult(h, A)))
        return hmac.compare_digest(_encode_point(check), Rb)
    except (CryptoError, ValueError):
        return False


def hkdf_sha256(ikm: bytes, length: int, *, salt: bytes | None = None, info: bytes = b"") -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=int(length), salt=salt, info=info).derive(bytes(ikm))


def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(bytes(plaintext)) + padder.finalize()
    enc = Cipher(algorithms.AES(bytes(key)), modes.CBC(bytes(iv))).encryptor()
    return enc.update(padded) + enc.finalize()


def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    dec = Cipher(algorithms.AES(bytes(key)), modes.CBC(bytes(iv))).decryptor()
    padded = dec.update(bytes(ciphertext)) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


_MEDIA_INFO = {
    "image": b"WhatsApp Image Keys",
    "video": b"WhatsApp Video Keys",
    "audio": b"WhatsApp Audio Keys",
    "document": b"WhatsApp Document Keys",
    "sticker": b"WhatsApp Image Keys",
}


def encrypt_media(plaintext: bytes, media_type: str, *, media_key: bytes | None = None) -> dict[str, bytes]:
    kind = str(media_type).lower()
    if kind not in _MEDIA_INFO:
        raise CryptoError(f"Unsupported WhatsApp media type: {media_type}")
    key = media_key or os.urandom(32)
    if len(key) != 32:
        raise CryptoError("WhatsApp media key must be 32 bytes")
    expanded = hkdf_sha256(key, 112, salt=None, info=_MEDIA_INFO[kind])
    iv, cipher_key, mac_key = expanded[:16], expanded[16:48], expanded[48:80]
    encrypted = aes_cbc_encrypt(cipher_key, iv, bytes(plaintext))
    mac = hmac.new(mac_key, iv + encrypted, hashlib.sha256).digest()[:10]
    blob = encrypted + mac
    return {
        "media_key": key,
        "file_enc_sha256": hashlib.sha256(blob).digest(),
        "file_sha256": hashlib.sha256(bytes(plaintext)).digest(),
        "encrypted": blob,
    }


def decrypt_media(blob: bytes, media_type: str, media_key: bytes, *, expected_file_sha256: bytes | None = None) -> bytes:
    kind = str(media_type).lower()
    if kind not in _MEDIA_INFO:
        raise CryptoError(f"Unsupported WhatsApp media type: {media_type}")
    if len(blob) < 11 or len(media_key) != 32:
        raise CryptoError("Invalid WhatsApp media payload")
    expanded = hkdf_sha256(media_key, 112, salt=None, info=_MEDIA_INFO[kind])
    iv, cipher_key, mac_key = expanded[:16], expanded[16:48], expanded[48:80]
    encrypted, mac = bytes(blob[:-10]), bytes(blob[-10:])
    expected = hmac.new(mac_key, iv + encrypted, hashlib.sha256).digest()[:10]
    if not hmac.compare_digest(mac, expected):
        raise CryptoError("WhatsApp media MAC mismatch")
    plaintext = aes_cbc_decrypt(cipher_key, iv, encrypted)
    if expected_file_sha256 is not None and not hmac.compare_digest(hashlib.sha256(plaintext).digest(), expected_file_sha256):
        raise CryptoError("WhatsApp media SHA-256 mismatch")
    return plaintext


def _noise_iv(counter: int) -> bytes:
    if counter < 0 or counter > 0xFFFFFFFF:
        raise CryptoError("Noise nonce counter exhausted")
    return b"\x00" * 8 + int(counter).to_bytes(4, "big")


@dataclass
class NoiseTransport:
    write_key: bytes
    read_key: bytes
    write_counter: int = 0
    read_counter: int = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        iv = _noise_iv(self.write_counter)
        self.write_counter += 1
        return AESGCM(self.write_key).encrypt(iv, bytes(plaintext), b"")

    def decrypt(self, ciphertext: bytes) -> bytes:
        iv = _noise_iv(self.read_counter)
        self.read_counter += 1
        try:
            return AESGCM(self.read_key).decrypt(iv, bytes(ciphertext), b"")
        except Exception as exc:
            raise CryptoError("Noise transport authentication failed") from exc


class WhatsAppNoiseXX:
    """Initiator-side Noise_XX_25519_AESGCM_SHA256 state used by WhatsApp Web."""

    def __init__(self, ephemeral: CurveKeyPair, *, header: bytes = NOISE_WA_HEADER):
        self.ephemeral = ephemeral
        initial = hashlib.sha256(NOISE_MODE).digest() if len(NOISE_MODE) != 32 else NOISE_MODE
        self.hash = initial
        self.salt = initial
        self.key = initial
        self.counter = 0
        self.transport: NoiseTransport | None = None
        self.authenticate(header)
        self.authenticate(ephemeral.public)

    def authenticate(self, data: bytes) -> None:
        if self.transport is None:
            self.hash = hashlib.sha256(self.hash + bytes(data)).digest()

    def _derive_pair(self, data: bytes) -> tuple[bytes, bytes]:
        material = hkdf_sha256(bytes(data), 64, salt=self.salt, info=b"")
        return material[:32], material[32:]

    def mix_key(self, data: bytes) -> None:
        self.salt, self.key = self._derive_pair(data)
        self.counter = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        if self.transport is not None:
            return self.transport.encrypt(plaintext)
        iv = _noise_iv(self.counter)
        self.counter += 1
        out = AESGCM(self.key).encrypt(iv, bytes(plaintext), self.hash)
        self.authenticate(out)
        return out

    def decrypt(self, ciphertext: bytes) -> bytes:
        if self.transport is not None:
            return self.transport.decrypt(ciphertext)
        iv = _noise_iv(self.counter)
        self.counter += 1
        try:
            out = AESGCM(self.key).decrypt(iv, bytes(ciphertext), self.hash)
        except Exception as exc:
            raise CryptoError("Noise handshake authentication failed") from exc
        self.authenticate(ciphertext)
        return out

    def process_server_hello(self, *, server_ephemeral: bytes, encrypted_static: bytes, encrypted_payload: bytes,
                             noise_static: CurveKeyPair) -> tuple[bytes, bytes]:
        self.authenticate(server_ephemeral)
        self.mix_key(curve_shared_key(self.ephemeral.private, server_ephemeral))
        server_static = self.decrypt(encrypted_static)
        if len(server_static) != 32:
            raise CryptoError("Noise server static key must be 32 bytes")
        self.mix_key(curve_shared_key(self.ephemeral.private, server_static))
        cert_chain = self.decrypt(encrypted_payload)
        encrypted_client_static = self.encrypt(noise_static.public)
        self.mix_key(curve_shared_key(noise_static.private, server_ephemeral))
        return encrypted_client_static, cert_chain

    def finish(self) -> NoiseTransport:
        write, read = self._derive_pair(b"")
        self.transport = NoiseTransport(write_key=write, read_key=read)
        return self.transport


def frame_noise_payload(payload: bytes, *, intro: bytes | None = None) -> bytes:
    size = len(payload)
    if size > 0xFFFFFF:
        raise CryptoError("WhatsApp Noise frame exceeds 24-bit length")
    return (bytes(intro or b"") + size.to_bytes(3, "big") + bytes(payload))


def split_noise_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    frames: list[bytes] = []
    pos = 0
    data = bytes(buffer)
    while len(data) - pos >= 3:
        size = int.from_bytes(data[pos:pos+3], "big")
        if len(data) - pos - 3 < size:
            break
        pos += 3
        frames.append(data[pos:pos+size])
        pos += size
    return frames, data[pos:]
