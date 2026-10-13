# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""A small LDAP v3 server for tests: simple bind, search with filters, StartTLS and LDAPS, unbind.

It holds two made-up directories of *Demo Engineering*: one shaped like Active Directory (objectGUID, memberOf,
nested groups, disabled accounts) and one like OpenLDAP (entryUUID, groupOfNames, posixGroup). Like real
servers it accepts a bind with an empty password as an anonymous bind, so the tests can show that the add-in
never takes that for a correct password.
"""
from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from kosha_ldap import ber

STARTTLS = "1.3.6.1.4.1.1466.20037"
AD_BASE = "DC=demo,DC=example,DC=com"
OL_BASE = "dc=demo,dc=example,dc=org"
GUIDS = {"asha": uuid.UUID("6f1d2b3c-0001-4a5b-8c9d-0123456789ab"),
         "daniel": uuid.UUID("6f1d2b3c-0002-4a5b-8c9d-0123456789ab"),
         "tom": uuid.UUID("6f1d2b3c-0004-4a5b-8c9d-0123456789ab")}
PASSWORD = "Mixer-Line-2026"
SERVICE_PASSWORD = "svc-secret-123"


def ad_entries() -> dict[str, dict[str, list]]:
    people = f"OU=People,{AD_BASE}"
    groups = f"OU=Groups,{AD_BASE}"

    def person(cn, sam, key, uac="512", member_of=()):
        return {"objectClass": ["top", "person", "organizationalPerson", "user"], "objectCategory": ["person"],
                "cn": [cn], "displayName": [cn], "sAMAccountName": [sam], "userPrincipalName": [f"{sam}@demo.example.com"],
                "mail": [f"{sam}@demo.example.com"], "objectGUID": [GUIDS[key].bytes_le], "userAccountControl": [uac],
                "memberOf": list(member_of)}
    eng, leads, appr = f"CN=Kosha Engineers,{groups}", f"CN=Design Leads,{groups}", f"CN=Kosha Approvers,{groups}"
    return {
        AD_BASE: {"objectClass": ["top", "domain"]},
        f"CN=svc-kosha,OU=Service,{AD_BASE}": {"objectClass": ["user"], "sAMAccountName": ["svc-kosha"]},
        f"CN=Asha Rao,{people}": person("Asha Rao", "asha", "asha", member_of=[eng]),
        f"CN=Daniel Okafor,{people}": person("Daniel Okafor", "daniel", "daniel", member_of=[leads]),
        f"CN=Tom Berg,{people}": person("Tom Berg", "tom", "tom", uac="514"),           # disabled
        eng: {"objectClass": ["top", "group"], "cn": ["Kosha Engineers"], "member": [f"CN=Asha Rao,{people}"]},
        leads: {"objectClass": ["top", "group"], "cn": ["Design Leads"], "member": [f"CN=Daniel Okafor,{people}"]},
        appr: {"objectClass": ["top", "group"], "cn": ["Kosha Approvers"], "member": [leads]},
    }


def ol_entries() -> dict[str, dict[str, list]]:
    return {
        OL_BASE: {"objectClass": ["dcObject", "organization"]},
        f"cn=kosha,ou=services,{OL_BASE}": {"objectClass": ["applicationProcess"], "cn": ["kosha"]},
        f"uid=sofia,ou=people,{OL_BASE}": {"objectClass": ["top", "person", "inetOrgPerson", "posixAccount"],
                                           "uid": ["sofia"], "cn": ["Sofia Lindqvist"], "givenName": ["Sofia"],
                                           "sn": ["Lindqvist"], "mail": ["sofia@demo.example.org"],
                                           "entryUUID": ["3c9a1e52-5b1f-4f8e-9d43-000000000003"]},
        f"cn=kosha-viewers,ou=groups,{OL_BASE}": {"objectClass": ["groupOfNames"], "cn": ["kosha-viewers"],
                                                  "member": [f"uid=sofia,ou=people,{OL_BASE}"]},
        f"cn=cad-users,ou=groups,{OL_BASE}": {"objectClass": ["posixGroup"], "cn": ["cad-users"], "memberUid": ["sofia"]},
        f"cn=sales,ou=groups,{OL_BASE}": {"objectClass": ["groupOfNames"], "cn": ["sales"],
                                          "member": [f"uid=someone,ou=people,{OL_BASE}"]},
    }


def passwords() -> dict[str, str]:
    return {f"CN=svc-kosha,OU=Service,{AD_BASE}".lower(): SERVICE_PASSWORD,
            f"CN=Asha Rao,OU=People,{AD_BASE}".lower(): PASSWORD,
            f"CN=Daniel Okafor,OU=People,{AD_BASE}".lower(): PASSWORD,
            f"cn=kosha,ou=services,{OL_BASE}".lower(): SERVICE_PASSWORD,
            f"uid=sofia,ou=people,{OL_BASE}".lower(): PASSWORD}


def make_certificate(folder: Path) -> tuple[Path, Path] | None:
    """A self-signed certificate for 127.0.0.1 (with the openssl program), or None when openssl is missing."""
    if not shutil.which("openssl"):
        return None
    cert, key = folder / "cert.pem", folder / "key.pem"
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj",
                        "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
                        "-keyout", str(key), "-out", str(cert)], capture_output=True)
    return (cert, key) if r.returncode == 0 else None


def _b(v) -> bytes:
    return v if isinstance(v, bytes) else str(v).encode("utf-8")


class FakeLdap:
    def __init__(self, entries: dict, pw: dict, *, cert: tuple[Path, Path] | None = None, ldaps: bool = False):
        self.entries = {dn: {k.lower(): [_b(v) for v in vals] for k, vals in attrs.items()} for dn, attrs in entries.items()}
        self.names = {dn.lower(): dn for dn in entries}
        self.pw = pw
        self.mode: set[str] = set()          # busy | hang | no_starttls | require_tls | duplicate
        self.log: list[tuple] = []
        self.ldaps = ldaps
        self.tls = None
        if cert:
            self.tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            self.tls.load_cert_chain(str(cert[0]), str(cert[1]))
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self._stop = False

    @property
    def url(self) -> str:
        return f"{'ldaps' if self.ldaps else 'ldap'}://127.0.0.1:{self.port}"

    def start(self) -> "FakeLdap":
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass

    def _accept(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    # ------------------------------------------------------------------ one connection
    def _serve(self, conn: socket.socket) -> None:
        state = {"bound": None, "tls": False, "buf": b""}
        try:
            if self.ldaps:
                conn = self.tls.wrap_socket(conn, server_side=True)
                state["tls"] = True
            while True:
                msg = self._read(conn, state)
                if msg is None:
                    return
                msg_id, op = msg
                if "hang" in self.mode:
                    continue
                if op.tag == 0x42:                                          # unbind
                    self.log.append(("unbind",))
                    return
                if op.tag == 0x60:
                    self._bind(conn, state, msg_id, op)
                elif op.tag == 0x77:
                    oid = op.children()[0].text()
                    self.log.append(("extended", oid))
                    if oid != STARTTLS or "no_starttls" in self.mode or not self.tls:
                        self._send(conn, msg_id, self._result(0x78, 2, "unsupported extended operation"))
                        continue
                    self._send(conn, msg_id, self._result(0x78, 0, ""))
                    conn = self.tls.wrap_socket(conn, server_side=True)
                    state["tls"] = True
                elif op.tag == 0x63:
                    self._search(conn, state, msg_id, op)
                else:
                    self._send(conn, msg_id, self._result(0x65, 2, "not supported here"))
        except (OSError, ssl.SSLError, ber.BerError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _read(self, conn, state):
        while True:
            size = ber.message_size(state["buf"])
            if size is not None and len(state["buf"]) >= size:
                raw, state["buf"] = state["buf"][:size], state["buf"][size:]
                msg, _ = ber.decode(raw)
                parts = msg.children()
                return parts[0].int(), parts[1]
            chunk = conn.recv(65536)
            if not chunk:
                return None
            state["buf"] += chunk

    @staticmethod
    def _send(conn, msg_id: int, op: bytes) -> None:
        conn.sendall(ber.sequence(ber.integer(msg_id), op))

    @staticmethod
    def _result(tag: int, code: int, message: str) -> bytes:
        return ber.sequence(ber.enumerated(code), ber.octets(""), ber.octets(message), tag=tag)

    def _bind(self, conn, state, msg_id, op) -> None:
        parts = op.children()
        name, auth = parts[1].text(), parts[2]
        password = auth.content.decode("utf-8", "replace")
        self.log.append(("bind", name, bool(password), state["tls"]))
        if "busy" in self.mode:
            self._send(conn, msg_id, self._result(0x61, 51, "server is busy"))
            return
        if "require_tls" in self.mode and not state["tls"]:
            self._send(conn, msg_id, self._result(0x61, 13, "confidentiality required"))
            return
        if auth.tag != 0x80:
            self._send(conn, msg_id, self._result(0x61, 7, "only simple binds"))
            return
        if not password:                        # an unauthenticated bind: "works", but grants nothing
            state["bound"] = None
            self._send(conn, msg_id, self._result(0x61, 0, ""))
            return
        dn = name.lower()
        if "@" in name and "=" not in name:     # Active Directory also takes user@domain
            dn = next((d.lower() for d, a in self.entries.items()
                       if any(v.decode().lower() == name.lower() for v in a.get("userprincipalname", []))), "")
        if dn in self.pw and self.pw[dn] == password and self._enabled(dn):
            state["bound"] = dn
            self._send(conn, msg_id, self._result(0x61, 0, ""))
        else:
            self._send(conn, msg_id, self._result(0x61, 49, "80090308: LdapErr: DSID-0C09042A, data 52e"))

    def _enabled(self, dn: str) -> bool:
        uac = self.entries.get(self.names.get(dn, ""), {}).get("useraccountcontrol", [b"512"])[0]
        return not int(uac) & 2

    def _search(self, conn, state, msg_id, op) -> None:
        parts = op.children()
        base, scope, size_limit = parts[0].text(), parts[1].int(), parts[3].int()
        flt, wanted = parts[6], [a.text().lower() for a in parts[7].children()]
        self.log.append(("search", base, state["bound"]))
        if state["bound"] is None:
            self._send(conn, msg_id, self._result(0x65, 1, "000004DC: LdapErr: DSID-0C090A5C, a successful bind "
                                                            "must be completed on the connection"))
            return
        if base.lower() not in self.names:
            self._send(conn, msg_id, self._result(0x65, 32, "no such object"))
            return
        sent = 0
        for dn, attrs in self.entries.items():
            low, b = dn.lower(), base.lower()
            in_scope = low == b if scope == 0 else (low.endswith("," + b) and (scope == 2 or low.count(",") == b.count(",") + 1))
            if not in_scope or not self._match(flt, dn, attrs):
                continue
            if size_limit and sent >= size_limit:
                self._send(conn, msg_id, self._result(0x65, 4, "size limit exceeded"))
                return
            chosen = {k: v for k, v in attrs.items() if not wanted or k in wanted}
            body = ber.sequence(*(ber.sequence(ber.octets(k), ber.sequence(*(ber.octets(v) for v in vals), tag=ber.SET))
                                  for k, vals in chosen.items()))
            self._send(conn, msg_id, ber.sequence(ber.octets(dn), body, tag=0x64))
            sent += 1
        if "duplicate" in self.mode and sent == 1 and b"sAMAccountName" in flt.content:   # a second "asha"
            self._send(conn, msg_id, ber.sequence(ber.octets(f"CN=Another Asha,OU=People,{AD_BASE}"), ber.sequence(),
                                                  tag=0x64))
        self._send(conn, msg_id, ber.sequence(ber.octets(f"ldap://elsewhere.example.com/{AD_BASE}"), tag=0x73))
        self._send(conn, msg_id, self._result(0x65, 0, ""))

    # ------------------------------------------------------------------ filters
    def _values(self, attrs, name: str) -> list[str]:
        return [v.decode("utf-8", "replace").lower() for v in attrs.get(name.lower(), [])]

    def _match(self, f: ber.Element, dn: str, attrs: dict) -> bool:
        t = f.tag
        if t == 0xA0:
            return all(self._match(c, dn, attrs) for c in f.children())
        if t == 0xA1:
            return any(self._match(c, dn, attrs) for c in f.children())
        if t == 0xA2:
            return not self._match(f.children()[0], dn, attrs)
        if t == 0x87:
            name = f.content.decode().lower()
            return name == "objectclass" or name in attrs
        if t == 0xA3:
            a, v = f.children()
            return v.text().lower() in self._values(attrs, a.text())
        if t == 0xA4:
            a, subs = f.children()
            for value in self._values(attrs, a.text()):
                pos, ok = 0, True
                for s in subs.children():
                    piece = s.text().lower()
                    if s.tag == 0x80:
                        ok = value.startswith(piece)
                        pos = len(piece)
                    elif s.tag == 0x81:
                        i = value.find(piece, pos)
                        ok, pos = i >= 0, (i + len(piece) if i >= 0 else pos)
                    else:
                        ok = value.endswith(piece) and len(value) - len(piece) >= pos
                    if not ok:
                        break
                if ok:
                    return True
            return False
        if t == 0xA9:
            rule = attr = value = ""
            for c in f.children():
                if c.tag == 0x81:
                    rule = c.text()
                elif c.tag == 0x82:
                    attr = c.text()
                elif c.tag == 0x83:
                    value = c.text()
            if rule == "1.2.840.113556.1.4.803":                     # bitwise AND
                return any(int(v) & int(value) == int(value) for v in self._values(attrs, attr) if v.isdigit())
            if rule == "1.2.840.113556.1.4.1941" and attr.lower() == "member":   # member, through nested groups
                return self._in_chain(dn, value.lower(), set())
            return False
        return False

    def _in_chain(self, group_dn: str, target: str, seen: set) -> bool:
        if group_dn.lower() in seen:
            return False
        seen.add(group_dn.lower())
        members = self._values(self.entries.get(group_dn, {}), "member")
        if target in members:
            return True
        return any(self._in_chain(self.names[m], target, seen) for m in members if m in self.names)


def tls_folder() -> Path:
    return Path(tempfile.mkdtemp(prefix="kosha-ldap-tls-"))
