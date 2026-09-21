from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import types
import unittest

from postmaster.whatsapp_v990.adapter import CurrentProtocolAdapter, ProtocolCredentials
from postmaster.whatsapp_v990.binary import BinaryNode
from postmaster.whatsapp_v990.crypto import generate_curve_keypair
from postmaster.whatsapp_v990.groups import (
    WhatsAppGroupError,
    build_group_metadata_query,
    build_participating_groups_query,
    parse_group_metadata,
    parse_participating_groups,
)
from postmaster.whatsapp_v990.signal_keys import generate_registration_id, generate_signed_pre_key
from postmaster.whatsapp_v990.store import EncryptedAuthStore


class FakeWire:
    def __init__(self, response: BinaryNode):
        self.response = response
        self.closed = False
        self.queries: list[BinaryNode] = []

    async def query(self, node: BinaryNode, *, timeout: float = 30.0) -> BinaryNode:
        self.queries.append(node)
        return self.response


def group_node() -> BinaryNode:
    return BinaryNode(
        "group",
        {
            "id": "12345",
            "subject": "Postmaster test",
            "size": "2",
            "creation": "1700000000",
            "creator": "111@s.whatsapp.net",
            "addressing_mode": "lid",
        },
        [
            BinaryNode("participant", {"jid": "111@s.whatsapp.net", "type": "admin", "lid": "900@lid"}),
            BinaryNode("participant", {"jid": "222@s.whatsapp.net"}),
            BinaryNode(
                "description",
                {"id": "d1", "participant": "111@s.whatsapp.net", "t": "1700000100"},
                [BinaryNode("body", {}, b"hello group")],
            ),
            BinaryNode("announcement"),
            BinaryNode("ephemeral", {"expiration": "86400"}),
        ],
    )


class WhatsAppGroupsV990Tests(unittest.IsolatedAsyncioTestCase):
    def test_participating_query_matches_current_wg2_shape(self):
        node = build_participating_groups_query()
        self.assertEqual(node.tag, "iq")
        self.assertEqual(node.attrs, {"to": "@g.us", "xmlns": "w:g2", "type": "get"})
        participating = node.child("participating")
        self.assertIsNotNone(participating)
        self.assertEqual([child.tag for child in participating.children()], ["participants", "description"])

    def test_group_metadata_parser_preserves_participants_and_flags(self):
        parsed = parse_group_metadata(group_node())
        self.assertEqual(parsed["id"], "12345@g.us")
        self.assertEqual(parsed["subject"], "Postmaster test")
        self.assertEqual(parsed["addressing_mode"], "lid")
        self.assertEqual(parsed["description"], "hello group")
        self.assertTrue(parsed["announce"])
        self.assertEqual(parsed["ephemeral_duration"], 86400)
        self.assertEqual(len(parsed["participants"]), 2)
        self.assertEqual(parsed["participants"][0]["admin"], "admin")
        self.assertEqual(parsed["participants"][0]["lid"], "900@lid")

    def test_group_metadata_query_requires_group_jid(self):
        with self.assertRaises(WhatsAppGroupError):
            build_group_metadata_query("123@s.whatsapp.net")
        node = build_group_metadata_query("123@g.us")
        self.assertEqual(node.attrs["to"], "123@g.us")
        self.assertEqual(node.child("query").attrs["request"], "interactive")

    def test_participating_parser_surfaces_server_error(self):
        response = BinaryNode("iq", {"type": "error"}, [BinaryNode("error", {"code": "403", "text": "forbidden"})])
        with self.assertRaises(WhatsAppGroupError):
            parse_participating_groups(response)

    async def test_group_text_send_distributes_sender_key_once_then_reuses_it(self):
        metadata_response = BinaryNode(
            "iq",
            {"type": "result"},
            [
                BinaryNode(
                    "group",
                    {"id": "12345", "subject": "Test", "addressing_mode": "pn"},
                    [
                        BinaryNode("participant", {"jid": "111@s.whatsapp.net"}),
                        BinaryNode("participant", {"jid": "222@s.whatsapp.net"}),
                    ],
                )
            ],
        )
        usync_response = BinaryNode(
            "iq",
            {"type": "result"},
            [
                BinaryNode(
                    "usync",
                    {},
                    [
                        BinaryNode(
                            "list",
                            {},
                            [
                                BinaryNode(
                                    "user",
                                    {"jid": "111@s.whatsapp.net"},
                                    [
                                        BinaryNode(
                                            "devices",
                                            {},
                                            [
                                                BinaryNode(
                                                    "device-list",
                                                    {},
                                                    [
                                                        BinaryNode("device", {"id": "1", "key-index": "7"}),
                                                        BinaryNode("device", {"id": "7", "key-index": "9"}),
                                                    ],
                                                )
                                            ],
                                        )
                                    ],
                                ),
                                BinaryNode(
                                    "user",
                                    {"jid": "222@s.whatsapp.net"},
                                    [
                                        BinaryNode(
                                            "devices",
                                            {},
                                            [
                                                BinaryNode(
                                                    "device-list",
                                                    {},
                                                    [BinaryNode("device", {"id": "0"})],
                                                )
                                            ],
                                        )
                                    ],
                                ),
                            ],
                        )
                    ],
                )
            ],
        )

        class Signal:
            def encrypt(self, data):
                self.last = bytes(data)
                return "msg", b"pairwise-" + bytes(data[:4])

        class Sessions:
            def __init__(self, jids):
                self.items = {jid: Signal() for jid in jids}
            def load(self, jid):
                return self.items.get(jid)
            def save(self, jid, signal):
                self.items[jid] = signal

        class GroupWire:
            def __init__(self):
                self.closed = False
                self.queries = []
                self.sent = []
            async def query(self, node, *, timeout=30.0):
                self.queries.append(node)
                if node.attrs.get("xmlns") == "w:g2":
                    return metadata_response
                if node.attrs.get("xmlns") == "usync":
                    return usync_response
                raise AssertionError(f"unexpected query {node.attrs}")
            async def send_and_wait(self, node, *, response_tag=None, timeout=30.0):
                self.sent.append(node)
                return BinaryNode("ack", {"id": node.attrs["id"], "class": "message"})

        with TemporaryDirectory() as td:
            auth = EncryptedAuthStore(str(Path(td) / "auth.db"))
            adapter = CurrentProtocolAdapter(auth)
            identity = generate_curve_keypair()
            creds = ProtocolCredentials(
                noise=generate_curve_keypair(),
                identity=identity,
                signed_pre_key=generate_signed_pre_key(identity, 1),
                registration_id=generate_registration_id(),
                adv_secret_b64="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
                registered=True,
                jid="111:7@s.whatsapp.net",
            )
            adapter._save(creds)
            wire = GroupWire()
            adapter.session = wire  # type: ignore[assignment]
            adapter._connected = True

            async def fake_sessions(self, _wire, _creds, addresses):
                return Sessions(addresses)
            adapter._ensure_signal_sessions = types.MethodType(fake_sessions, adapter)  # type: ignore[method-assign]

            first = await adapter.send_text(jid="12345@g.us", text="hello group")
            self.assertTrue(first["server_ack"])
            self.assertEqual(first["sender_key_recipients"], 2)
            self.assertEqual(first["device_fanout"], 2)
            stanza = wire.sent[-1]
            self.assertEqual(stanza.attrs["to"], "12345@g.us")
            self.assertEqual(stanza.attrs["addressing_mode"], "pn")
            self.assertEqual(stanza.child("enc").attrs["type"], "skmsg")
            self.assertEqual(len(stanza.child("participants").children("to")), 2)

            second = await adapter.send_text(jid="12345@g.us", text="second")
            self.assertTrue(second["server_ack"])
            self.assertEqual(second["sender_key_recipients"], 0)
            stanza2 = wire.sent[-1]
            self.assertIsNone(stanza2.child("participants"))
            memory = auth.get_json("group-sender-key-memory", "12345@g.us")
            self.assertEqual(
                set(memory["devices"]),
                {"111:1@s.whatsapp.net", "222@s.whatsapp.net"},
            )
            self.assertTrue(adapter.status()["group_text_send_implemented"])
            self.assertFalse(adapter.status()["groups_ready"])

    async def test_adapter_list_groups_uses_live_session_query(self):
        response = BinaryNode("iq", {"type": "result"}, [BinaryNode("groups", {}, [group_node()])])
        with TemporaryDirectory() as td:
            auth = EncryptedAuthStore(str(Path(td) / "auth.db"))
            adapter = CurrentProtocolAdapter(auth)
            identity = generate_curve_keypair()
            creds = ProtocolCredentials(
                noise=generate_curve_keypair(),
                identity=identity,
                signed_pre_key=generate_signed_pre_key(identity, 1),
                registration_id=generate_registration_id(),
                adv_secret_b64="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
                registered=True,
                jid="111:7@s.whatsapp.net",
            )
            adapter._save(creds)
            wire = FakeWire(response)
            adapter.session = wire  # type: ignore[assignment]
            adapter._connected = True
            groups = await adapter.list_groups()
            self.assertEqual(groups[0]["id"], "12345@g.us")
            self.assertEqual(wire.queries[0].attrs["xmlns"], "w:g2")
            self.assertTrue(adapter.status()["group_listing_implemented"])
            self.assertFalse(adapter.status()["groups_ready"])


if __name__ == "__main__":
    unittest.main()
