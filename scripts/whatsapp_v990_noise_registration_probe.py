from __future__ import annotations

"""Live, unauthenticated WhatsApp Noise ClientFinish/registration probe.

The probe uses fresh ephemeral Noise + Signal identity material on every run. It sends a
registration-shaped ClientPayload through the current Noise XX handshake and verifies that the
first post-handshake transport frame can be authenticated/decrypted with the derived transport
keys. It deliberately stops before QR display/scan and never pairs an account.

Passing is evidence for:
- current WebSocket endpoint/origin reachability,
- Noise XX ClientHello/ServerHello/ClientFinish compatibility,
- pinned server certificate-chain verification,
- current registration ClientPayload wire acceptance far enough to enter transport mode.

Passing is NOT evidence for account pairing, Signal multi-device lifecycle, messaging, media,
groups, receipts, or controlled-account interoperability.
"""

import asyncio
import hashlib

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
from postmaster.whatsapp_v990.websocket_driver import open_whatsapp_websocket


async def _recv_frames(ws, *, timeout: float = 15.0) -> list[bytes]:
    incoming = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if isinstance(incoming, str):
        raise RuntimeError("WhatsApp server returned text during binary Noise session")
    frames, rest = split_noise_frames(bytes(incoming))
    if rest:
        raise RuntimeError(f"partial trailing Noise frame received: {len(rest)} bytes")
    if not frames:
        raise RuntimeError("WhatsApp returned no complete Noise frame")
    return frames


async def main() -> int:
    ephemeral = generate_curve_keypair()
    noise_static = generate_curve_keypair()
    identity = generate_curve_keypair()
    signed_pre_key = generate_signed_pre_key(identity, 1)
    registration = RegistrationKeys(
        registration_id=generate_registration_id(),
        identity_public_key=identity.public,
        signed_pre_key=signed_pre_key,
    )
    payload = build_registration_payload(registration)

    ws = await open_whatsapp_websocket()
    try:
        await ws.send(frame_noise_payload(encode_client_hello(ephemeral.public), intro=NOISE_WA_HEADER))
        server_frames = await _recv_frames(ws)
        if len(server_frames) != 1:
            raise RuntimeError(f"expected one ServerHello frame, received {len(server_frames)}")

        handshake = decode_handshake(server_frames[0])
        server = handshake.server_hello
        if server is None:
            raise RuntimeError("WhatsApp did not return ServerHello")

        noise = WhatsAppNoiseXX(ephemeral)
        encrypted_client_static, _cert_chain = noise.process_server_hello(
            server_ephemeral=server.ephemeral,
            encrypted_static=server.static,
            encrypted_payload=server.payload,
            noise_static=noise_static,
        )
        encrypted_payload = noise.encrypt(payload)
        finish = encode_client_finish(encrypted_client_static, encrypted_payload)
        await ws.send(frame_noise_payload(finish))

        transport = noise.finish()
        post_frames = await _recv_frames(ws)
        plaintext = [transport.decrypt(frame) for frame in post_frames]
        if not plaintext or not any(item for item in plaintext):
            raise RuntimeError("post-ClientFinish transport response decrypted to empty payloads")

        total = sum(len(item) for item in plaintext)
        digest = hashlib.sha256(b"".join(plaintext)).hexdigest()
        print("WhatsApp live Noise ClientFinish accepted into authenticated transport framing")
        print(f"Registration ClientPayload bytes: {len(payload)}")
        print(f"Post-handshake transport frames: {len(plaintext)}")
        print(f"Post-handshake plaintext bytes: {total}")
        print(f"Post-handshake plaintext SHA256: {digest}")
        print("QR/account pairing: NOT PERFORMED")
        print("Signal multi-device/message/media/group/receipt interoperability: NOT TESTED")
        return 0
    finally:
        await ws.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
