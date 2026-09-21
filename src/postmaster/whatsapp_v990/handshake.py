from __future__ import annotations

from dataclasses import dataclass

from .proto import ProtoError, decode_fields, field_bytes, field_message


class HandshakeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ClientHello:
    ephemeral: bytes
    static: bytes = b""
    payload: bytes = b""


@dataclass(frozen=True, slots=True)
class ServerHello:
    ephemeral: bytes
    static: bytes = b""
    payload: bytes = b""


@dataclass(frozen=True, slots=True)
class ClientFinish:
    static: bytes
    payload: bytes


@dataclass(frozen=True, slots=True)
class HandshakeMessage:
    client_hello: ClientHello | None = None
    server_hello: ServerHello | None = None
    client_finish: ClientFinish | None = None


def _bytes_fields(raw: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    try:
        fields = decode_fields(raw)
    except ProtoError as exc:
        raise HandshakeError(str(exc)) from exc
    for field in fields:
        if not isinstance(field.value, bytes):
            raise HandshakeError(f"Handshake field {field.number} has non-bytes wire type")
        out[field.number] = field.value
    return out


def _encode_hello(value: ClientHello | ServerHello) -> bytes:
    fields = [field_bytes(1, value.ephemeral)]
    if value.static:
        fields.append(field_bytes(2, value.static))
    if value.payload:
        fields.append(field_bytes(3, value.payload))
    return b"".join(fields)


def encode_handshake(message: HandshakeMessage) -> bytes:
    variants = [message.client_hello is not None, message.server_hello is not None, message.client_finish is not None]
    if sum(variants) != 1:
        raise HandshakeError("HandshakeMessage must contain exactly one handshake phase")
    if message.client_hello is not None:
        return field_message(2, [_encode_hello(message.client_hello)])
    if message.server_hello is not None:
        return field_message(3, [_encode_hello(message.server_hello)])
    finish = message.client_finish
    assert finish is not None
    return field_message(4, [field_bytes(1, finish.static), field_bytes(2, finish.payload)])


def decode_handshake(raw: bytes) -> HandshakeMessage:
    top = _bytes_fields(bytes(raw))
    present = [number for number in (2, 3, 4) if number in top]
    if len(present) != 1:
        raise HandshakeError("HandshakeMessage must contain exactly one recognized phase")
    number = present[0]
    nested = _bytes_fields(top[number])
    if number in (2, 3):
        ephemeral = nested.get(1, b"")
        if len(ephemeral) != 32:
            raise HandshakeError("Handshake ephemeral public key must be 32 bytes")
        cls = ClientHello if number == 2 else ServerHello
        hello = cls(ephemeral=ephemeral, static=nested.get(2, b""), payload=nested.get(3, b""))
        return HandshakeMessage(client_hello=hello) if number == 2 else HandshakeMessage(server_hello=hello)
    if 1 not in nested or 2 not in nested:
        raise HandshakeError("ClientFinish requires static and payload")
    return HandshakeMessage(client_finish=ClientFinish(static=nested[1], payload=nested[2]))


def encode_client_hello(ephemeral_public: bytes) -> bytes:
    if len(ephemeral_public) != 32:
        raise HandshakeError("ClientHello ephemeral public key must be 32 bytes")
    return encode_handshake(HandshakeMessage(client_hello=ClientHello(ephemeral=bytes(ephemeral_public))))


def encode_client_finish(encrypted_static: bytes, encrypted_payload: bytes) -> bytes:
    return encode_handshake(
        HandshakeMessage(client_finish=ClientFinish(static=bytes(encrypted_static), payload=bytes(encrypted_payload)))
    )


__all__ = [
    "HandshakeError", "ClientHello", "ServerHello", "ClientFinish", "HandshakeMessage",
    "encode_handshake", "decode_handshake", "encode_client_hello", "encode_client_finish",
]
