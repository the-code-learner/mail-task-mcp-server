from __future__ import annotations

import unittest

from postmaster.whatsapp_v990.binary import BinaryNode
from postmaster.whatsapp_v990.messages import (
    build_ack_stanza,
    build_direct_message_stanza,
    decode_text_message,
    encode_device_sent_message,
    encode_reply_text_message,
    encode_text_message,
    encrypted_participant_node,
    generate_message_id_v2,
    pad_random_max16,
    participant_hash_v2,
    unpad_random_max16,
)
from postmaster.whatsapp_v990.proto import decode_fields


class WhatsAppMessagesV990Tests(unittest.TestCase):
    def test_message_id_and_participant_hash_are_deterministic(self):
        mid=generate_message_id_v2(
            "123@s.whatsapp.net",
            now_seconds=1720000000,
            random16=bytes(range(16)),
        )
        self.assertEqual(mid,"3EB0"+mid[4:])
        self.assertEqual(len(mid),22)
        self.assertEqual(mid,generate_message_id_v2("123@s.whatsapp.net",now_seconds=1720000000,random16=bytes(range(16))))
        self.assertEqual(
            participant_hash_v2(["2@s.whatsapp.net","1@s.whatsapp.net"]),
            participant_hash_v2(["1@s.whatsapp.net","2@s.whatsapp.net"]),
        )
        self.assertTrue(participant_hash_v2(["1@s.whatsapp.net"]).startswith("2:"))

    def test_whatsapp_message_padding_roundtrip_and_bounds(self):
        raw=b"hello protobuf"
        for low_nibble in (0,1,7,15):
            padded=pad_random_max16(raw,random1=bytes((low_nibble,)))
            self.assertEqual(len(padded)-len(raw),low_nibble+1)
            self.assertEqual(unpad_random_max16(padded),raw)
        with self.assertRaises(Exception):
            unpad_random_max16(b"")
        with self.assertRaises(Exception):
            unpad_random_max16(b"x"+bytes((17,)))

    def test_text_and_device_sent_message_wire_fields(self):
        msg=encode_text_message("hello")
        fields=decode_fields(msg)
        self.assertEqual(fields[0].number,1)
        self.assertEqual(fields[0].value,b"hello")

        dsm=encode_device_sent_message("123@s.whatsapp.net",msg,phash="2:ABCDEF")
        top=decode_fields(dsm)
        self.assertEqual(top[0].number,31)
        inner=decode_fields(top[0].value)
        self.assertEqual(inner[0].number,1)
        self.assertEqual(inner[0].value,b"123@s.whatsapp.net")
        self.assertEqual(inner[1].number,2)
        self.assertEqual(inner[1].value,msg)
        self.assertEqual(inner[2].number,3)
        self.assertEqual(inner[2].value,b"2:ABCDEF")

    def test_text_decoder_handles_conversation_extended_and_device_sent(self):
        plain=encode_text_message("hello")
        self.assertEqual(decode_text_message(plain),"hello")
        extended=encode_reply_text_message(
            "reply",stanza_id="m1",participant="123@s.whatsapp.net",remote_jid="123@s.whatsapp.net"
        )
        self.assertEqual(decode_text_message(extended),"reply")
        dsm=encode_device_sent_message("123@s.whatsapp.net",plain,phash="2:ABCDEF")
        self.assertEqual(decode_text_message(dsm),"hello")

    def test_transport_ack_is_not_read_receipt(self):
        incoming=BinaryNode(
            "message",
            {"id":"m1","from":"123:2@lid","type":"text","participant":"123:2@lid"},
        )
        ack=build_ack_stanza(incoming,me_id="999:4@s.whatsapp.net")
        self.assertEqual(ack.tag,"ack")
        self.assertEqual(ack.attrs["id"],"m1")
        self.assertEqual(ack.attrs["to"],"123:2@lid")
        self.assertEqual(ack.attrs["class"],"message")
        self.assertEqual(ack.attrs["from"],"999:4@s.whatsapp.net")
        self.assertEqual(ack.attrs["type"],"text")
        self.assertNotEqual(ack.attrs.get("type"),"read")

    def test_direct_stanza_participants_and_device_identity(self):
        p1=encrypted_participant_node("123@s.whatsapp.net",ciphertext_type="pkmsg",ciphertext=b"a")
        p2=encrypted_participant_node("123:2@s.whatsapp.net",ciphertext_type="msg",ciphertext=b"b")
        stanza=build_direct_message_stanza(
            destination_jid="123@s.whatsapp.net",
            message_id="3EB0TEST",
            participants=[p1,p2],
            device_identity=b"adv",
        )
        self.assertEqual(stanza.tag,"message")
        self.assertEqual(stanza.attrs["to"],"123@s.whatsapp.net")
        self.assertEqual(stanza.attrs["type"],"text")
        self.assertTrue(stanza.attrs["phash"].startswith("2:"))
        self.assertEqual(len(stanza.child("participants").children("to")),2)
        self.assertEqual(stanza.child("device-identity").content,b"adv")


if __name__=="__main__":
    unittest.main()
