from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class ProtoError(ValueError):
    pass


WIRE_VARINT = 0
WIRE_64BIT = 1
WIRE_LENGTH = 2
WIRE_32BIT = 5


def encode_varint(value: int) -> bytes:
    value = int(value)
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80); value >>= 7
    out.append(value)
    return bytes(out)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    result = 0; shift = 0; pos = int(offset)
    while True:
        if pos >= len(data): raise ProtoError("Truncated protobuf varint")
        byte = data[pos]; pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80): return result, pos
        shift += 7
        if shift >= 70: raise ProtoError("Oversized protobuf varint")


@dataclass(frozen=True, slots=True)
class ProtoField:
    number: int
    wire_type: int
    value: int | bytes


def field_varint(number: int, value: int) -> bytes:
    return encode_varint((int(number) << 3) | WIRE_VARINT) + encode_varint(value)


def field_bytes(number: int, value: bytes | bytearray | memoryview | str) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return encode_varint((int(number) << 3) | WIRE_LENGTH) + encode_varint(len(raw)) + raw


def field_message(number: int, fields: Iterable[bytes]) -> bytes:
    return field_bytes(number, b"".join(fields))


def decode_fields(data: bytes) -> list[ProtoField]:
    raw = bytes(data); pos = 0; fields: list[ProtoField] = []
    while pos < len(raw):
        key, pos = decode_varint(raw, pos)
        number, wire = key >> 3, key & 7
        if number <= 0: raise ProtoError("Invalid protobuf field number")
        if wire == WIRE_VARINT:
            value, pos = decode_varint(raw, pos)
        elif wire == WIRE_LENGTH:
            length, pos = decode_varint(raw, pos)
            if pos + length > len(raw): raise ProtoError("Truncated protobuf length-delimited field")
            value = raw[pos:pos+length]; pos += length
        elif wire == WIRE_64BIT:
            if pos + 8 > len(raw): raise ProtoError("Truncated protobuf fixed64 field")
            value = raw[pos:pos+8]; pos += 8
        elif wire == WIRE_32BIT:
            if pos + 4 > len(raw): raise ProtoError("Truncated protobuf fixed32 field")
            value = raw[pos:pos+4]; pos += 4
        else:
            raise ProtoError(f"Unsupported protobuf wire type {wire}")
        fields.append(ProtoField(number, wire, value))
    return fields
