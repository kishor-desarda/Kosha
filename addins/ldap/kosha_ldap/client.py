# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""A minimal LDAP v3 client (RFC 4511): simple bind, search, StartTLS, unbind. Standard library only.

Kosha needs four operations to check a password and read a person's groups, so it speaks them itself instead
of depending on a library (the usual pure-Python one is LGPL, which the project's licence rules do not allow
inside an add-in; the C binding needs a compiler and OpenLDAP's libraries on every server).
"""
from __future__ import annotations

import socket
import ssl
import urllib.parse
from dataclasses import dataclass, field

from . import ber

# result codes (RFC 4511, appendix A)
SUCCESS, SIZE_LIMIT_EXCEEDED, INVALID_CREDENTIALS = 0, 4, 49
STARTTLS_OID = "1.3.6.1.4.1.1466.20037"
MAX_MESSAGE = 16 * 1024 * 1024        # no answer Kosha asks for is anywhere near this
SCOPE_BASE, SCOPE_ONE, SCOPE_SUB = 0, 1, 2

RESULT_TEXT = {
    1: "operations error", 2: "protocol error", 3: "time limit exceeded", 4: "size limit exceeded",
    7: "authentication method not supported", 8: "stronger authentication (TLS) required", 10: "referral",
    11: "administrative limit exceeded", 13: "confidentiality (TLS) required", 32: "no such object (check the base)",
    34: "invalid DN syntax", 48: "inappropriate authentication", 49: "invalid credentials",
    50: "insufficient access rights", 51: "busy", 52: "unavailable", 53: "unwilling to perform",
}


class LdapError(Exception):
    """The directory could not be reached, or answered in a way Kosha did not expect."""


@dataclass
class Result:
    code: int
    matched_dn: str = ""
    message: str = ""

    def describe(self) -> str:
        words = RESULT_TEXT.get(self.code, f"result code {self.code}")
        return f"{words}" + (f" ({self.message.strip()[:200]})" if self.message.strip() else "")


@dataclass
class Entry:
    dn: str
    attrs: dict[str, list[bytes]] = field(default_factory=dict)       # attribute names in lower case

    def first(self, name: str) -> bytes | None:
        values = self.attrs.get(name.lower())
        return values[0] if values else None

    def text(self, name: str) -> str:
        v = self.first(name)
        return v.decode("utf-8", "replace") if v is not None else ""

    def texts(self, name: str) -> list[str]:
        return [v.decode("utf-8", "replace") for v in self.attrs.get(name.lower(), [])]


def parse_url(url: str) -> tuple[str, str, int]:
    """(scheme, host, port) of ldap://host[:port] or ldaps://host[:port]."""
    p = urllib.parse.urlsplit((url or "").strip())
    if p.scheme not in ("ldap", "ldaps") or not p.hostname:
        raise LdapError("The directory address must look like ldaps://dc1.example.com or ldap://ldap.example.com.")
    return p.scheme, p.hostname, p.port or (636 if p.scheme == "ldaps" else 389)


class Connection:
    def __init__(self, url: str, *, timeout: float = 8.0, starttls: bool = True, verify_tls: bool = True,
                 ca_file: str = ""):
        self.scheme, self.host, self.port = parse_url(url)
        self.timeout = timeout
        self.starttls = starttls and self.scheme == "ldap"
        self.verify_tls = verify_tls
        self.ca_file = ca_file
        self.sock: socket.socket | None = None
        self.encrypted = False
        self._next_id = 0
        self._buf = b""

    @property
    def where(self) -> str:
        return f"{self.host}:{self.port}"

    # ------------------------------------------------------------------ socket
    def _tls(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=self.ca_file or None)
        if not self.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE         # only when the admin switched verification off
        return ctx

    def open(self) -> "Connection":
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
            if self.scheme == "ldaps":
                self.sock = self._tls().wrap_socket(self.sock, server_hostname=self.host)
                self.encrypted = True
            elif self.starttls:
                r = self._extended(STARTTLS_OID)
                if r.code != SUCCESS:
                    raise LdapError(f"The directory at {self.where} refused StartTLS: {r.describe()}. Use an "
                                    "ldaps:// address, or switch StartTLS off only for a test directory.")
                self.sock = self._tls().wrap_socket(self.sock, server_hostname=self.host)
                self.encrypted = True
        except ssl.SSLCertVerificationError as e:
            self.close(quiet=True)
            raise LdapError(f"The directory's certificate at {self.where} is not trusted ({e.verify_message}). Give "
                            "the file with your CA certificate in the settings.") from None
        except (OSError, ssl.SSLError) as e:
            self.close(quiet=True)
            raise LdapError(f"Cannot reach the directory at {self.where}: {e.__class__.__name__}: {e}") from None
        return self

    def close(self, quiet: bool = False) -> None:
        if self.sock is None:
            return
        try:
            if not quiet:
                self._send(ber.tlv(0x42, b""))                     # UnbindRequest
        except Exception:
            pass
        try:
            self.sock.close()
        finally:
            self.sock = None

    def __enter__(self) -> "Connection":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def _send(self, op: bytes) -> int:
        self._next_id += 1
        msg = ber.sequence(ber.integer(self._next_id), op)
        try:
            self.sock.sendall(msg)
        except OSError as e:
            raise LdapError(f"The connection to the directory at {self.where} broke: {e}") from None
        return self._next_id

    def _read_message(self) -> tuple[int, ber.Element]:
        while True:
            try:
                size = ber.message_size(self._buf)
            except ber.BerError as e:
                raise LdapError(f"The directory at {self.where} sent something that is not LDAP ({e}).") from None
            if size is not None and size > MAX_MESSAGE:
                raise LdapError(f"The directory at {self.where} sent an answer that is far too large.")
            if size is not None and len(self._buf) >= size:
                raw, self._buf = self._buf[:size], self._buf[size:]
                break
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise LdapError(f"The directory at {self.where} did not answer within {self.timeout:.0f} seconds.") from None
            except OSError as e:
                raise LdapError(f"The connection to the directory at {self.where} broke: {e}") from None
            if not chunk:
                raise LdapError(f"The directory at {self.where} closed the connection.")
            self._buf += chunk
        try:
            msg, _ = ber.decode(raw)
            parts = msg.children()
            if msg.tag != ber.SEQUENCE or len(parts) < 2:
                raise ber.BerError("not an LDAP message")
            return parts[0].int(), parts[1]
        except ber.BerError as e:
            raise LdapError(f"The directory at {self.where} sent something that is not LDAP ({e}).") from None

    def _answer(self, msg_id: int) -> ber.Element:
        while True:
            got, op = self._read_message()
            if got == msg_id:
                return op
            if got == 0:            # notice of disconnection
                raise LdapError(f"The directory at {self.where} ended the connection: "
                                f"{self._result(op).describe()}")

    @staticmethod
    def _result(op: ber.Element) -> Result:
        try:
            parts = op.children()
            return Result(parts[0].int(), parts[1].text(), parts[2].text())
        except (ber.BerError, IndexError):
            raise LdapError("The directory sent a result Kosha cannot read.") from None

    # ------------------------------------------------------------------ operations
    def _extended(self, oid: str) -> Result:
        mid = self._send(ber.tlv(0x77, ber.octets(oid, 0x80)))
        op = self._answer(mid)
        if op.tag != 0x78:
            raise LdapError("The directory answered StartTLS with something else.")
        return self._result(op)

    def bind(self, name: str, password: str) -> Result:
        """Simple bind. Callers must refuse an empty password first: the directory would take it as anonymous."""
        mid = self._send(ber.sequence(ber.integer(3), ber.octets(name), ber.octets(password, 0x80), tag=0x60))
        op = self._answer(mid)
        if op.tag != 0x61:
            raise LdapError("The directory answered a sign-in with something else.")
        return self._result(op)

    def search(self, base: str, filter_text: str, attributes: list[str], scope: int = SCOPE_SUB,
               size_limit: int = 0) -> tuple[list[Entry], Result]:
        """Entries matching the filter (referrals are skipped), and the final result."""
        try:
            flt = ber.compile_filter(filter_text)
        except ber.BerError as e:
            raise LdapError(f"The search filter is not valid: {e}") from None
        req = ber.sequence(ber.octets(base), ber.enumerated(scope), ber.enumerated(0), ber.integer(size_limit),
                           ber.integer(int(self.timeout)), ber.boolean(False), flt,
                           ber.sequence(*(ber.octets(a) for a in attributes)), tag=0x63)
        mid = self._send(req)
        entries: list[Entry] = []
        while True:
            got, op = self._read_message()
            if got == 0:
                raise LdapError(f"The directory at {self.where} ended the connection: {self._result(op).describe()}")
            if got != mid:
                continue
            if op.tag == 0x64:                           # SearchResultEntry
                try:
                    dn_el, attrs_el = op.children()[:2]
                    attrs: dict[str, list[bytes]] = {}
                    for a in attrs_el.children():
                        name_el, vals_el = a.children()[:2]
                        attrs.setdefault(name_el.text().lower(), []).extend(v.content for v in vals_el.children())
                    entries.append(Entry(dn_el.text(), attrs))
                except (ber.BerError, ValueError):
                    raise LdapError("The directory sent a search result Kosha cannot read.") from None
            elif op.tag == 0x73:                         # SearchResultReference: another server; not followed
                continue
            elif op.tag == 0x65:                         # SearchResultDone
                return entries, self._result(op)
            else:
                raise LdapError("The directory answered a search with something else.")


def escape_dn_value(value: str) -> str:
    """Make text safe as one value in a DN (RFC 4514), e.g. for uid={username},ou=people,dc=example,dc=com."""
    out = []
    for i, c in enumerate(value):
        if c in ',+"\\<>;=' or (c == "#" and i == 0) or (c == " " and i in (0, len(value) - 1)):
            out.append("\\" + c)
        elif c == "\x00":
            out.append("\\00")
        else:
            out.append(c)
    return "".join(out)


def first_rdn_value(dn: str) -> str:
    """'Kosha Engineers' from 'CN=Kosha Engineers,OU=Groups,DC=example,DC=com' (escapes undone)."""
    value = dn.split("=", 1)[1] if "=" in dn else dn
    out, i = bytearray(), 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            pair = value[i + 1:i + 3]
            if len(pair) == 2 and all(ch in "0123456789abcdefABCDEF" for ch in pair):
                out.append(int(pair, 16))
                i += 3
            else:
                out += value[i + 1].encode("utf-8")
                i += 2
            continue
        if c in ",+":
            break
        out += c.encode("utf-8")
        i += 1
    return out.decode("utf-8", "replace").strip()
