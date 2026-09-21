from __future__ import annotations

import hashlib
import hmac
import unittest

from postmaster.whatsapp_v990.binary import BinaryNode
from postmaster.whatsapp_v990.crypto import generate_curve_keypair, xeddsa_sign, xeddsa_verify
from postmaster.whatsapp_v990.pair_success import (
    ADV_ENCRYPTION_E2EE,
    ADV_ENCRYPTION_HOSTED,
    PairSuccessError,
    WA_ADV_ACCOUNT_SIG_PREFIX,
    WA_ADV_DEVICE_SIG_PREFIX,
    WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX,
    configure_pair_success,
    decode_signed_device_identity,
)
from postmaster.whatsapp_v990.proto import field_bytes, field_varint


class PairSuccessV990Tests(unittest.TestCase):
    def make_stanza(self, *, hosted: bool = False, tamper_hmac: bool = False):
        identity = generate_curve_keypair()
        account = generate_curve_keypair()
        adv_secret = bytes(range(32))
        device_type = ADV_ENCRYPTION_HOSTED if hosted else ADV_ENCRYPTION_E2EE
        details = b"".join((
            field_varint(1, 123),
            field_varint(2, 1_790_000_000),
            field_varint(3, 7),
            field_varint(4, device_type),
            field_varint(5, device_type),
        ))
        prefix = WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX if hosted else WA_ADV_ACCOUNT_SIG_PREFIX
        account_sig = xeddsa_sign(account.private, prefix + details + identity.public, random64=b"a" * 64)
        signed = b"".join((
            field_bytes(1, details),
            field_bytes(2, account.public),
            field_bytes(3, account_sig),
        ))
        wrapper_prefix = WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX if hosted else b""
        digest = hmac.new(adv_secret, wrapper_prefix + signed, hashlib.sha256).digest()
        if tamper_hmac:
            digest = bytes([digest[0] ^ 1]) + digest[1:]
        wrapper = b"".join((
            field_bytes(1, signed),
            field_bytes(2, digest),
            field_varint(3, device_type),
        ))
        stanza = BinaryNode(
            "iq",
            {"id": "pair-1", "type": "set"},
            [
                BinaryNode(
                    "pair-success",
                    {},
                    [
                        BinaryNode("device-identity", {}, wrapper),
                        BinaryNode("device", {"jid": "12345:7@s.whatsapp.net", "lid": "999:7@lid"}),
                        BinaryNode("platform", {"name": "android"}),
                        BinaryNode("biz", {"name": "Controlled Test"}),
                    ],
                )
            ],
        )
        return identity, account, adv_secret, details, stanza

    def test_e2ee_pair_success_verifies_and_builds_signed_reply(self):
        identity, account, adv_secret, details, stanza = self.make_stanza()
        result = configure_pair_success(stanza, adv_secret_key=adv_secret, signed_identity_key=identity)
        self.assertEqual(result.jid, "12345:7@s.whatsapp.net")
        self.assertEqual(result.lid, "999:7@lid")
        self.assertEqual(result.platform, "android")
        self.assertEqual(result.business_name, "Controlled Test")
        self.assertEqual(result.device_identity.key_index, 7)
        self.assertEqual(result.signal_identity_key, account.public)
        signed_node = result.reply.child("pair-device-sign").child("device-identity")
        self.assertEqual(signed_node.attrs["key-index"], "7")
        encoded = decode_signed_device_identity(signed_node.content)
        self.assertIsNone(encoded.account_signature_key)
        device_msg = WA_ADV_DEVICE_SIG_PREFIX + details + identity.public + account.public
        self.assertTrue(xeddsa_verify(identity.public, device_msg, encoded.device_signature))

    def test_hosted_pair_success_uses_hosted_prefixes(self):
        identity, account, adv_secret, _details, stanza = self.make_stanza(hosted=True)
        result = configure_pair_success(stanza, adv_secret_key=adv_secret, signed_identity_key=identity)
        self.assertEqual(result.device_identity.device_type, ADV_ENCRYPTION_HOSTED)
        self.assertEqual(result.signal_identity_key, account.public)

    def test_tampered_hmac_fails_closed(self):
        identity, _account, adv_secret, _details, stanza = self.make_stanza(tamper_hmac=True)
        with self.assertRaisesRegex(PairSuccessError, "HMAC"):
            configure_pair_success(stanza, adv_secret_key=adv_secret, signed_identity_key=identity)


if __name__ == "__main__":
    unittest.main()
