from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from postmaster.whatsapp_v990.sender_key import (
    EncryptedSenderKeyStore,
    SenderKeyDistribution,
    SenderKeyError,
    SenderKeyMessage,
    SenderMessageKey,
    decode_sender_key_distribution_message,
    encode_sender_key_distribution_message,
)
from postmaster.whatsapp_v990.store import EncryptedAuthStore


class SenderKeyV990Tests(unittest.TestCase):
    def test_whisper_group_message_key_reference_vector(self):
        seed = bytes.fromhex("9b4c8120a4823a95f47cde17a244f4507244ee6e3957d1fab9fa29b44d3829b7")
        key = SenderMessageKey(7, seed)
        self.assertEqual(key.iv.hex(), "ed1f5e26325b1399f6a34c76e47ff047")
        self.assertEqual(
            key.cipher_key.hex(),
            "d89f10a08215e845ceb4df3fc59c052ad09e01cd499650025ff83df48ed656e6",
        )

    def test_distribution_wire_roundtrip(self):
        distribution = SenderKeyDistribution(
            key_id=123456,
            iteration=9,
            chain_key=bytes(range(32)),
            signing_public=bytes(reversed(range(32))),
        )
        raw = distribution.serialize()
        self.assertEqual(raw[0], 0x33)
        parsed = SenderKeyDistribution.parse(raw)
        self.assertEqual(parsed, distribution)

    def test_sender_key_message_signature_detects_tamper(self):
        from postmaster.whatsapp_v990.crypto import generate_curve_keypair

        pair = generate_curve_keypair()
        msg = SenderKeyMessage.create(
            key_id=1,
            iteration=0,
            ciphertext=b"cipher",
            signing_private=pair.private,
        )
        raw = msg.serialize()
        parsed = SenderKeyMessage.parse(raw)
        self.assertTrue(parsed.verify(pair.public))
        tampered = bytearray(raw)
        tampered[-1] ^= 1
        self.assertFalse(SenderKeyMessage.parse(bytes(tampered)).verify(pair.public))

    def test_sender_key_distribution_envelope_uses_message_field_two(self):
        distribution = SenderKeyDistribution(
            key_id=7,
            iteration=0,
            chain_key=b"c" * 32,
            signing_public=b"p" * 32,
        ).serialize()
        message = encode_sender_key_distribution_message("123@g.us", distribution)
        decoded = decode_sender_key_distribution_message(message)
        self.assertEqual(decoded, ("123@g.us", distribution))

    def test_two_party_encrypt_decrypt_and_out_of_order_cache(self):
        with TemporaryDirectory() as td:
            sender_auth = EncryptedAuthStore(str(Path(td) / "sender.db"))
            receiver_auth = EncryptedAuthStore(str(Path(td) / "receiver.db"))
            sender = EncryptedSenderKeyStore(sender_auth)
            receiver = EncryptedSenderKeyStore(receiver_auth)
            group = "123@g.us"
            author = "111:7@lid"

            receiver.process_distribution(group, author, sender.distribution(group, author))
            m0 = sender.encrypt(group, author, b"zero")
            m1 = sender.encrypt(group, author, b"two")
            m2 = sender.encrypt(group, author, b"four")

            # Current libsignal sender-key behavior uses 0,2,4... for new outbound messages.
            self.assertEqual(SenderKeyMessage.parse(m0).iteration, 0)
            self.assertEqual(SenderKeyMessage.parse(m1).iteration, 2)
            self.assertEqual(SenderKeyMessage.parse(m2).iteration, 4)

            self.assertEqual(receiver.decrypt(group, author, m2), b"four")
            self.assertEqual(receiver.decrypt(group, author, m0), b"zero")
            self.assertEqual(receiver.decrypt(group, author, m1), b"two")

            # Re-open from encrypted persistence; state remains usable and plaintext key
            # material is not present directly in the SQLite database.
            receiver2 = EncryptedSenderKeyStore(
                EncryptedAuthStore(str(Path(td) / "receiver.db"))
            )
            self.assertIsNotNone(receiver2.load(group, author))
            self.assertNotIn(b"WhisperGroup", (Path(td) / "receiver.db").read_bytes())

    def test_distribution_version_and_missing_state_fail_closed(self):
        with self.assertRaises(SenderKeyError):
            SenderKeyDistribution.parse(b"\x22bad")
        with TemporaryDirectory() as td:
            store = EncryptedSenderKeyStore(EncryptedAuthStore(str(Path(td) / "auth.db")))
            with self.assertRaises(SenderKeyError):
                store.decrypt("123@g.us", "111:7@lid", b"\x33" + b"x" * 80)


if __name__ == "__main__":
    unittest.main()
