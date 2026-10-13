# SPDX-License-Identifier: AGPL-3.0-or-later
"""The clamd client: clean and infected files, size limits, unreachable or silent scanners (fail closed)."""
import io
import os
import sys
import tempfile

import pytest

from fake_clamd import SIGNATURE, VIRUS_NAME, FakeClamd, closed_port
from kosha import config, scan


@pytest.fixture
def clamd():
    c = FakeClamd(max_stream=2 * 1024 * 1024).start()
    yield c
    c.stop()


def test_clean_and_infected_streams(clamd):
    big = io.BytesIO(os.urandom(700_000))            # several INSTREAM chunks
    assert scan.scan_stream(big, clamd.address).clean
    assert clamd.scanned[-1] == 700_000
    r = scan.scan_stream(io.BytesIO(b"header " + SIGNATURE + b" trailer"), clamd.address)
    assert not r.clean and r.signature == VIRUS_NAME
    assert scan.scan_stream(io.BytesIO(b""), clamd.address).clean


def test_a_stream_over_the_scanner_limit_is_refused(clamd):
    with pytest.raises(scan.ScanUnavailable, match="StreamMaxLength"):
        scan.scan_stream(io.BytesIO(b"x" * (3 * 1024 * 1024)), clamd.address)


def test_unreachable_scanner_fails_closed():
    with pytest.raises(scan.ScanUnavailable, match="not reachable"):
        scan.scan_stream(io.BytesIO(b"data"), f"tcp://127.0.0.1:{closed_port()}")


def test_silent_scanner_times_out(monkeypatch):
    c = FakeClamd(silent=True).start()
    monkeypatch.setattr(config, "CLAMD_TIMEOUT_SECONDS", 0.5)
    try:
        with pytest.raises(scan.ScanUnavailable, match="did not answer"):
            scan.scan_stream(io.BytesIO(b"data"), c.address)
    finally:
        c.stop()


@pytest.mark.skipif(not hasattr(__import__("socket"), "AF_UNIX") or sys.platform == "win32", reason="Unix sockets")
def test_unix_socket():
    path = os.path.join(tempfile.mkdtemp(), "clamd.sock")
    c = FakeClamd(unix_path=path).start()
    try:
        assert scan.command("PING", c.address) == "PONG"
        assert not scan.scan_stream(io.BytesIO(SIGNATURE), f"unix:{path}").clean
        assert scan.scan_stream(io.BytesIO(b"fine"), path).clean
    finally:
        c.stop()


def test_status_for_the_admin(clamd, monkeypatch):
    monkeypatch.setattr(config, "CLAMD", "")
    assert scan.status()["configured"] is False
    monkeypatch.setattr(config, "CLAMD", clamd.address)
    st = scan.status()
    assert st["configured"] and st["reachable"] and "ClamAV" in st["version"]
    monkeypatch.setattr(config, "CLAMD", f"tcp://127.0.0.1:{closed_port()}")
    st = scan.status()
    assert st["configured"] and not st["reachable"] and "refused" in st["message"]
