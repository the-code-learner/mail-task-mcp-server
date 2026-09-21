from __future__ import annotations

"""Unauthenticated WhatsApp Web transport smoke for CI.

This proves only DNS/TCP/TLS plus RFC6455 Upgrade reachability for the current clean-room
transport endpoint. It never sends a Noise handshake, QR material, credentials, cookies,
account identifiers, messages, or other authenticated protocol data.
"""

import base64
import hashlib
import os
import socket
import ssl
import sys

HOST = "web.whatsapp.com"
PORT = 443
PATH = "/ws/chat"
ORIGIN = "https://web.whatsapp.com"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HEADER_BYTES = 64 * 1024


def _headers(raw: bytes) -> tuple[str, dict[str, str]]:
    head = raw.decode("iso-8859-1", errors="replace")
    lines = head.split("\r\n")
    status = lines[0] if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return status, headers


def main() -> int:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {PATH} HTTP/1.1\r\n"
        f"Host: {HOST}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Origin: {ORIGIN}\r\n"
        "User-Agent: Postmaster-v9.9-CI-transport-smoke/1.0\r\n"
        "\r\n"
    ).encode("ascii")

    context = ssl.create_default_context()
    with socket.create_connection((HOST, PORT), timeout=15) as tcp:
        with context.wrap_socket(tcp, server_hostname=HOST) as tls:
            tls.settimeout(15)
            tls.sendall(request)
            response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > MAX_HEADER_BYTES:
                    raise RuntimeError("WebSocket response headers exceeded safety bound")

            header_block = bytes(response).split(b"\r\n\r\n", 1)[0]
            status, headers = _headers(header_block)
            if not status.startswith("HTTP/1.1 101"):
                print(f"WhatsApp WebSocket transport smoke failed: {status or 'no HTTP status'}", file=sys.stderr)
                return 2

            upgrade = headers.get("upgrade", "").lower()
            connection = headers.get("connection", "").lower()
            expected_accept = base64.b64encode(
                hashlib.sha1((key + GUID).encode("ascii")).digest()
            ).decode("ascii")
            actual_accept = headers.get("sec-websocket-accept", "")

            if upgrade != "websocket" or "upgrade" not in connection:
                print("WhatsApp endpoint did not return a valid WebSocket Upgrade response", file=sys.stderr)
                return 3
            if actual_accept != expected_accept:
                print("WhatsApp endpoint returned an invalid Sec-WebSocket-Accept", file=sys.stderr)
                return 4

            peer = tls.getpeercert()
            print("WhatsApp WebSocket transport reachable: HTTP 101")
            print(f"TLS version: {tls.version()}")
            print(f"Certificate subject present: {bool(peer.get('subject'))}")
            print("Authentication/protocol/account interoperability: NOT TESTED")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
