from __future__ import annotations

"""Live unauthenticated WhatsApp registration -> pair-device probe.

The probe completes the public Noise XX ClientFinish with a fresh, throw-away registration
bundle and waits for WhatsApp's encrypted WABinary pair-device stanza. It never displays or
persists QR refs, never scans a QR, never authenticates an account, and never sends messages.
"""

import asyncio
import os

from postmaster.whatsapp_v990.binary import BinaryNode, BinaryNodeCodec, BinaryNodeError
from postmaster.whatsapp_v990.cert import verify_noise_certificate_chain
from postmaster.whatsapp_v990.client_payload import RegistrationKeys, build_registration_payload
from postmaster.whatsapp_v990.crypto import (
    NOISE_WA_HEADER,
    WhatsAppNoiseXX,
    frame_noise_payload,
    generate_curve_keypair,
    split_noise_frames,
)
from postmaster.whatsapp_v990.handshake import decode_handshake, encode_client_finish, encode_client_hello
from postmaster.whatsapp_v990.signal_keys import generate_registration_id, generate_signed_pre_key
from postmaster.whatsapp_v990.tokens import CURRENT_TOKEN_TABLE
from postmaster.whatsapp_v990.websocket_driver import WebSocketDriverConfig, open_whatsapp_websocket


async def _recv_frames(ws, pending: bytes, *, timeout: float = 15.0) -> tuple[list[bytes], bytes]:
    value = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if isinstance(value, str):
        raise RuntimeError("WhatsApp returned a text WebSocket frame during binary protocol setup")
    return split_noise_frames(pending + bytes(value))


async def main() -> int:
    ephemeral = generate_curve_keypair()
    noise_static = generate_curve_keypair()
    identity = generate_curve_keypair()
    signed_pre_key = generate_signed_pre_key(identity, 1)
    registration_id = generate_registration_id()

    registration_payload = build_registration_payload(
        RegistrationKeys(
            registration_id=registration_id,
            identity_public_key=identity.public,
            signed_pre_key=signed_pre_key,
        )
    )

    noise = WhatsAppNoiseXX(ephemeral)
    codec = BinaryNodeCodec(CURRENT_TOKEN_TABLE)
    ws = await open_whatsapp_websocket(WebSocketDriverConfig(prefer_native=True))
    pending = b""
    try:
        await ws.send(frame_noise_payload(encode_client_hello(ephemeral.public), intro=NOISE_WA_HEADER))

        frames: list[bytes] = []
        while not frames:
            frames, pending = await _recv_frames(ws, pending)
        if len(frames) != 1:
            raise RuntimeError(f"expected one Noise ServerHello frame, received {len(frames)}")

        hello = decode_handshake(frames[0]).server_hello
        if hello is None:
            raise RuntimeError("WhatsApp did not return Noise ServerHello")

        encrypted_static, cert_chain = noise.process_server_hello(
            server_ephemeral=hello.ephemeral,
            encrypted_static=hello.static,
            encrypted_payload=hello.payload,
            noise_static=noise_static,
        )
        cert = verify_noise_certificate_chain(cert_chain)
        encrypted_payload = noise.encrypt(registration_payload)
        await ws.send(frame_noise_payload(encode_client_finish(encrypted_static, encrypted_payload)))
        transport = noise.finish()

        pair_device: BinaryNode | None = None
        pair_iq: BinaryNode | None = None
        decoded_count = 0
        for _ in range(24):
            encrypted_frames, pending = await _recv_frames(ws, pending, timeout=15.0)
            for encrypted in encrypted_frames:
                plaintext = transport.decrypt(encrypted)
                try:
                    node = codec.decode(plaintext)
                except BinaryNodeError as exc:
                    raise RuntimeError(f"current WABinary token dictionary could not decode live traffic: {exc}") from exc
                decoded_count += 1
                candidate = node.child("pair-device") if node.tag == "iq" else None
                if candidate is not None:
                    pair_iq, pair_device = node, candidate
                    break
            if pair_device is not None:
                break

        if pair_device is None or pair_iq is None:
            raise RuntimeError(f"pair-device stanza not observed after {decoded_count} decrypted WABinary frames")

        refs = pair_device.children("ref")
        if not refs:
            raise RuntimeError("pair-device stanza contained no QR reference nodes")
        if not pair_iq.attrs.get("id"):
            raise RuntimeError("pair-device IQ has no stanza id")

        # Acknowledge the stanza exactly as a Web companion is expected to do. This does not
        # authenticate or pair an account; it only confirms receipt of the ephemeral QR refs.
        ack = BinaryNode(
            "iq",
            {"to": "s.whatsapp.net", "type": "result", "id": pair_iq.attrs["id"]},
        )
        ack_wire = codec.encode(ack)
        await ws.send(frame_noise_payload(transport.encrypt(ack_wire)))

        print("WhatsApp live registration ClientFinish accepted")
        print(f"Noise certificate signatures verified: {cert.chain_signatures_verified}")
        print(f"Current WABinary token table version: {CURRENT_TOKEN_TABLE.version}")
        print(f"Encrypted transport frames decoded before pair-device: {decoded_count}")
        print(f"Pair-device QR references received: {len(refs)}")
        print("QR reference contents: NOT PRINTED / NOT PERSISTED")
        print("Account pairing/authentication: NOT PERFORMED")
        return 0
    finally:
        await ws.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
