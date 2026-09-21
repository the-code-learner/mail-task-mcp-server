from __future__ import annotations

import unittest

from postmaster.whatsapp_v990.cert import NoiseCertificateError, verify_noise_certificate_chain
from postmaster.whatsapp_v990.crypto import generate_curve_keypair, xeddsa_sign
from postmaster.whatsapp_v990.proto import field_bytes, field_message, field_varint


def _details(*, serial: int, issuer: int, key: bytes, not_before: int, not_after: int) -> bytes:
    return b"".join(
        (
            field_varint(1, serial),
            field_varint(2, issuer),
            field_bytes(3, key),
            field_varint(4, not_before),
            field_varint(5, not_after),
        )
    )


def _cert(details: bytes, signature: bytes) -> bytes:
    return field_bytes(1, details) + field_bytes(2, signature)


class WhatsAppNoiseCertificateV990Tests(unittest.TestCase):
    def build_chain(self):
        now = 1_800_000_000
        root = generate_curve_keypair()
        intermediate = generate_curve_keypair()
        leaf = generate_curve_keypair()

        intermediate_details = _details(
            serial=1, issuer=0, key=intermediate.public, not_before=now - 60, not_after=now + 60
        )
        intermediate_signature = xeddsa_sign(
            root.private, intermediate_details, random64=b"r" * 64
        )
        leaf_details = _details(
            serial=2, issuer=1, key=leaf.public, not_before=now - 60, not_after=now + 60
        )
        leaf_signature = xeddsa_sign(
            intermediate.private, leaf_details, random64=b"i" * 64
        )
        chain = field_message(1, [_cert(leaf_details, leaf_signature)]) + field_message(
            2, [_cert(intermediate_details, intermediate_signature)]
        )
        return now, root, chain

    def test_valid_chain_verifies_to_injected_root(self):
        now, root, chain = self.build_chain()
        verified = verify_noise_certificate_chain(
            chain, root_public_key=root.public, root_serial=0, now=now
        )
        self.assertTrue(verified.chain_signatures_verified)
        self.assertEqual(verified.intermediate.issuer_serial, 0)
        self.assertEqual(verified.intermediate.serial, 1)
        self.assertEqual(verified.leaf.serial, 2)

    def test_tampered_chain_fails_closed(self):
        now, root, chain = self.build_chain()
        tampered = bytearray(chain)
        tampered[-1] ^= 1
        with self.assertRaises(NoiseCertificateError):
            verify_noise_certificate_chain(
                bytes(tampered), root_public_key=root.public, root_serial=0, now=now
            )


if __name__ == "__main__":
    unittest.main()
