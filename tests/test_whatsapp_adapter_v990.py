from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from postmaster.whatsapp_v990.adapter import CurrentProtocolAdapter, ProtocolCredentials
from postmaster.whatsapp_v990.binary import BinaryNode
from postmaster.whatsapp_v990.crypto import generate_curve_keypair
from postmaster.whatsapp_v990.messages import encode_text_message, pad_random_max16
from postmaster.whatsapp_v990.signal_keys import SignalPreKeyBundle, generate_signed_pre_key
from postmaster.whatsapp_v990.signal_session import EncryptedSignalSessionStore, initialize_outgoing_session
from postmaster.whatsapp_v990.store import EncryptedAuthStore


class FakeSession:
    def __init__(self, nodes, query_responses=None):
        self.nodes=list(nodes)
        self.query_responses=list(query_responses or [])
        self.queries=[]
        self.sent=[]
        self.closed=False

    async def recv_node(self, *, timeout=30.0):
        if not self.nodes:
            await asyncio.sleep(3600)
        return self.nodes.pop(0)

    async def send_node(self, node):
        self.sent.append(node)

    async def query(self, node, *, timeout=30.0):
        self.queries.append(node)
        if not self.query_responses:
            raise AssertionError("unexpected query without prepared response")
        return self.query_responses.pop(0)

    async def send_and_wait(self, node, *, response_tag=None, timeout=30.0):
        await self.send_node(node)
        response=await self.recv_node(timeout=timeout)
        if response_tag is not None and response.tag != response_tag:
            raise AssertionError(f"unexpected correlated response tag {response.tag}")
        return response

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


    def _usync_direct_result(self):
        return BinaryNode("iq",{"id":"u1","type":"result"},[
            BinaryNode("usync",{},[
                BinaryNode("list",{},[
                    BinaryNode("user",{"jid":"111@s.whatsapp.net"},[
                        BinaryNode("lid",{"val":"999@lid"}),
                        BinaryNode("devices",{},[
                            BinaryNode("device-list",{},[
                                BinaryNode("device",{"id":"0"}),
                                BinaryNode("device",{"id":"2","key-index":"7"}),
                            ])
                        ]),
                    ]),
                    BinaryNode("user",{"jid":"222@s.whatsapp.net"},[
                        BinaryNode("lid",{"val":"888@lid"}),
                        BinaryNode("devices",{},[
                            BinaryNode("device-list",{},[
                                BinaryNode("device",{"id":"0"}),
                                BinaryNode("device",{"id":"3","key-index":"8"}),
                            ])
                        ]),
                    ]),
                ])
            ])
        ])

    def _connected_direct_adapter(self, td, *, reply_ack_id=None):
        auth=self.store(td)
        identity=generate_curve_keypair()
        creds=ProtocolCredentials(
            noise=generate_curve_keypair(),
            identity=identity,
            signed_pre_key=generate_signed_pre_key(identity,1),
            registration_id=111,
            adv_secret_b64=base64.b64encode(bytes(range(32))).decode("ascii"),
            registered=True,
            jid="111:2@s.whatsapp.net",
            lid="999:2@lid",
            account_identity_b64=base64.b64encode(b"adv-device-identity").decode("ascii"),
        )
        ack=BinaryNode("ack",{"id":reply_ack_id or "placeholder","class":"message"})
        session=FakeSession([ack],[self._usync_direct_result()])
        adapter=CurrentProtocolAdapter(auth)
        adapter._save(creds)
        adapter.session=session
        adapter._connected=True

        sessions=EncryptedSignalSessionStore(auth)
        for address in ("999@lid","888@lid","888:3@lid"):
            remote_identity=generate_curve_keypair()
            remote_signed=generate_signed_pre_key(remote_identity,17)
            bundle=SignalPreKeyBundle(
                registration_id=222,
                identity_key=remote_identity.public,
                signed_pre_key_id=remote_signed.key_id,
                signed_pre_key=remote_signed.key_pair.public,
                signed_pre_key_signature=remote_signed.signature,
            )
            sessions.save(
                address,
                initialize_outgoing_session(
                    our_identity=creds.identity,
                    our_registration_id=creds.registration_id,
                    bundle=bundle,
                ),
            )
        return adapter,session

    async def test_direct_text_fans_out_to_remote_and_other_own_devices_and_waits_for_ack(self):
        with TemporaryDirectory() as td:
            adapter,session=self._connected_direct_adapter(td)
            # Message ids are random; make the prepared ACK follow the outgoing stanza id.
            original_send=session.send_node
            async def send_and_ack(node):
                await original_send(node)
                if node.tag=="message":
                    session.nodes[0].attrs["id"]=node.attrs["id"]
            session.send_node=send_and_ack

            result=await adapter.send_text(jid="222@s.whatsapp.net",text="hello",emit_read_receipt=False)
            self.assertTrue(result["ok"])
            self.assertTrue(result["server_ack"])
            self.assertEqual(result["device_fanout"],3)
            self.assertEqual(result["own_device_targets"],1)
            self.assertEqual(result["remote_device_targets"],2)
            self.assertTrue(result["used_prekey_message"])
            self.assertFalse(result["read_receipt_emitted"])

            self.assertEqual(len(session.queries),1)
            self.assertEqual(session.queries[0].attrs["xmlns"],"usync")
            stanza=session.sent[0]
            self.assertEqual(stanza.tag,"message")
            self.assertEqual(stanza.attrs["to"],"222@s.whatsapp.net")
            participants=stanza.child("participants").children("to")
            self.assertEqual(
                {node.attrs["jid"] for node in participants},
                {"999@lid","888@lid","888:3@lid"},
            )
            self.assertNotIn("999:2@lid",{node.attrs["jid"] for node in participants})
            self.assertEqual(stanza.child("device-identity").content,b"adv-device-identity")

    async def test_reply_emits_read_receipt_only_after_message_ack(self):
        with TemporaryDirectory() as td:
            adapter,session=self._connected_direct_adapter(td)
            order=[]
            original_send=session.send_node
            async def send_and_ack(node):
                order.append(node.tag)
                await original_send(node)
                if node.tag=="message":
                    session.nodes[0].attrs["id"]=node.attrs["id"]
            session.send_node=send_and_ack

            result=await adapter.send_text(
                jid="222@s.whatsapp.net",
                text="reply",
                reply_to_message_id="incoming-1",
                emit_read_receipt=True,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["read_receipt_emitted"])
            self.assertEqual(order,["message","receipt"])
            receipt=session.sent[-1]
            self.assertEqual(receipt.attrs["id"],"incoming-1")
            self.assertEqual(receipt.attrs["type"],"read")


    async def test_incoming_pkmsg_decrypts_persists_and_transport_acks_without_read_receipt(self):
        with TemporaryDirectory() as td:
            adapter,session=self._connected_direct_adapter(td)
            creds=adapter._load()
            self.assertIsNotNone(creds)
            messages=[]
            receipts=[]
            adapter.on_message=lambda **kwargs: messages.append(kwargs)
            adapter.on_receipt=lambda **kwargs: receipts.append(kwargs)

            one_time=generate_curve_keypair()
            adapter._store_pre_key(7,one_time)
            remote_identity=generate_curve_keypair()
            bundle=SignalPreKeyBundle(
                registration_id=creds.registration_id,
                identity_key=creds.identity.public,
                signed_pre_key_id=creds.signed_pre_key.key_id,
                signed_pre_key=creds.signed_pre_key.key_pair.public,
                signed_pre_key_signature=creds.signed_pre_key.signature,
                pre_key_id=7,
                pre_key=one_time.public,
            )
            sender=initialize_outgoing_session(
                our_identity=remote_identity,
                our_registration_id=222,
                bundle=bundle,
            )
            kind,ciphertext=sender.encrypt(
                pad_random_max16(encode_text_message("incoming hello"),random1=b"\x00")
            )
            self.assertEqual(kind,"pkmsg")
            session.sent.clear()
            incoming=BinaryNode(
                "message",
                {"id":"incoming-1","from":"222:3@lid","type":"text","t":"1790000000"},
                [BinaryNode("enc",{"v":"2","type":"pkmsg"},ciphertext)],
            )
            await adapter._handle_unsolicited(incoming)

            self.assertEqual(len(messages),1)
            self.assertEqual(messages[0]["message_id"],"incoming-1")
            self.assertEqual(messages[0]["jid"],"222@lid")
            self.assertEqual(messages[0]["direction"],"in")
            self.assertEqual(messages[0]["kind"],"text")
            self.assertEqual(messages[0]["text"],"incoming hello")
            self.assertEqual(receipts,[])
            self.assertIsNone(adapter._load_pre_key(7))
            self.assertEqual(len(session.sent),1)
            ack=session.sent[0]
            self.assertEqual(ack.tag,"ack")
            self.assertEqual(ack.attrs["id"],"incoming-1")
            self.assertEqual(ack.attrs["class"],"message")
            self.assertEqual(ack.attrs["from"],creds.jid)
            self.assertNotEqual(ack.attrs.get("type"),"read")

    async def test_remote_receipt_is_recorded_and_transport_acked(self):
        with TemporaryDirectory() as td:
            adapter,session=self._connected_direct_adapter(td)
            receipts=[]
            adapter.on_receipt=lambda **kwargs: receipts.append(kwargs)
            session.sent.clear()
            node=BinaryNode("receipt",{"id":"sent-1","from":"222@s.whatsapp.net","type":"read"})
            await adapter._handle_unsolicited(node)
            self.assertEqual(receipts,[{
                "message_id":"sent-1",
                "jid":"222@s.whatsapp.net",
                "receipt_type":"read",
                "source":"remote",
            }])
            self.assertEqual(len(session.sent),1)
            self.assertEqual(session.sent[0].tag,"ack")
            self.assertEqual(session.sent[0].attrs["class"],"receipt")


if __name__ == "__main__":
    unittest.main()
