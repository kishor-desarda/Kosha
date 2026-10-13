# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end test harness: a real Kosha server in its own process, its own data folder and port.

    def test_something(kosha):                 # fixture from conftest.py (empty installation)
        admin = kosha.client()                 # signed in as admin
        r = admin.call("GET", "/api/health")

    def test_with_demo(demo_kosha):            # Demo Engineering data loaded
        asha = demo_kosha.client("asha", "demo-kosha-1")

Each test module gets a fresh server, so tests never share state with other modules.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
ADMIN_PASSWORD = "admin-test-pass-1"
DEMO_PASSWORD = "demo-kosha-1"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base: str, token: str | None = None):
        self.base, self.token = base, token

    def call(self, method: str, path: str, body=None, files=None, fields=None, expect=200, raw=False,
             headers: dict | None = None, raw_body: bytes | None = None):
        """files = {"file": ("name.dxf", b"...")}; returns parsed JSON (or (bytes, content_type) when raw)."""
        hdrs = dict(headers or {})
        data = None
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        if files is not None or fields is not None:
            boundary = uuid.uuid4().hex
            buf = io.BytesIO()
            for k, v in (fields or {}).items():
                buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
            for k, (name, content) in (files or {}).items():
                buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                          f"Content-Type: application/octet-stream\r\n\r\n".encode())
                buf.write(content)
                buf.write(b"\r\n")
            buf.write(f"--{boundary}--\r\n".encode())
            data = buf.getvalue()
            hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        elif raw_body is not None:
            data = raw_body
            hdrs.setdefault("Content-Type", "application/octet-stream")
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, payload, ctype = r.status, r.read(), r.headers.get("content-type", "")
        except urllib.error.HTTPError as e:
            status, payload, ctype = e.code, e.read(), e.headers.get("content-type", "")
        assert status == expect, f"{method} {path}: expected {expect}, got {status}: {payload[:500]!r}"
        if raw:
            return payload, ctype
        return json.loads(payload) if payload and "json" in ctype else payload


class KoshaServer:
    def __init__(self, demo: bool = False, env: dict | None = None):
        self.demo = demo
        self.env = env or {}
        self.data_dir = Path(tempfile.mkdtemp(prefix="kosha-e2e-"))
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self.log = self.data_dir / "server.log"

    def start(self) -> "KoshaServer":
        env = dict(os.environ, KOSHA_DATA_DIR=str(self.data_dir / "data"), KOSHA_ADMIN_PASSWORD=ADMIN_PASSWORD,
                   KOSHA_DISABLE_WORKER="1", KOSHA_HOST="127.0.0.1", KOSHA_PORT=str(self.port))
        env.update(self.env)     # a test may override any of the above
        cmd = [sys.executable, "-m", "kosha"] + (["--demo"] if self.demo else [])
        self._logf = open(self.log, "wb")
        self.proc = subprocess.Popen(cmd, cwd=SERVER_DIR, env=env, stdout=self._logf, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("Kosha exited during start-up:\n" + self.log.read_text(errors="replace")[-3000:])
            try:
                urllib.request.urlopen(self.base + "/api/health", timeout=1)
                return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("Kosha did not start:\n" + self.log.read_text(errors="replace")[-3000:])

    def client(self, username: str = "admin", password: str | None = None) -> Client:
        if password is None:
            password = ADMIN_PASSWORD if username == "admin" else DEMO_PASSWORD
        r = Client(self.base).call("POST", "/api/auth/login", {"username": username, "password": password})
        return Client(self.base, r["token"])

    def add_user(self, username: str, role: str, password: str = "password-123", full_name: str | None = None) -> Client:
        self.client().call("POST", "/api/users", {"username": username, "full_name": full_name or username.title(),
                                                  "role": role, "password": password})
        return self.client(username, password)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "_logf", None):
            self._logf.close()
        shutil.rmtree(self.data_dir, ignore_errors=True)
