from __future__ import annotations

import asyncio
import unittest

from postmaster.whatsapp_v990.websocket_driver import NativeWebSocket


class FakeWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(bytes(data))

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def server_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    first = (0x80 if fin else 0) | opcode
    size = len(payload)
    if size < 126:
        return bytes((first, size)) + payload
    if size <= 0xFFFF:
        return bytes((first, 126)) + size.to_bytes(2, "big") + payload
    return bytes((first, 127)) + size.to_bytes(8, "big") + payload


class NativeWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_binary_frame_is_masked_and_roundtrips(self):
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        ws = NativeWebSocket(reader, writer)
        await ws.send(b"hello")
        wire = bytes(writer.data)
        self.assertEqual(wire[0], 0x82)
        self.assertTrue(wire[1] & 0x80)
        length = wire[1] & 0x7F
        self.assertEqual(length, 5)
        mask = wire[2:6]
        masked = wire[6:]
        decoded = bytes(value ^ mask[i & 3] for i, value in enumerate(masked))
        self.assertEqual(decoded, b"hello")

    async def test_ping_is_answered_and_fragmented_binary_is_reassembled(self):
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        ws = NativeWebSocket(reader, writer)
        reader.feed_data(
            server_frame(0x9, b"p")
            + server_frame(0x2, b"abc", fin=False)
            + server_frame(0x0, b"de", fin=True)
        )
        result = await ws.recv()
        self.assertEqual(result, b"abcde")
        pong = bytes(writer.data)
        self.assertEqual(pong[0], 0x8A)
        self.assertTrue(pong[1] & 0x80)

    async def test_server_masked_frame_is_rejected(self):
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        ws = NativeWebSocket(reader, writer)
        reader.feed_data(bytes((0x82, 0x80)))
        with self.assertRaisesRegex(Exception, "must not be masked"):
            await ws.recv()


if __name__ == "__main__":
    unittest.main()
