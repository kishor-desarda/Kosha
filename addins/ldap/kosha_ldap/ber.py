# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""The small part of BER (ITU-T X.690) that LDAP v3 needs, and LDAP search filters (RFC 4515).

Only definite lengths and single-byte tags, as RFC 4511 section 5.1 allows. Decoding never trusts a length
beyond the bytes it has, so a broken or hostile answer raises BerError instead of reading too far.
"""
from __future__ import annotations

from dataclasses import dataclass

# universal tags
BOOLEAN, INTEGER, OCTET_STRING, NULL, ENUMERATED, SEQUENCE, SET = 0x01, 0x02, 0x04, 0x05, 0x0A, 0x30, 0x31


class BerError(ValueError):
    """The bytes are not valid BER, or not what LDAP allows here."""


# ------------------------------------------------------------------ encoding

def length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + length(len(content)) + content


def integer(value: int, tag: int = INTEGER) -> bytes:
    body = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big", signed=True)
    return tlv(tag, body)


def enumerated(value: int) -> bytes:
    return integer(value, ENUMERATED)


def boolean(value: bool, tag: int = BOOLEAN) -> bytes:
    return tlv(tag, b"\xff" if value else b"\x00")


def octets(value: bytes | str, tag: int = OCTET_STRING) -> bytes:
    return tlv(tag, value.encode("utf-8") if isinstance(value, str) else value)


def sequence(*parts: bytes, tag: int = SEQUENCE) -> bytes:
    return tlv(tag, b"".join(parts))


# ------------------------------------------------------------------ decoding

@dataclass(frozen=True)
class Element:
    tag: int
    content: bytes

    @property
    def constructed(self) -> bool:
        return bool(self.tag & 0x20)

    def children(self) -> list["Element"]:
        if not self.constructed:
            raise BerError(f"element with tag 0x{self.tag:02x} has no parts")
        return decode_all(self.content)

    def int(self) -> int:
        if not self.content or len(self.content) > 8:
            raise BerError("integer of a size LDAP never uses")
        return int.from_bytes(self.content, "big", signed=True)

    def bool(self) -> bool:
        if len(self.content) != 1:
            raise BerError("bad boolean")
        return self.content != b"\x00"

    def text(self) -> str:
        return self.content.decode("utf-8", "replace")


def decode(data: bytes, pos: int = 0) -> tuple[Element, int]:
    """One element starting at `pos`; returns it and the position after it."""
    if pos + 2 > len(data):
        raise BerError("message cut short")
    tag = data[pos]
    if tag & 0x1F == 0x1F:
        raise BerError("multi-byte tags are not used in LDAP")
    first = data[pos + 1]
    pos += 2
    if first < 0x80:
        n = first
    else:
        size = first & 0x7F
        if size == 0 or size > 4:
            raise BerError("indefinite or huge length")
        if pos + size > len(data):
            raise BerError("message cut short")
        n = int.from_bytes(data[pos:pos + size], "big")
        pos += size
    if pos + n > len(data):
        raise BerError("message cut short")
    return Element(tag, data[pos:pos + n]), pos + n


def decode_all(data: bytes) -> list[Element]:
    out, pos = [], 0
    while pos < len(data):
        e, pos = decode(data, pos)
        out.append(e)
    return out


def message_size(head: bytes) -> int | None:
    """Total size of the element whose first bytes are `head`, or None when more bytes are needed to tell."""
    if len(head) < 2:
        return None
    first = head[1]
    if first < 0x80:
        return 2 + first
    size = first & 0x7F
    if size == 0 or size > 4:
        raise BerError("indefinite or huge length")
    if len(head) < 2 + size:
        return None
    return 2 + size + int.from_bytes(head[2:2 + size], "big")


# ------------------------------------------------------------------ search filters (RFC 4515)

def escape_filter_value(value: str) -> str:
    """Make text safe to put inside a filter: ``*``, ``(``, ``)``, ``\\`` and NUL become ``\\2a`` and so on."""
    return "".join(f"\\{ord(c):02x}" if c in "*()\\\x00" else c for c in value)


def _unescape(value: str) -> bytes:
    raw = value.encode("utf-8")
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == 0x5C:              # backslash: two hex digits follow
            pair = raw[i + 1:i + 3]
            try:
                out.append(int(pair, 16))
            except ValueError:
                raise BerError(f"bad escape in filter value {value!r}") from None
            if len(pair) != 2:
                raise BerError(f"bad escape in filter value {value!r}")
            i += 3
        else:
            out.append(raw[i])
            i += 1
    return bytes(out)


def _attr(desc: str) -> bytes:
    desc = desc.strip()
    if not desc or any(c in desc for c in "()=*\\ "):
        raise BerError(f"bad attribute name {desc!r} in filter")
    return desc.encode("ascii", "strict")


def _item(text: str) -> bytes:
    """One simple filter item, without its parentheses. The first '=' is the operator (names cannot hold one)."""
    eq = text.find("=")
    if eq <= 0:
        raise BerError(f"filter item {text!r} has no attribute and '='")
    before, value = text[eq - 1], text[eq + 1:]
    if before == ":":                                   # extensible match: attr[:dn][:rule]:=value
        parts = text[:eq - 1].split(":")
        attr, rest = parts[0], [p for p in parts[1:] if p]
        dn = "dn" in [p.lower() for p in rest]
        rules = [p for p in rest if p.lower() != "dn"]
        body = b""
        if rules:
            body += octets(_attr(rules[0]), 0x81)
        if attr:
            body += octets(_attr(attr), 0x82)
        body += octets(_unescape(value), 0x83)
        if dn:
            body += boolean(True, 0x84)
        return tlv(0xA9, body)
    if before in "><~":
        tag = {">": 0xA5, "<": 0xA6, "~": 0xA8}[before]
        return sequence(octets(_attr(text[:eq - 1])), octets(_unescape(value)), tag=tag)
    attr = text[:eq]
    if value == "*":
        return tlv(0x87, _attr(attr))
    if "*" in value:
        pieces = value.split("*")
        subs = b""
        if pieces[0]:
            subs += octets(_unescape(pieces[0]), 0x80)
        for mid in pieces[1:-1]:
            if mid:
                subs += octets(_unescape(mid), 0x81)
        if pieces[-1]:
            subs += octets(_unescape(pieces[-1]), 0x82)
        return sequence(octets(_attr(attr)), sequence(subs), tag=0xA4)
    return sequence(octets(_attr(attr)), octets(_unescape(value)), tag=0xA3)


def _parse(text: str, pos: int) -> tuple[bytes, int]:
    if pos >= len(text) or text[pos] != "(":
        raise BerError(f"filter must start with '(' at position {pos}: {text!r}")
    pos += 1
    if pos < len(text) and text[pos] in "&|!":
        op = text[pos]
        pos += 1
        parts = []
        while pos < len(text) and text[pos] == "(":
            enc, pos = _parse(text, pos)
            parts.append(enc)
        if pos >= len(text) or text[pos] != ")":
            raise BerError(f"missing ')' in filter {text!r}")
        if op == "!":
            if len(parts) != 1:
                raise BerError("'!' takes exactly one filter")
            return tlv(0xA2, parts[0]), pos + 1
        return tlv(0xA0 if op == "&" else 0xA1, b"".join(parts)), pos + 1
    end = text.find(")", pos)
    if end < 0:
        raise BerError(f"missing ')' in filter {text!r}")
    return _item(text[pos:end]), end + 1


def compile_filter(text: str) -> bytes:
    """The BER encoding of a filter such as ``(&(objectClass=user)(sAMAccountName=asha))``."""
    text = text.strip()
    if not text.startswith("("):
        text = f"({text})"
    enc, pos = _parse(text, 0)
    if pos != len(text):
        raise BerError(f"unexpected text after the filter: {text[pos:]!r}")
    return enc
