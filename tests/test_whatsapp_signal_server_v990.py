from __future__ import annotations

import unittest

from postmaster.whatsapp_v990.binary import BinaryNode
from postmaster.whatsapp_v990.crypto import generate_curve_keypair
from postmaster.whatsapp_v990.signal_keys import generate_signed_pre_key
from postmaster.whatsapp_v990.signal_server import (
    build_prekey_count_query,
    build_prekey_upload,
    build_session_query,
    parse_prekey_count,
    parse_session_bundles,
)


class SignalServerV990Tests(unittest.TestCase):
    def test_prekey_count_query_and_response(self):
        query=build_prekey_count_query(stanza_id="x1")
        self.assertEqual(query.attrs["xmlns"],"encrypt")
        self.assertEqual(query.child("count").tag,"count")
        response=BinaryNode("iq",{"id":"x1","type":"result"},[BinaryNode("count",{"value":"17"})])
        self.assertEqual(parse_prekey_count(response),17)

    def test_upload_node_contains_registration_identity_and_ordered_prekeys(self):
        identity=generate_curve_keypair()
        signed=generate_signed_pre_key(identity,9,random64=b"s"*64)
        keys={5:generate_curve_keypair(),3:generate_curve_keypair()}
        node=build_prekey_upload(
            registration_id=1234,
            identity_public=identity.public,
            signed_pre_key=signed,
            pre_keys=keys,
            stanza_id="up1",
        )
        self.assertEqual(node.attrs["type"],"set")
        self.assertEqual(node.child("registration").content,(1234).to_bytes(2,"big"))
        self.assertEqual(node.child("identity").content,identity.public)
        ids=[int.from_bytes(item.child("id").content,"big") for item in node.child("list").children("key")]
        self.assertEqual(ids,[3,5])
        self.assertEqual(int.from_bytes(node.child("skey").child("id").content,"big"),9)

    def test_session_query_and_bundle_parse(self):
        remote_identity=generate_curve_keypair()
        signed=generate_signed_pre_key(remote_identity,17,random64=b"r"*64)
        one_time=generate_curve_keypair()
        query=build_session_query(["123:1@s.whatsapp.net","123:1@s.whatsapp.net"],stanza_id="q1")
        self.assertEqual(len(query.child("key").children("user")),1)

        user=BinaryNode("user",{"jid":"123:1@s.whatsapp.net"},[
            BinaryNode("registration",{},(4321).to_bytes(2,"big")),
            BinaryNode("type",{},b"\x05"),
            BinaryNode("identity",{},remote_identity.public),
            BinaryNode("skey",{},[
                BinaryNode("id",{},(17).to_bytes(3,"big")),
                BinaryNode("value",{},signed.key_pair.public),
                BinaryNode("signature",{},signed.signature),
            ]),
            BinaryNode("key",{},[
                BinaryNode("id",{},(44).to_bytes(3,"big")),
                BinaryNode("value",{},one_time.public),
            ]),
        ])
        response=BinaryNode("iq",{"id":"q1","type":"result"},[BinaryNode("list",{},[user])])
        bundles=parse_session_bundles(response)
        bundle=bundles["123:1@s.whatsapp.net"]
        self.assertEqual(bundle.registration_id,4321)
        self.assertEqual(bundle.signed_pre_key_id,17)
        self.assertEqual(bundle.pre_key_id,44)
        self.assertEqual(bundle.pre_key,one_time.public)


if __name__=="__main__":
    unittest.main()
