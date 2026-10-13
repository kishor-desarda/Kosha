# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tiny SMTP server for tests (Python 3.12 removed smtpd). Standard library only.

    smtp = FakeSmtp(user="kosha", password="pw").start()
    ... send to 127.0.0.1:smtp.port ...
    smtp.messages[0]["message"]["Subject"]
    smtp.stop()

Speaks EHLO/HELO, AUTH PLAIN and LOGIN, MAIL, RCPT, DATA, RSET, NOOP, QUIT, and optionally STARTTLS or
TLS from the first byte (with a certificate made by the openssl tool). `mode` simulates trouble:
"refuse_rcpt" (550 for every address) or "hang" (accepts, then never answers).
"""
from __future__ import annotations

import base64
import email
import email.policy
import shutil
import socketserver
import ssl
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def self_signed_context() -> ssl.SSLContext | None:
    """A server context with a throw-away certificate, or None when the openssl tool is missing."""
    if not shutil.which("openssl"):
        return None
    d = Path(tempfile.mkdtemp(prefix="kosha-smtp-tls-"))
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "k.pem"),
                        "-out", str(d / "c.pem"), "-days", "1", "-subj", "/CN=localhost"], capture_output=True)
    if r.returncode != 0:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "c.pem", d / "k.pem")
    return ctx


class _Handler(socketserver.StreamRequestHandler):
    timeout = 30

    def _say(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())
        self.wfile.flush()

    def _read(self) -> str:
        return self.rfile.readline(65536).decode("utf-8", "replace").rstrip("\r\n")

    def _wrap(self, ctx: ssl.SSLContext) -> None:
        self.connection = ctx.wrap_socket(self.connection, server_side=True)
        self.rfile = self.connection.makefile("rb")
        self.wfile = self.connection.makefile("wb")

    def handle(self) -> None:
        fake: FakeSmtp = self.server.fake          # type: ignore[attr-defined]
        if fake.mode == "hang":
            time.sleep(fake.hang_seconds)
            return
        if fake.implicit_tls and fake.tls:
            try:
                self._wrap(fake.tls)
            except (ssl.SSLError, OSError):
                return
        self._say("220 fake-smtp.example.com ESMTP ready")
        mail_from, rcpts, authed = None, [], False
        while True:
            line = self._read()
            if not line and self.rfile.closed:
                return
            if not line:
                return
            verb, _, arg = line.partition(" ")
            verb = verb.upper()
            if verb == "EHLO":
                caps = ["fake-smtp.example.com", "8BITMIME", "SIZE 10000000"]
                if fake.tls and not fake.implicit_tls and not isinstance(self.connection, ssl.SSLSocket):
                    caps.append("STARTTLS")
                if fake.user:
                    caps.append("AUTH PLAIN LOGIN")
                for c in caps[:-1]:
                    self._say(f"250-{c}")
                self._say(f"250 {caps[-1]}")
            elif verb == "HELO":
                self._say("250 fake-smtp.example.com")
            elif verb == "STARTTLS" and fake.tls and not fake.implicit_tls:
                self._say("220 2.0.0 Ready to start TLS")
                try:
                    self._wrap(fake.tls)
                except (ssl.SSLError, OSError):
                    return
            elif verb == "AUTH":
                mech, _, initial = arg.partition(" ")
                if mech.upper() == "PLAIN":
                    if not initial:
                        self._say("334 ")
                        initial = self._read()
                    parts = base64.b64decode(initial or "").split(b"\0")
                    user, pw = (parts[1].decode(), parts[2].decode()) if len(parts) == 3 else ("", "")
                elif mech.upper() == "LOGIN":
                    self._say("334 VXNlcm5hbWU6")
                    user = base64.b64decode(self._read()).decode()
                    self._say("334 UGFzc3dvcmQ6")
                    pw = base64.b64decode(self._read()).decode()
                else:
                    self._say("504 5.5.4 Unrecognized authentication type")
                    continue
                if (user, pw) == (fake.user, fake.password):
                    authed = True
                    self._say("235 2.7.0 Authentication successful")
                else:
                    self._say("535 5.7.8 Authentication credentials invalid")
            elif verb == "MAIL":
                if fake.user and not authed:
                    self._say("530 5.7.0 Authentication required")
                    continue
                mail_from, rcpts = arg.split(":", 1)[-1].strip().split()[0].strip("<>"), []
                self._say("250 2.1.0 Ok")
            elif verb == "RCPT":
                addr = arg.split(":", 1)[-1].strip().strip("<>")
                if fake.mode == "refuse_rcpt":
                    self._say("550 5.1.1 No such user here")
                    continue
                rcpts.append(addr)
                self._say("250 2.1.5 Ok")
            elif verb == "DATA":
                if not rcpts:
                    self._say("503 5.5.1 Need RCPT first")
                    continue
                self._say("354 End data with <CR><LF>.<CR><LF>")
                buf = []
                while True:
                    raw = self.rfile.readline(1 << 20)
                    if not raw or raw in (b".\r\n", b".\n"):
                        break
                    buf.append(raw[1:] if raw.startswith(b"..") else raw)
                data = b"".join(buf)
                with fake.lock:
                    fake.messages.append({"from": mail_from, "to": list(rcpts), "data": data,
                                          "message": email.message_from_bytes(data, policy=email.policy.default),
                                          "tls": isinstance(self.connection, ssl.SSLSocket)})
                self._say("250 2.0.0 Ok: queued")
            elif verb == "RSET":
                mail_from, rcpts = None, []
                self._say("250 2.0.0 Ok")
            elif verb == "NOOP":
                self._say("250 2.0.0 Ok")
            elif verb == "QUIT":
                self._say("221 2.0.0 Bye")
                return
            else:
                self._say("502 5.5.2 Command not recognized")


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class FakeSmtp:
    def __init__(self, user: str = "", password: str = "", tls: ssl.SSLContext | None = None, implicit_tls: bool = False):
        self.user, self.password, self.tls, self.implicit_tls = user, password, tls, implicit_tls
        self.mode = "ok"
        self.hang_seconds = 5.0
        self.messages: list[dict] = []
        self.lock = threading.Lock()
        self._srv = _Server(("127.0.0.1", 0), _Handler)
        self._srv.fake = self
        self.port = self._srv.server_address[1]

    def start(self) -> "FakeSmtp":
        threading.Thread(target=self._srv.serve_forever, name="fake-smtp", daemon=True).start()
        return self

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    def wait_for(self, n: int, timeout: float = 10.0) -> list[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline and len(self.messages) < n:
            time.sleep(0.05)
        return self.messages
