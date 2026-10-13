# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end test harness: a real Kosha server in its own process, its own data folder and port.

    def test_something(kosha):                 # fixture from conftest.py (empty installation)
        admin = kosha.client()                 # signed in as admin
        r = admin.call("GET", "/api/health")

    def test_with_demo(demo_kosha):            # Demo Engineering data loaded
        asha = demo_kosha.client("asha", "demo-kosha-1")

Each test module gets a fresh server, so tests never share state with other modules.

With KOSHA_TEST_DATABASE_URL set (a PostgreSQL URL whose user may create databases, for example
postgresql://kosha:secret@localhost:5432/postgres), every server gets its own new PostgreSQL database,
dropped again when the server stops; without it, every server uses its own SQLite file. The suites that
run as scripts get the same with:  python tests/harness.py run tests/test_api.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SERVER_DIR = Path(__file__).resolve().parent.parent
ADMIN_PASSWORD = "admin-test-pass-1"
DEMO_PASSWORD = "demo-kosha-1"
TEST_DATABASE_URL = os.environ.get("KOSHA_TEST_DATABASE_URL", "").strip()


def _with_database(url: str, name: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


def _admin(url: str):
    import psycopg   # only needed for PostgreSQL runs: pip install "psycopg[binary]"
    return psycopg.connect(url, autocommit=True)


def create_test_database(prefix: str = "kosha_e2e") -> str:
    """A new, empty PostgreSQL database next to KOSHA_TEST_DATABASE_URL; returns its URL."""
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    with _admin(TEST_DATABASE_URL) as c:
        try:   # code-point order like SQLite; C.UTF-8 is missing on a few systems
            c.execute(f"CREATE DATABASE {name} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' "
                      "LC_CTYPE 'C.UTF-8'")
        except Exception:
            c.execute(f"CREATE DATABASE {name} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
    return _with_database(TEST_DATABASE_URL, name)


def drop_test_database(url: str) -> None:
    name = urlsplit(url).path.lstrip("/")
    with _admin(TEST_DATABASE_URL) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


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


class _TestDb:
    """``with server.db() as con:`` as with sqlite3: commits at the end (rolls back on an error), then closes."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, *_):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()


class KoshaServer:
    def __init__(self, demo: bool = False, env: dict | None = None):
        self.demo = demo
        self.env = env or {}
        self.data_dir = Path(tempfile.mkdtemp(prefix="kosha-e2e-"))
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self.log = self.data_dir / "server.log"
        self.database_url = ""      # set when this server runs on its own PostgreSQL database

    def start(self) -> "KoshaServer":
        extra = dict(self.env)
        if TEST_DATABASE_URL and "KOSHA_DATABASE_URL" not in extra:
            self.database_url = create_test_database()
            extra["KOSHA_DATABASE_URL"] = self.database_url
        env = dict(os.environ, KOSHA_DATA_DIR=str(self.data_dir / "data"), KOSHA_ADMIN_PASSWORD=ADMIN_PASSWORD,
                   KOSHA_DISABLE_WORKER="1", KOSHA_API_RATE_LIMIT_PER_MINUTE="0", KOSHA_HOST="127.0.0.1", KOSHA_PORT=str(self.port))
        env.update(extra)     # a test may override any of the above; the PostgreSQL URL comes in here too
        cmd = [sys.executable, "-m", "kosha"] + (["--demo"] if self.demo else [])
        self._logf = open(self.log, "wb")
        self.proc = subprocess.Popen(cmd, cwd=SERVER_DIR, env=env, stdout=self._logf, stderr=subprocess.STDOUT)
        try:
            self._wait_until_up()
        except BaseException:
            self.stop()          # also drops its PostgreSQL database
            raise
        return self

    def _wait_until_up(self) -> None:
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("Kosha exited during start-up:\n" + self.log.read_text(errors="replace")[-3000:])
            try:
                urllib.request.urlopen(self.base + "/api/health", timeout=1)
                return
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

    def db(self) -> _TestDb:
        """The server's own database (its SQLite file, or its PostgreSQL database through kosha.pgsql, so SQL is
        written the SQLite way either way), for the few checks a test cannot make through the API."""
        if self.database_url:
            if str(SERVER_DIR) not in sys.path:
                sys.path.insert(0, str(SERVER_DIR))
            from kosha import pgsql
            return _TestDb(pgsql.connect(self.database_url, "kosha-tests"))
        return _TestDb(sqlite3.connect(self.data_dir / "data" / "kosha.db", timeout=30))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "_logf", None):
            self._logf.close()
        if self.database_url:
            drop_test_database(self.database_url)
            self.database_url = ""
        shutil.rmtree(self.data_dir, ignore_errors=True)


def run_script(argv: list[str]) -> int:
    """Run a test script; with KOSHA_TEST_DATABASE_URL, on a new PostgreSQL database that is dropped afterwards."""
    env = dict(os.environ)
    url = create_test_database("kosha_script") if TEST_DATABASE_URL else ""
    if url:
        env["KOSHA_DATABASE_URL"] = url
        print(f"Running {' '.join(argv)} on PostgreSQL database {urlsplit(url).path.lstrip('/')}", flush=True)
    try:
        return subprocess.call([sys.executable, *argv], cwd=SERVER_DIR, env=env)
    finally:
        if url:
            drop_test_database(url)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "run":
        print("Usage: python tests/harness.py run tests/test_api.py [arguments]")
        sys.exit(2)
    sys.exit(run_script(sys.argv[2:]))
