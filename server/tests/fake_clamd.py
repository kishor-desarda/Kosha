# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small stand-in for ClamAV's clamd, for tests: PING, VERSION and INSTREAM over TCP or a Unix socket.

A stream that contains ``SIGNATURE`` is reported as infected; one longer than ``max_stream`` gets clamd's
"size limit exceeded" error; with ``silent=True`` the server accepts connections but never answers.
"""
from __future__ import annotations

import os
import socket
import socketserver
import struct
import threading

SIGNATURE = b"KOSHA-TEST-VIRUS-SIGNATURE-NOT-A-REAL-VIRUS"
VIRUS_NAME = "Kosha.Test.Signature"


class _Handler(socketserver.BaseRequestHandler):
    def _read_command(self) -> bytes:
        buf = b""
        while True:
            c = self.request.recv(1)
            if not c or c in (b"\0", b"\n"):
                return buf
            buf += c

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            part = self.request.recv(n - len(buf))
            if not part:
                raise ConnectionError("client went away")
            buf += part
        return buf

    def handle(self):
        srv = self.server.fake
        cmd = self._read_command().lstrip(b"zn")
        srv.commands.append(cmd.decode(errors="replace"))
        if srv.silent:
            srv.release.wait(10)
            return
        if cmd == b"PING":
            self.request.sendall(b"PONG\0")
        elif cmd == b"VERSION":
            self.request.sendall(b"ClamAV 1.4.0/27000/Fake\0")
        elif cmd == b"INSTREAM":
            data = b""
            try:
                while True:
                    (n,) = struct.unpack("!L", self._recv_exact(4))
                    if n == 0:
                        break
                    data += self._recv_exact(n)
                    if len(data) > srv.max_stream:
                        self.request.sendall(b"INSTREAM size limit exceeded. ERROR\0")
                        return
            except ConnectionError:
                return
            srv.scanned.append(len(data))
            if SIGNATURE in data:
                self.request.sendall(f"stream: {VIRUS_NAME} FOUND\0".encode())
            else:
                self.request.sendall(b"stream: OK\0")
        else:
            self.request.sendall(b"UNKNOWN COMMAND\0")


class _TCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if hasattr(socketserver, "ThreadingUnixStreamServer"):
    class _Unix(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True


class FakeClamd:
    def __init__(self, unix_path: str | None = None, max_stream: int = 25 * 1024 * 1024, silent: bool = False):
        self.max_stream, self.silent = max_stream, silent
        self.commands: list[str] = []
        self.scanned: list[int] = []
        self.release = threading.Event()
        self.unix_path = unix_path
        if unix_path:
            self.server = _Unix(unix_path, _Handler)
            self.address = f"unix://{unix_path}"
        else:
            self.server = _TCP(("127.0.0.1", 0), _Handler)
            self.address = f"tcp://127.0.0.1:{self.server.server_address[1]}"
        self.server.fake = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "FakeClamd":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        if self.unix_path and os.path.exists(self.unix_path):
            os.unlink(self.unix_path)


def closed_port() -> int:
    """A local TCP port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
