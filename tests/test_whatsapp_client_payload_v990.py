from __future__ import annotations

import hashlib
import unittest

from postmaster.whatsapp_v990.client_payload import (
    CURRENT_WA_WEB_VERSION,
    DEVICE_PROPS_VERSION,
    RegistrationKeys,
    build_login_payload,
    build_registration_payload,
    parse_web_version,
)
from postmaster.whatsapp_v990.crypto import generate_curve_keypair
from postmaster.whatsapp_v990.proto import decode_fields
from postmaster.whatsapp_v990.signal_keys import generate_signed_pre_key


def fmap(raw: bytes):
    result = {}
    for field in decode_fields(raw):
        result.setdefault(field.number, []).append(field.value)
    return result


class WhatsAppClientPayloadV990Tests(unittest.TestCase):
    def test_current_version_and_parser(self):
        self.assertEqual(CURRENT_WA_WEB_VERSION, (2, 3000, 1048032155))
        self.assertEqual(parse_web_version("2.3000.1048032155-alpha"), CURRENT_WA_WEB_VERSION)
        self.assertEqual(parse_web_version([2, 3000, 42]), (2, 3000, 42))

    def test_registration_payload_uses_current_public_wire_fields(self):
        identity = generate_curve_keypair()
        signed = generate_signed_pre_key(identity, 1, random64=b"r" * 64)
        raw = build_registration_payload(
            RegistrationKeys(94, identity.public, signed),
            version=CURRENT_WA_WEB_VERSION,
            os_name="Linux",
        )
        top = fmap(raw)
        self.assertEqual(top[3][-1], 0)
        self.assertEqual(top[12][-1], 1)
        self.assertEqual(top[13][-1], 1)
        self.assertEqual(top[33][-1], 0)

        pairing = fmap(top[19][-1])
        self.assertEqual(pairing[1][-1], (94).to_bytes(4, "big"))
        self.assertEqual(pairing[2][-1], b"\x05")
        self.assertEqual(pairing[3][-1], identity.public)
        self.assertEqual(pairing[4][-1], (1).to_bytes(3, "big"))
        self.assertEqual(pairing[5][-1], signed.key_pair.public)
        self.assertEqual(pairing[6][-1], signed.signature)
        self.assertEqual(
            pairing[7][-1],
            hashlib.md5(b"2.3000.1048032155", usedforsecurity=False).digest(),
        )

        props = fmap(pairing[8][-1])
        self.assertEqual(props[1][-1], b"Linux")
        self.assertEqual(props[3][-1], 1)
        self.assertEqual(props[4][-1], 0)
        version = fmap(props[2][-1])
        self.assertEqual((version[1][-1], version[2][-1], version[3][-1]), DEVICE_PROPS_VERSION)

    def test_login_payload_fields(self):
        raw = build_login_payload(username=12345, device=7)
        top = fmap(raw)
        self.assertEqual(top[1][-1], 12345)
        self.assertEqual(top[3][-1], 1)
        self.assertEqual(top[18][-1], 7)
        self.assertEqual(top[33][-1], 1)
        self.assertEqual(top[41][-1], 0)


if __name__ == "__main__":
    unittest.main()
