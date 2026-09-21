from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import stat
from tempfile import TemporaryDirectory
import unittest

from postmaster.whatsapp_v990.binary import BinaryNode, BinaryNodeCodec, BinaryNodeError, TokenTable
from postmaster.whatsapp_v990.jid import JID, parse_jid, same_user, transfer_device
from postmaster.whatsapp_v990.proto import decode_fields, field_bytes, field_message, field_varint
from postmaster.whatsapp_v990.receipts import receipt_for_event
from postmaster.whatsapp_v990.store import EncryptedAuthStore
from postmaster.whatsapp_v990.transport import ReconnectingTransport, ReconnectPolicy, TransportError


class JIDAndBinaryTests(unittest.TestCase):
    def test_jid_device_agent_and_normalization(self):
        jid = parse_jid("12345_7:42@c.us")
        self.assertEqual(jid.user, "12345")
        self.assertEqual(jid.agent, 7)
        self.assertEqual(jid.device, 42)
        self.assertEqual(str(jid), "12345_7:42@c.us")
        self.assertEqual(str(jid.normalized_user()), "12345@s.whatsapp.net")
        self.assertTrue(same_user("12345:1@s.whatsapp.net", "12345:9@s.whatsapp.net"))
        self.assertEqual(str(transfer_device("12345:9@s.whatsapp.net", "999@g.us")), "999:9@g.us")

    def test_binary_node_roundtrip_raw_jid_bytes_children_and_compression(self):
        codec = BinaryNodeCodec()
        node = BinaryNode(
            "iq",
            {"id": "123.45", "to": "12345:7@s.whatsapp.net", "type": "get"},
            [BinaryNode("query", {"xmlns": "w:g2"}), BinaryNode("payload", {}, b"\x00\x01hello")],
        )
        for compressed in (False, True):
            encoded = codec.encode(node, compressed=compressed)
            decoded = codec.decode(encoded)
            self.assertEqual(decoded.tag, "iq")
            self.assertEqual(decoded.attrs["to"], "12345:7@s.whatsapp.net")
            self.assertEqual(decoded.children()[0].attrs["xmlns"], "w:g2")
            self.assertEqual(decoded.children()[1].content, b"\x00\x01hello")

    def test_binary_codec_uses_injected_token_table_and_rejects_unknown(self):
        tokens = TokenTable([None, "iq", "type", "get"], [["query"]], version=3)
        codec = BinaryNodeCodec(tokens)
        encoded = codec.encode(BinaryNode("iq", {"type": "get"}, [BinaryNode("query")]))
        self.assertEqual(codec.decode(encoded).children()[0].tag, "query")
        broken = bytearray(encoded)
        # Root list prefix is 0, LIST_8, size, then tag token. Replace tag token with unknown 200.
        broken[3] = 200
        with self.assertRaises(BinaryNodeError):
            codec.decode(bytes(broken))


class CurrentTokenTableTests(unittest.TestCase):
    def test_pair_device_tokens_are_pinned_to_current_snapshot(self):
        self.assertEqual(CURRENT_TOKEN_TABLE.version, 20260921)
        self.assertEqual(CURRENT_TOKEN_TABLE.token_for("iq"), (None, 25))
        self.assertEqual(CURRENT_TOKEN_TABLE.token_for("pair-device"), (1, 238))
        self.assertEqual(CURRENT_TOKEN_TABLE.token_for("ref"), (1, 80))

    def test_fb_and_interop_jid_wire_tags_decode(self):
        codec = BinaryNodeCodec(CURRENT_TOKEN_TABLE)
        # FB_JID: user string, 16-bit device, server string.
        fb = bytes([246, 252, 3]) + b"123" + bytes([0, 7, 3])
        value, pos = codec._read_string(fb, 1, first=246)
        self.assertEqual(value, "123:7@s.whatsapp.net")
        self.assertEqual(pos, len(fb))

        # INTEROP_JID: user string, 16-bit device, 16-bit integrator, optional server.
        interop = bytes([245, 252, 3]) + b"456" + bytes([0, 2, 0, 9, 3])
        value, pos = codec._read_string(interop, 1, first=245)
        self.assertEqual(value, "9-456:2@s.whatsapp.net")
        self.assertEqual(pos, len(interop))


class ProtoTests(unittest.TestCase):
    def test_minimal_wire_codec_roundtrip(self):
        nested = field_varint(1, 7) + field_bytes(2, "desktop")
        raw = field_varint(1, 300) + field_bytes(2, b"abc") + field_message(3, [nested])
        fields = decode_fields(raw)
        self.assertEqual(fields[0].value, 300)
        self.assertEqual(fields[1].value, b"abc")
        nested_fields = decode_fields(fields[2].value)
        self.assertEqual([x.number for x in nested_fields], [1, 2])


class StoreAndReceiptTests(unittest.TestCase):
    def test_encrypted_store_persists_without_plaintext_or_key_exposure(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "wa.db"
            store = EncryptedAuthStore(str(db))
            secret = {"jid":"123@s.whatsapp.net", "private":"super-secret-private-material"}
            store.put_json("creds", "primary", secret)
            self.assertEqual(store.get_json("creds", "primary"), secret)
            raw_db = db.read_bytes()
            self.assertNotIn(b"super-secret-private-material", raw_db)
            self.assertEqual(stat.S_IMODE((Path(str(db)+".key")).stat().st_mode) & 0o077, 0)
            status = store.status()
            self.assertTrue(status["encrypted_at_rest"])
            self.assertFalse(status["private_material_exposed"])

    def test_receipts_are_asymmetric(self):
        self.assertFalse(receipt_for_event(event="read").emit)
        self.assertFalse(receipt_for_event(event="send", outbound_action=True).emit)
        reply = receipt_for_event(event="reply", outbound_action=True, reply_to_message=True)
        self.assertTrue(reply.emit)
        self.assertEqual(reply.receipt_type, "read")


class FakeSocket:
    def __init__(self): self.sent=[]; self.incoming=[b"ok"]; self.closed=False
    async def send(self, data): self.sent.append(bytes(data))
    async def recv(self): return self.incoming.pop(0)
    async def close(self): self.closed=True


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_then_send_receive_close(self):
        calls = 0
        sock = FakeSocket()
        async def connector():
            nonlocal calls
            calls += 1
            if calls == 1: raise OSError("temporary")
            return sock
        sleeps=[]
        async def no_sleep(delay): sleeps.append(delay)
        transport = ReconnectingTransport(connector, policy=ReconnectPolicy(base_seconds=0.1, jitter=0))
        await transport.ensure_connected(max_attempts=2, sleep=no_sleep)
        self.assertTrue(transport.connected)
        self.assertEqual(transport.reconnects, 1)
        self.assertEqual(sleeps, [0.1])
        await transport.send(b"hello")
        self.assertEqual(sock.sent, [b"hello"])
        self.assertEqual(await transport.recv(), b"ok")
        await transport.close()
        self.assertTrue(sock.closed)
        self.assertFalse(transport.connected)

    async def test_send_requires_connection(self):
        async def connector(): return FakeSocket()
        transport = ReconnectingTransport(connector)
        with self.assertRaises(TransportError):
            await transport.send(b"x")


if __name__ == "__main__":
    unittest.main()

from postmaster.whatsapp_v990.handshake import (
    HandshakeError, HandshakeMessage, ServerHello, decode_handshake, encode_client_finish, encode_client_hello, encode_handshake,
)
from postmaster.whatsapp_v990.pairing import PAIRING_QR_PREFIX, PairingQR, build_pairing_qr_data, companion_web_client_type
from postmaster.whatsapp_v990.websocket_driver import WebSocketDriverConfig, WebSocketDriverError, open_whatsapp_websocket
from postmaster.whatsapp_v990.tokens import CURRENT_TOKEN_TABLE


class HandshakeAndPairingTests(unittest.IsolatedAsyncioTestCase):
    def test_handshake_client_hello_and_server_hello_wire_fields(self):
        eph = bytes(range(32))
        raw = encode_client_hello(eph)
        top = decode_fields(raw)
        self.assertEqual(top[0].number, 2)
        decoded = decode_handshake(raw)
        self.assertEqual(decoded.client_hello.ephemeral, eph)

        server = encode_handshake(HandshakeMessage(server_hello=ServerHello(eph, b"static", b"payload")))
        parsed = decode_handshake(server)
        self.assertEqual(parsed.server_hello.static, b"static")
        self.assertEqual(parsed.server_hello.payload, b"payload")

    def test_handshake_client_finish_and_validation(self):
        raw = encode_client_finish(b"encrypted-static", b"encrypted-payload")
        parsed = decode_handshake(raw)
        self.assertEqual(parsed.client_finish.static, b"encrypted-static")
        self.assertEqual(parsed.client_finish.payload, b"encrypted-payload")
        with self.assertRaises(HandshakeError):
            encode_client_hello(b"too-short")

    def test_current_companion_qr_shape_and_public_status(self):
        payload = build_pairing_qr_data(
            "ref-token", b"n" * 32, b"i" * 32, "adv-secret-b64", os_name="Linux", browser_name="Chrome"
        )
        self.assertTrue(payload.startswith(PAIRING_QR_PREFIX))
        fragment = payload.split("#", 1)[1]
        self.assertEqual(len(fragment.split(",")), 5)
        self.assertEqual(fragment.split(",")[-1], "1")
        self.assertEqual(companion_web_client_type("Windows", "Desktop"), 8)
        self.assertEqual(companion_web_client_type("Linux", "Desktop"), 7)
        status = PairingQR(payload).public_status()
        self.assertTrue(status["available"])
        self.assertFalse(status["payload_exposed_in_status"])
        self.assertNotIn("payload", status)

    async def test_websocket_driver_uses_current_endpoint_origin_and_can_be_injected(self):
        calls = []
        class Sock: pass
        async def connector(*args, **kwargs):
            calls.append((args, kwargs)); return Sock()
        sock = await open_whatsapp_websocket(connect_impl=connector)
        self.assertIsInstance(sock, Sock)
        self.assertEqual(calls[0][0][0], "wss://web.whatsapp.com/ws/chat")
        self.assertEqual(calls[0][1]["origin"], "https://web.whatsapp.com")
        with self.assertRaises(WebSocketDriverError):
            await open_whatsapp_websocket(WebSocketDriverConfig(url="ws://insecure.invalid"), connect_impl=connector)

from postmaster.whatsapp_v990.crypto import generate_curve_keypair, signal_public_key
from postmaster.whatsapp_v990.signal_keys import (
    chain_message_seed, derive_message_keys, derive_x3dh_initiator, derive_x3dh_responder,
    generate_registration_id, generate_signed_pre_key, next_chain_key, verify_signed_pre_key,
)
from postmaster.whatsapp_v990.signal_wire import PreKeyWhisperMessageV3, SignalMessageV3, SignalWireError


class SignalV3WireAndKeyTests(unittest.TestCase):
    def test_signed_prekey_matches_whatsapp_bundle_shape(self):
        identity = generate_curve_keypair()
        signed_pair = generate_curve_keypair()
        signed = generate_signed_pre_key(identity, 1, pre_key=signed_pair, random64=b"r" * 64)
        self.assertEqual(signed.key_id, 1)
        self.assertTrue(verify_signed_pre_key(identity.public, signed.key_pair.public, signed.signature))
        self.assertFalse(verify_signed_pre_key(generate_curve_keypair().public, signed.key_pair.public, signed.signature))
        self.assertEqual(generate_registration_id(b"\xff\xff"), 0x3FFF)

    def test_x3dh_initiator_responder_match_with_and_without_one_time_prekey(self):
        alice_identity = generate_curve_keypair()
        alice_base = generate_curve_keypair()
        bob_identity = generate_curve_keypair()
        bob_signed = generate_curve_keypair()
        bob_one = generate_curve_keypair()
        for one_time in (None, bob_one):
            a = derive_x3dh_initiator(
                our_identity_private=alice_identity.private,
                our_base_private=alice_base.private,
                their_identity_public=bob_identity.public,
                their_signed_pre_key_public=bob_signed.public,
                their_one_time_pre_key_public=None if one_time is None else one_time.public,
            )
            b = derive_x3dh_responder(
                our_identity_private=bob_identity.private,
                our_signed_pre_key_private=bob_signed.private,
                their_identity_public=alice_identity.public,
                their_base_public=alice_base.public,
                our_one_time_pre_key_private=None if one_time is None else one_time.private,
            )
            self.assertEqual(a, b)
            self.assertEqual(len(a[0]), 32)
            self.assertEqual(len(a[1]), 32)

    def test_signal_chain_key_reference_vector(self):
        chain_key = bytes.fromhex("8ab72d6f4cc5ac0d387eaf463378ddb28edd07385b1cb01250c715982e7ad48f")
        cipher_key, mac_key, _iv = derive_message_keys(chain_key)
        self.assertEqual(cipher_key.hex(), "bf51e9d75e0e31031051f82a2491ffc084fa298b7793bd9db620056febf45217")
        self.assertEqual(mac_key.hex(), "c6c77d6a73a354337a56435e34607dfe48e3ace14e77314dc6abc172e7a7030b")
        self.assertEqual(next_chain_key(chain_key).hex(), "28e8f8fee54b801eef7c5cfb2f17f32c7b334485bbb70fac6ec10342a246d15d")
        self.assertNotEqual(chain_message_seed(chain_key), next_chain_key(chain_key))

    def test_signal_v3_message_wire_and_mac(self):
        ratchet = generate_curve_keypair()
        sender_id = generate_curve_keypair()
        receiver_id = generate_curve_keypair()
        mac_key = b"m" * 32
        message = SignalMessageV3(ratchet.public, 7, 3, b"ciphertext")
        raw = message.serialize(mac_key=mac_key, sender_identity_key=sender_id.public, receiver_identity_key=receiver_id.public)
        self.assertEqual(raw[0], 0x33)
        parsed = SignalMessageV3.parse(raw)
        self.assertEqual(parsed.ratchet_key, signal_public_key(ratchet.public))
        self.assertEqual(parsed.counter, 7)
        self.assertEqual(parsed.previous_counter, 3)
        self.assertTrue(parsed.verify_mac(mac_key=mac_key, sender_identity_key=sender_id.public, receiver_identity_key=receiver_id.public))
        tampered = raw[:-1] + bytes([raw[-1] ^ 1])
        self.assertFalse(SignalMessageV3.parse(tampered).verify_mac(mac_key=mac_key, sender_identity_key=sender_id.public, receiver_identity_key=receiver_id.public))

    def test_prekey_whisper_v3_roundtrip_and_version_rejection(self):
        base = generate_curve_keypair()
        identity = generate_curve_keypair()
        nested = b"\x33nested-signal-message"
        msg = PreKeyWhisperMessageV3(
            registration_id=1234,
            pre_key_id=9,
            signed_pre_key_id=1,
            base_key=base.public,
            identity_key=identity.public,
            message=nested,
        )
        raw = msg.serialize()
        parsed = PreKeyWhisperMessageV3.parse(raw)
        self.assertEqual(parsed.registration_id, 1234)
        self.assertEqual(parsed.pre_key_id, 9)
        self.assertEqual(parsed.base_key, signal_public_key(base.public))
        self.assertEqual(parsed.message, nested)
        with self.assertRaises(SignalWireError):
            PreKeyWhisperMessageV3.parse(bytes([0x44]) + raw[1:])
