from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from postmaster.whatsapp_v990.adapter import CurrentProtocolAdapter
from postmaster.whatsapp_v990.binary import BinaryNode
from postmaster.whatsapp_v990.store import EncryptedAuthStore


class FakeSession:
    def __init__(self, nodes):
        self.nodes=list(nodes)
        self.sent=[]
        self.closed=False

    async def recv_node(self, *, timeout=30.0):
        if not self.nodes:
            await asyncio.sleep(3600)
        return self.nodes.pop(0)

    async def send_node(self, node):
        self.sent.append(node)

    async def close(self):
        self.closed=True


class WhatsAppAdapterV990Tests(unittest.IsolatedAsyncioTestCase):
    def store(self, td):
        return EncryptedAuthStore(str(Path(td)/"auth.db"), key_path=str(Path(td)/"auth.key"))

    async def test_construction_and_status_do_not_open_network(self):
        with TemporaryDirectory() as td:
            called=0
            async def opener(**kwargs):
                nonlocal called
                called += 1
                raise AssertionError("network/session opener must not run during construction or status")
            adapter=CurrentProtocolAdapter(self.store(td), session_opener=opener)
            status=adapter.status()
            self.assertEqual(called,0)
            self.assertTrue(status["configured"])
            self.assertFalse(status["connected"])
            self.assertFalse(status["paired"])
            self.assertFalse(status["signal_send_ready"])

    async def test_explicit_pairing_returns_qr_and_persists_private_material_only_encrypted(self):
        with TemporaryDirectory() as td:
            session=FakeSession([
                BinaryNode(
                    "iq",
                    {"id":"pair-1","type":"set"},
                    [BinaryNode("pair-device",{},[
                        BinaryNode("ref",{},b"ref-one"),
                        BinaryNode("ref",{},b"ref-two"),
                    ])],
                )
            ])
            calls=[]
            async def opener(**kwargs):
                calls.append(kwargs)
                return session
            auth=self.store(td)
            adapter=CurrentProtocolAdapter(auth, session_opener=opener)
            result=await adapter.start_pairing()
            self.assertTrue(result["ok"])
            self.assertTrue(result["qr"].startswith("https://wa.me/settings/linked_devices#"))
            self.assertNotIn("private_key",result)
            self.assertFalse(result["private_material_exposed"])
            self.assertEqual(len(calls),1)
            self.assertTrue(calls[0]["client_payload"])
            self.assertEqual(len(calls[0]["noise_static"].private),32)
            self.assertEqual(session.sent[0].tag,"iq")
            self.assertEqual(session.sent[0].attrs["type"],"result")
            self.assertIsNotNone(auth.get_json("protocol","credentials"))
            raw=(Path(td)/"auth.db").read_bytes()
            self.assertNotIn(calls[0]["noise_static"].private,raw)
            self.assertTrue(adapter.status()["pairing_pending"])
            await adapter.close()
            self.assertTrue(session.closed)

    async def test_reconnect_without_pairing_fails_closed_without_network(self):
        with TemporaryDirectory() as td:
            calls=0
            async def opener(**kwargs):
                nonlocal calls
                calls += 1
                raise AssertionError("should not open a session without paired credentials")
            adapter=CurrentProtocolAdapter(self.store(td), session_opener=opener)
            with self.assertRaisesRegex(Exception,"No paired WhatsApp session"):
                await adapter.reconnect()
            self.assertEqual(calls,0)


if __name__ == "__main__":
    unittest.main()
