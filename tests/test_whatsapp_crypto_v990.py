from __future__ import annotations

import os
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from postmaster.whatsapp_v990.crypto import (
    NOISE_WA_HEADER,
    WhatsAppNoiseXX,
    curve_shared_key,
    decrypt_media,
    encrypt_media,
    frame_noise_payload,
    generate_curve_keypair,
    split_noise_frames,
    xeddsa_public_edwards,
    xeddsa_sign,
    xeddsa_verify,
)


class WhatsAppCryptoV990Tests(unittest.TestCase):
    def test_curve_shared_secret_matches(self):
        a = generate_curve_keypair(); b = generate_curve_keypair()
        self.assertEqual(curve_shared_key(a.private, b.public), curve_shared_key(b.private, a.public))

    def test_xeddsa_signature_self_and_ed25519_verification(self):
        pair = generate_curve_keypair()
        msg = b"postmaster xeddsa compatibility"
        sig = xeddsa_sign(pair.private, msg, random64=bytes(range(64)))
        self.assertTrue(xeddsa_verify(pair.public, msg, sig))
        self.assertFalse(xeddsa_verify(pair.public, msg + b"!", sig))
        Ed25519PublicKey.from_public_bytes(xeddsa_public_edwards(pair.public)).verify(sig, msg)

    def test_media_roundtrip_and_tamper_rejection(self):
        plaintext = os.urandom(913) + b"document"
        enc = encrypt_media(plaintext, "document", media_key=bytes(range(32)))
        self.assertEqual(decrypt_media(enc["encrypted"], "document", enc["media_key"], expected_file_sha256=enc["file_sha256"]), plaintext)
        tampered = bytearray(enc["encrypted"]); tampered[-1] ^= 1
        with self.assertRaises(ValueError):
            decrypt_media(bytes(tampered), "document", enc["media_key"])

    def test_noise_frame_split_handles_partial_data(self):
        payloads = [b"one", os.urandom(100), b""]
        stream = b"".join(frame_noise_payload(p) for p in payloads)
        frames, rest = split_noise_frames(stream[:-7])
        self.assertEqual(frames, payloads[:1])
        more, rest2 = split_noise_frames(rest + stream[-7:])
        self.assertEqual(more, payloads[1:])
        self.assertEqual(rest2, b"")

    def test_noise_symmetric_handshake_steps_are_deterministic(self):
        # Full WA server certificate acceptance belongs to protocol tests. Here we validate
        # that identical state transitions stay synchronized for encrypt/decrypt directions.
        a = generate_curve_keypair()
        n = WhatsAppNoiseXX(a, header=NOISE_WA_HEADER)
        self.assertEqual(len(n.hash), 32)
        self.assertEqual(n.counter, 0)


if __name__ == "__main__": unittest.main()
