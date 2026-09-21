from __future__ import annotations

"""Live, unauthenticated WhatsApp Noise server-hello probe.

This stops before ClientFinish/ClientPayload. It uses only ephemeral/random client keys and
validates the server Noise certificate chain. No QR, account, cookie, message or credential
is sent. Passing this probe is evidence for the initial Noise XX server-hello path only.
"""

import asyncio
import time

from postmaster.whatsapp_v990.cert import verify_noise_certificate_chain
from postmaster.whatsapp_v990.crypto import (
    NOISE_WA_HEADER,
    WhatsAppNoiseXX,
    generate_curve_keypair,
    split_noise_frames,
    frame_noise_payload,
)
from postmaster.whatsapp_v990.handshake import decode_handshake, encode_client_hello
from postmaster.whatsapp_v990.proto import ProtoField, decode_fields
from postmaster.whatsapp_v990.websocket_driver import open_whatsapp_websocket


async def main() -> int:
    ephemeral = generate_curve_keypair()
    noise_static = generate_curve_keypair()
    noise = WhatsAppNoiseXX(ephemeral)

    ws = await open_whatsapp_websocket()
    try:
        client_hello = encode_client_hello(ephemeral.public)
        await ws.send(frame_noise_payload(client_hello, intro=NOISE_WA_HEADER))

        incoming = await asyncio.wait_for(ws.recv(), timeout=15)
        if isinstance(incoming, str):
            raise RuntimeError("WhatsApp server returned text during Noise handshake")
        frames, rest = split_noise_frames(bytes(incoming))
        if rest:
            raise RuntimeError(f"partial trailing Noise frame received: {len(rest)} bytes")
        if len(frames) != 1:
            raise RuntimeError(f"expected one server Noise frame, received {len(frames)}")

        handshake = decode_handshake(frames[0])
        server = handshake.server_hello
        if server is None:
            raise RuntimeError("WhatsApp did not return ServerHello")

        _encrypted_client_static, cert_chain = noise.process_server_hello(
            server_ephemeral=server.ephemeral,
            encrypted_static=server.static,
            encrypted_payload=server.payload,
            noise_static=noise_static,
        )
        cert = verify_server_cert_chain(cert_chain)

        print("WhatsApp live Noise ServerHello decrypted successfully")
        print(f"Server ephemeral bytes: {len(server.ephemeral)}")
        print(f"Server certificate chain bytes: {len(cert_chain)}")
        print(f"Certificate signatures verified: {cert.chain_signatures_verified}")
        print(f"Issuer serial: {cert.intermediate.issuer_serial}")
        print("ClientFinish/account authentication: NOT SENT / NOT TESTED")
        return 0
    finally:
        await ws.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
