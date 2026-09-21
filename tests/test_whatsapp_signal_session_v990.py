from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from postmaster.whatsapp_v990.crypto import generate_curve_keypair
from postmaster.whatsapp_v990.signal_keys import SignalPreKeyBundle, generate_signed_pre_key, signal_public_key
from postmaster.whatsapp_v990.signal_session import (
    EncryptedSignalSessionStore,
    SignalSession,
    decrypt_prekey_message,
    initialize_outgoing_session,
)
from postmaster.whatsapp_v990.signal_wire import PreKeyWhisperMessageV3
from postmaster.whatsapp_v990.store import EncryptedAuthStore


class SignalSessionV990Tests(unittest.TestCase):
    def make_pair(self):
        alice_identity = generate_curve_keypair()
        bob_identity = generate_curve_keypair()
        bob_signed = generate_signed_pre_key(bob_identity, 17)
        bob_one_time = generate_curve_keypair()
        bundle = SignalPreKeyBundle(
            registration_id=222,
            identity_key=bob_identity.public,
            signed_pre_key_id=bob_signed.key_id,
            signed_pre_key=bob_signed.key_pair.public,
            signed_pre_key_signature=bob_signed.signature,
            pre_key_id=7,
            pre_key=bob_one_time.public,
        )
        alice = initialize_outgoing_session(
            our_identity=alice_identity,
            our_registration_id=111,
            bundle=bundle,
        )
        return alice, bob_identity, bob_signed, bob_one_time

    def test_prekey_then_bidirectional_ratchet_and_out_of_order_messages(self):
        alice, bob_identity, bob_signed, bob_one_time = self.make_pair()

        kind, first = alice.encrypt(b"hello bob")
        self.assertEqual(kind, "pkmsg")
        envelope = PreKeyWhisperMessageV3.parse(first)
        self.assertEqual(envelope.registration_id, 111)
        self.assertEqual(envelope.pre_key_id, 7)
        self.assertEqual(envelope.signed_pre_key_id, 17)

        bob, plaintext, consumed_pre_key = decrypt_prekey_message(
            first,
            our_identity=bob_identity,
            our_signed_pre_key=bob_signed,
            our_one_time_pre_key=bob_one_time,
            our_registration_id=222,
        )
        self.assertEqual(plaintext, b"hello bob")
        self.assertEqual(consumed_pre_key, 7)
        self.assertEqual(bob.registration_id, 111)
        self.assertEqual(bob.local_registration_id, 222)

        kind, reply = bob.encrypt(b"hello alice")
        self.assertEqual(kind, "msg")
        self.assertEqual(alice.decrypt_signal(reply), b"hello alice")
        self.assertIsNone(alice.pending_pre_key)

        emitted = []
        for value in (b"zero", b"one", b"two"):
            kind, wire = alice.encrypt(value)
            self.assertEqual(kind, "msg")
            emitted.append(wire)

        # Deliver newest first: the receiving chain must cache skipped message keys.
        self.assertEqual(bob.decrypt_signal(emitted[2]), b"two")
        self.assertEqual(bob.decrypt_signal(emitted[0]), b"zero")
        self.assertEqual(bob.decrypt_signal(emitted[1]), b"one")
        with self.assertRaisesRegex(Exception, "already used|never derived"):
            bob.decrypt_signal(emitted[1])

    def test_session_json_and_encrypted_store_roundtrip(self):
        alice, _bob_identity, _bob_signed, _bob_one_time = self.make_pair()
        restored = SignalSession.from_json(alice.to_json())
        self.assertEqual(restored.root_key, alice.root_key)
        self.assertEqual(restored.ratchet_key.private, alice.ratchet_key.private)
        self.assertEqual(restored.pending_pre_key.pre_key_id, 7)

        with TemporaryDirectory() as td:
            auth = EncryptedAuthStore(
                str(Path(td) / "auth.db"),
                key_path=str(Path(td) / "auth.key"),
            )
            store = EncryptedSignalSessionStore(auth)
            store.save("123.1", alice)
            loaded = store.load("123.1")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.ratchet_key.private, alice.ratchet_key.private)
            raw = (Path(td) / "auth.db").read_bytes()
            self.assertNotIn(alice.ratchet_key.private, raw)
            self.assertTrue(store.delete("123.1"))
            self.assertIsNone(store.load("123.1"))


if __name__ == "__main__":
    unittest.main()
