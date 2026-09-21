from __future__ import annotations

import unittest

from postmaster.whatsapp_v990.binary import BinaryNode
from postmaster.whatsapp_v990.usync import build_device_query, parse_device_result


class USyncV990Tests(unittest.TestCase):
    def test_query_shape_and_lid_device_parse(self):
        query=build_device_query(["123@s.whatsapp.net","123@s.whatsapp.net"],stanza_id="u1",sid="sid1")
        self.assertEqual(query.attrs["xmlns"],"usync")
        usync=query.child("usync")
        self.assertEqual(usync.attrs["context"],"message")
        self.assertEqual([x.tag for x in usync.child("query").children()],["devices","lid"])
        self.assertEqual(len(usync.child("list").children("user")),1)

        response=BinaryNode("iq",{"id":"u1","type":"result"},[
            BinaryNode("usync",{},[
                BinaryNode("list",{},[
                    BinaryNode("user",{"jid":"123@s.whatsapp.net"},[
                        BinaryNode("lid",{"val":"999@lid"}),
                        BinaryNode("devices",{},[
                            BinaryNode("device-list",{},[
                                BinaryNode("device",{"id":"0"}),
                                BinaryNode("device",{"id":"2","key-index":"11"}),
                                BinaryNode("device",{"id":"3","key-index":"12","is_hosted":"true"}),
                                BinaryNode("device",{"id":"4"}), # invalid non-zero without key-index
                            ])
                        ])
                    ])
                ])
            ])
        ])
        targets=parse_device_result(response,prefer_lid=True)
        self.assertEqual([x.jid for x in targets],["999@lid","999:2@lid","999:3@hosted.lid"])
        self.assertEqual(targets[1].source_jid,"123@s.whatsapp.net")
        self.assertEqual(targets[1].lid,"999@lid")

    def test_own_device_and_zero_filter(self):
        response=BinaryNode("iq",{"type":"result"},[
            BinaryNode("usync",{},[
                BinaryNode("list",{},[
                    BinaryNode("user",{"jid":"123@s.whatsapp.net"},[
                        BinaryNode("devices",{},[
                            BinaryNode("device-list",{},[
                                BinaryNode("device",{"id":"0"}),
                                BinaryNode("device",{"id":"2","key-index":"1"}),
                            ])
                        ])
                    ])
                ])
            ])
        ])
        self.assertEqual(
            [x.jid for x in parse_device_result(response,own_jid="123:2@s.whatsapp.net")],
            ["123@s.whatsapp.net"],
        )
        self.assertEqual(
            [x.jid for x in parse_device_result(response,ignore_zero_devices=True)],
            ["123:2@s.whatsapp.net"],
        )


if __name__=="__main__":
    unittest.main()
