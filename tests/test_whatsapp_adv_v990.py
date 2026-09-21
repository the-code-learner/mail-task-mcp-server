from __future__ import annotations

import hashlib
import hmac
import unittest

from postmaster.whatsapp_v990.adv import (
    ADVDeviceIdentity,
    ADVSignedDeviceIdentity,
    ADVSignedDeviceIdentityHMAC,
    ADV_E2EE,
    ADVError,
    WA_ADV_ACCOUNT_SIG_PREFIX,
    WA_ADV_DEVICE_SIG_PREFIX,
    decode_signed_device_identity,
    encode_adv_device_identity,
    encode_signed_device_identity,
    encode_signed_device_identity_hmac,
    verify_and_sign_pair_success_identity,
)
from postmaster.whatsapp_v990.crypto import generate_curve_keypair, xeddsa_sign, xeddsa_verify


class WhatsAppAdvV990Tests(unittest.TestCase):
    def build_fixture(self):
        signed_identity = generate_curve_keypair()
        account_key = generate_curve_keypair()
        device_details = encode_adv_device_identity(
            ADVDeviceIdentity(raw_id=11, timestamp=1720000000, key_index=7, account_type=ADV_E2EE, device_type=ADV_E2EE)
        )
        account_message = WA_ADV_ACCOUNT_SIG_PREFIX + device_details + signed_identity.public
        account_signature = xeddsa_sign(account_key.private, account_message, random64=b"a" * 64)
        account = ADVSignedDeviceIdentity(
            details=device_details,
            account_signature_key=account_key.public,
            account_signature=account_signature,
        )
        account_blob = encode_signed_device_identity(account, include_signature_key=True)
        adv_secret = bytes(range(32))
        mac = hmac.new(adv_secret, account_blob, hashlib.sha256).digest()
        outer = encode_signed_device_identity_hmac(
            ADVSignedDeviceIdentityHMAC(details=account_blob, hmac_value=mac, account_type=ADV_E2EE)
        )
        return signed_identity, account_key, adv_secret, device_details, outer

    def test_pair_success_hmac_account_and_device_signatures(self):
        signed_identity, account_key, adv_secret, device_details, outer = self.build_fixture()
        verified = verify_and_sign_pair_success_identity(
            outer,
            adv_secret_key=adv_secret,
            signed_identity_key=signed_identity,
            random64=b"b" * 64,
        )
        self.assertEqual(verified.device_identity.key_index, 7)
        self.assertTrue(verified.hmac_verified)
        self.assertTrue(verified.account_signature_verified)
        reply = decode_signed_device_identity(verified.encoded_for_reply)
        self.assertIsNone(reply.account_signature_key)
        self.assertIsNotNone(reply.device_signature)
        device_message = (
            WA_ADV_DEVICE_SIG_PREFIX
            + device_details
            + signed_identity.public
            + account_key.public
        )
        self.assertTrue(xeddsa_verify(signed_identity.public, device_message, reply.device_signature))

    def test_tampered_hmac_fails_closed(self):
        signed_identity, _account_key, adv_secret, _details, outer = self.build_fixture()
        tampered = bytearray(outer)
        tampered[-1] ^= 1
        with self.assertRaises(ADVError):
            verify_and_sign_pair_success_identity(
                bytes(tampered),
                adv_secret_key=adv_secret,
                signed_identity_key=signed_identity,
            )


if __name__ == "__main__":
    unittest.main()
