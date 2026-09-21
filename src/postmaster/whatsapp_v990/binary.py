from __future__ import annotations

from dataclasses import dataclass, field
import zlib
from typing import Iterable, Mapping, Sequence

from .jid import JID, parse_jid


class BinaryNodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BinaryTags:
    LIST_EMPTY: int = 0
    DICTIONARY_0: int = 236
    DICTIONARY_1: int = 237
    DICTIONARY_2: int = 238
    DICTIONARY_3: int = 239
    INTEROP_JID: int = 245
    FB_JID: int = 246
    AD_JID: int = 247
    LIST_8: int = 248
    LIST_16: int = 249
    JID_PAIR: int = 250
    HEX_8: int = 251
    BINARY_8: int = 252
    BINARY_20: int = 253
    BINARY_32: int = 254
    NIBBLE_8: int = 255
    PACKED_MAX: int = 127


TAGS = BinaryTags()


@dataclass(slots=True)
class BinaryNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    content: bytes | str | list["BinaryNode"] | None = None

    def child(self, tag: str) -> "BinaryNode | None":
        if not isinstance(self.content, list):
            return None
        return next((item for item in self.content if item.tag == tag), None)

    def children(self, tag: str | None = None) -> list["BinaryNode"]:
        if not isinstance(self.content, list):
            return []
        return [item for item in self.content if tag is None or item.tag == tag]


class TokenTable:
    """Versioned WABinary token lookup.

    Production can inject token tables generated from a pinned WhatsApp Web protocol snapshot.
    Encoding always has a raw-string fallback, while decoding rejects unknown token references
    instead of silently corrupting nodes.
    """

    def __init__(self, single: Sequence[str | None] = (), double: Sequence[Sequence[str | None]] = (), *, version: int | None = None):
        self.single = tuple(single)
        self.double = tuple(tuple(row) for row in double)
        self.version = version
        self._map: dict[str, tuple[int | None, int]] = {}
        for idx, value in enumerate(self.single):
            if value:
                self._map[str(value)] = (None, idx)
        for d, row in enumerate(self.double):
            for idx, value in enumerate(row):
                if value:
                    self._map[str(value)] = (d, idx)

    def token_for(self, value: str) -> tuple[int | None, int] | None:
        return self._map.get(value)

    def decode_single(self, idx: int) -> str:
        if not (0 <= idx < len(self.single)) or self.single[idx] is None:
            raise BinaryNodeError(f"Unknown WABinary single-byte token {idx}")
        return str(self.single[idx])

    def decode_double(self, dictionary: int, idx: int) -> str:
        if not (0 <= dictionary < len(self.double)) or not (0 <= idx < len(self.double[dictionary])) or self.double[dictionary][idx] is None:
            raise BinaryNodeError(f"Unknown WABinary double-byte token {dictionary}:{idx}")
        return str(self.double[dictionary][idx])


EMPTY_TOKENS = TokenTable()


def _is_nibble(value: str) -> bool:
    return bool(value) and len(value) <= TAGS.PACKED_MAX and all(ch.isdigit() or ch in "-." for ch in value)


def _is_hex(value: str) -> bool:
    return bool(value) and len(value) <= TAGS.PACKED_MAX and all(ch in "0123456789ABCDEF" for ch in value)


def _pack(value: str, *, hex_mode: bool) -> bytes:
    def cv(ch: str) -> int:
        if ch == "\0": return 15
        if not hex_mode:
            if ch == "-": return 10
            if ch == ".": return 11
            if ch.isdigit(): return ord(ch) - 48
        else:
            if ch.isdigit(): return ord(ch) - 48
            if "A" <= ch <= "F": return 10 + ord(ch) - ord("A")
        raise BinaryNodeError(f"Cannot pack character {ch!r}")
    out = bytearray([TAGS.HEX_8 if hex_mode else TAGS.NIBBLE_8])
    size = (len(value) + 1) // 2
    out.append(size | (0x80 if len(value) % 2 else 0))
    for i in range(0, len(value), 2):
        a = cv(value[i])
        b = cv(value[i + 1] if i + 1 < len(value) else "\0")
        out.append((a << 4) | b)
    return bytes(out)


def _unpack(tag: int, raw: bytes, pos: int) -> tuple[str, int]:
    if pos >= len(raw): raise BinaryNodeError("Truncated packed string")
    size_byte = raw[pos]; pos += 1
    nbytes = size_byte & 0x7F
    odd = bool(size_byte & 0x80)
    if pos + nbytes > len(raw): raise BinaryNodeError("Truncated packed string")
    chars: list[str] = []
    for byte in raw[pos:pos+nbytes]:
        for v in ((byte >> 4) & 15, byte & 15):
            if tag == TAGS.HEX_8:
                if v < 10: chars.append(chr(48 + v))
                elif v < 16: chars.append(chr(ord("A") + v - 10) if v < 15 else "\0")
                else: raise BinaryNodeError("Invalid hex nibble")
            else:
                if v <= 9: chars.append(chr(48 + v))
                elif v == 10: chars.append("-")
                elif v == 11: chars.append(".")
                elif v == 15: chars.append("\0")
                else: raise BinaryNodeError("Invalid numeric nibble")
    if odd:
        chars = chars[:-1]
    return "".join(chars).replace("\0", ""), pos + nbytes


class BinaryNodeCodec:
    def __init__(self, tokens: TokenTable = EMPTY_TOKENS, *, tags: BinaryTags = TAGS):
        self.tokens = tokens
        self.tags = tags

    def encode(self, node: BinaryNode, *, compressed: bool = False) -> bytes:
        body = bytearray()
        self._write_node(body, node)
        if compressed:
            return bytes([2]) + zlib.compress(bytes(body))
        return bytes([0]) + bytes(body)

    def decode(self, payload: bytes) -> BinaryNode:
        raw = bytes(payload)
        if not raw:
            raise BinaryNodeError("Empty WABinary payload")
        flag, body = raw[0], raw[1:]
        if flag & 2:
            try:
                body = zlib.decompress(body)
            except zlib.error as exc:
                raise BinaryNodeError("Invalid compressed WABinary payload") from exc
        node, pos = self._read_node(body, 0)
        if pos != len(body):
            raise BinaryNodeError(f"Trailing WABinary bytes: {len(body)-pos}")
        return node

    def _list_start(self, out: bytearray, size: int) -> None:
        if size == 0: out.append(self.tags.LIST_EMPTY)
        elif size < 256: out.extend((self.tags.LIST_8, size))
        elif size < 65536: out.append(self.tags.LIST_16); out.extend(size.to_bytes(2, "big"))
        else: raise BinaryNodeError("WABinary list too large")

    def _write_len(self, out: bytearray, length: int) -> None:
        if length < 256: out.extend((self.tags.BINARY_8, length))
        elif length < (1 << 20): out.append(self.tags.BINARY_20); out.extend(((length >> 16) & 15, (length >> 8) & 255, length & 255))
        elif length < (1 << 32): out.append(self.tags.BINARY_32); out.extend(length.to_bytes(4, "big"))
        else: raise BinaryNodeError("WABinary value too large")

    def _write_raw(self, out: bytearray, raw: bytes) -> None:
        self._write_len(out, len(raw)); out.extend(raw)

    def _write_jid(self, out: bytearray, jid: JID) -> None:
        if jid.device is not None:
            out.extend((self.tags.AD_JID, jid.domain_type & 255, jid.device & 255))
            self._write_string(out, jid.user)
        else:
            out.append(self.tags.JID_PAIR)
            if jid.user: self._write_string(out, jid.user)
            else: out.append(self.tags.LIST_EMPTY)
            self._write_string(out, jid.server)

    def _write_string(self, out: bytearray, value: str | None) -> None:
        if value is None:
            out.append(self.tags.LIST_EMPTY); return
        token = self.tokens.token_for(value)
        if token is not None:
            dictionary, idx = token
            if dictionary is not None:
                if not 0 <= dictionary <= 3: raise BinaryNodeError("Token dictionary index out of range")
                out.append(self.tags.DICTIONARY_0 + dictionary)
            out.append(idx & 255); return
        if _is_nibble(value): out.extend(_pack(value, hex_mode=False)); return
        if _is_hex(value): out.extend(_pack(value, hex_mode=True)); return
        try:
            jid = parse_jid(value)
        except ValueError:
            jid = None
        if jid is not None:
            self._write_jid(out, jid); return
        self._write_raw(out, value.encode("utf-8"))

    def _write_node(self, out: bytearray, node: BinaryNode) -> None:
        if not node.tag: raise BinaryNodeError("Node tag cannot be empty")
        attrs = [(str(k), str(v)) for k, v in (node.attrs or {}).items() if v is not None]
        has_content = node.content is not None
        self._list_start(out, 1 + 2 * len(attrs) + (1 if has_content else 0))
        self._write_string(out, node.tag)
        for key, value in attrs:
            self._write_string(out, key); self._write_string(out, value)
        content = node.content
        if content is None: return
        if isinstance(content, str): self._write_string(out, content)
        elif isinstance(content, (bytes, bytearray, memoryview)): self._write_raw(out, bytes(content))
        elif isinstance(content, list):
            self._list_start(out, len(content))
            for child in content:
                if not isinstance(child, BinaryNode): raise BinaryNodeError("Node child must be BinaryNode")
                self._write_node(out, child)
        else: raise BinaryNodeError(f"Unsupported node content: {type(content).__name__}")

    def _read_n(self, raw: bytes, pos: int, n: int) -> tuple[bytes, int]:
        if pos + n > len(raw): raise BinaryNodeError("WABinary end of stream")
        return raw[pos:pos+n], pos+n

    def _read_list_size(self, raw: bytes, pos: int, tag: int | None = None) -> tuple[int, int]:
        if tag is None:
            b, pos = self._read_n(raw, pos, 1); tag = b[0]
        if tag == self.tags.LIST_EMPTY: return 0, pos
        if tag == self.tags.LIST_8:
            b, pos = self._read_n(raw, pos, 1); return b[0], pos
        if tag == self.tags.LIST_16:
            b, pos = self._read_n(raw, pos, 2); return int.from_bytes(b, "big"), pos
        raise BinaryNodeError(f"Invalid WABinary list tag {tag}")

    def _read_len(self, raw: bytes, pos: int, tag: int) -> tuple[int, int]:
        if tag == self.tags.BINARY_8:
            b, pos = self._read_n(raw, pos, 1); return b[0], pos
        if tag == self.tags.BINARY_20:
            b, pos = self._read_n(raw, pos, 3); return ((b[0] & 15) << 16) | (b[1] << 8) | b[2], pos
        if tag == self.tags.BINARY_32:
            b, pos = self._read_n(raw, pos, 4); return int.from_bytes(b, "big"), pos
        raise BinaryNodeError(f"Not a binary-length tag: {tag}")

    def _read_string(self, raw: bytes, pos: int, first: int | None = None) -> tuple[str, int]:
        if first is None:
            b, pos = self._read_n(raw, pos, 1); first = b[0]
        if 1 <= first < len(self.tokens.single): return self.tokens.decode_single(first), pos
        if self.tags.DICTIONARY_0 <= first <= self.tags.DICTIONARY_3:
            b, pos = self._read_n(raw, pos, 1); return self.tokens.decode_double(first - self.tags.DICTIONARY_0, b[0]), pos
        if first == self.tags.LIST_EMPTY: return "", pos
        if first in (self.tags.BINARY_8, self.tags.BINARY_20, self.tags.BINARY_32):
            length, pos = self._read_len(raw, pos, first); b, pos = self._read_n(raw, pos, length)
            try: return b.decode("utf-8"), pos
            except UnicodeDecodeError as exc: raise BinaryNodeError("Binary string is not UTF-8") from exc
        if first in (self.tags.NIBBLE_8, self.tags.HEX_8): return _unpack(first, raw, pos)
        if first == self.tags.JID_PAIR:
            user, pos = self._read_string(raw, pos); server, pos = self._read_string(raw, pos)
            if not server: raise BinaryNodeError("JID pair has empty server")
            return f"{user}@{server}", pos
        if first == self.tags.AD_JID:
            b, pos = self._read_n(raw, pos, 2); domain_type, device = b[0], b[1]
            user, pos = self._read_string(raw, pos)
            server = {1:"lid",128:"hosted",129:"hosted.lid"}.get(domain_type, "s.whatsapp.net")
            return str(JID(user, server, device=device, domain_type=domain_type)), pos
        if first == self.tags.FB_JID:
            user, pos = self._read_string(raw, pos)
            b, pos = self._read_n(raw, pos, 2); device = int.from_bytes(b, "big")
            server, pos = self._read_string(raw, pos)
            if not server: raise BinaryNodeError("FB JID has empty server")
            return f"{user}:{device}@{server}", pos
        if first == self.tags.INTEROP_JID:
            user, pos = self._read_string(raw, pos)
            b, pos = self._read_n(raw, pos, 2); device = int.from_bytes(b, "big")
            b, pos = self._read_n(raw, pos, 2); integrator = int.from_bytes(b, "big")
            # Current wire format may omit the optional server; in that case preserve
            # the protocol-default interop domain instead of consuming the next field.
            before = pos
            try:
                server, pos = self._read_string(raw, pos)
            except BinaryNodeError:
                server, pos = "interop", before
            if not server: server = "interop"
            return f"{integrator}-{user}:{device}@{server}", pos
        raise BinaryNodeError(f"Unknown WABinary string token/tag {first}")

    def _read_content(self, raw: bytes, pos: int, first: int) -> tuple[bytes | str | list[BinaryNode], int]:
        if first in (self.tags.LIST_EMPTY, self.tags.LIST_8, self.tags.LIST_16):
            size, pos = self._read_list_size(raw, pos, first)
            items: list[BinaryNode] = []
            for _ in range(size):
                node, pos = self._read_node(raw, pos); items.append(node)
            return items, pos
        if first in (self.tags.BINARY_8, self.tags.BINARY_20, self.tags.BINARY_32):
            length, pos = self._read_len(raw, pos, first)
            return self._read_n(raw, pos, length)
        return self._read_string(raw, pos, first)

    def _read_node(self, raw: bytes, pos: int) -> tuple[BinaryNode, int]:
        size, pos = self._read_list_size(raw, pos)
        if size <= 0: raise BinaryNodeError("Invalid empty WABinary node")
        tag, pos = self._read_string(raw, pos)
        if not tag: raise BinaryNodeError("WABinary node tag is empty")
        attrs: dict[str, str] = {}
        for _ in range((size - 1) // 2):
            key, pos = self._read_string(raw, pos); value, pos = self._read_string(raw, pos); attrs[key] = value
        content = None
        if size % 2 == 0:
            b, pos = self._read_n(raw, pos, 1); content, pos = self._read_content(raw, pos, b[0])
        return BinaryNode(tag=tag, attrs=attrs, content=content), pos
