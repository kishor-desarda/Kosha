# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional virus scan of uploads through ClamAV's ``clamd`` (INSTREAM protocol, standard library only).

Set ``KOSHA_CLAMD`` to ``tcp://host:3310`` (or ``host:3310``) or to ``unix:///run/clamav/clamd.ctl`` (or just the
socket path). When it is set, every uploaded file is streamed to clamd before Kosha keeps it. When clamd can't be
reached or can't finish the scan, the upload is refused: Kosha fails closed.

Note: clamd refuses streams longer than its ``StreamMaxLength`` (25 MB by default). Raise it in ``clamd.conf`` to at
least the largest CAD file you expect (``KOSHA_MAX_UPLOAD_MB``), or such uploads are refused.
"""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import BinaryIO

from . import config

CHUNK = 256 * 1024
CONNECT_TIMEOUT = 5.0


class ScanUnavailable(RuntimeError):
    """clamd could not be reached or could not scan the file; the upload must be refused."""


@dataclass
class ScanResult:
    clean: bool
    signature: str = ""


def address(spec: str | None = None) -> tuple[str, object] | None:
    """('unix', path) or ('tcp', (host, port)) for a KOSHA_CLAMD value; None when no scanner is configured."""
    spec = (config.CLAMD if spec is None else spec).strip()
    if not spec:
        return None
    if spec.startswith("unix://"):
        return "unix", spec[len("unix://"):]
    if spec.startswith("unix:"):
        return "unix", spec[len("unix:"):]
    if spec.startswith("/"):
        return "unix", spec
    if spec.startswith("tcp://"):
        spec = spec[len("tcp://"):]
    spec = spec.rstrip("/")
    if spec.startswith("["):                      # [::1]:3310
        host, _, rest = spec[1:].partition("]")
        port = rest.lstrip(":") or "3310"
    elif spec.count(":") == 1:
        host, port = spec.split(":")
    else:
        host, port = spec, "3310"
    try:
        return "tcp", (host, int(port))
    except ValueError:
        raise ScanUnavailable(f"KOSHA_CLAMD is not a valid address: {spec!r}")


def configured() -> bool:
    return bool(config.CLAMD.strip())


def _connect(spec: str | None = None, timeout: float | None = None) -> socket.socket:
    addr = address(spec)
    if addr is None:
        raise ScanUnavailable("No virus scanner is configured.")
    kind, where = addr
    try:
        if kind == "unix":
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(CONNECT_TIMEOUT)
            s.connect(str(where))
        else:
            s = socket.create_connection(where, timeout=CONNECT_TIMEOUT)
    except (OSError, AttributeError) as e:   # AttributeError: no Unix sockets on this system
        raise ScanUnavailable(f"the virus scanner at {config.CLAMD if spec is None else spec} is not reachable ({e})")
    s.settimeout(config.CLAMD_TIMEOUT_SECONDS if timeout is None else timeout)
    return s


def _reply(s: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\0") and not buf.endswith(b"\n"):
        part = s.recv(4096)
        if not part:
            break
        buf += part
        if len(buf) > 65536:
            break
    return buf.rstrip(b"\0\n").decode("utf-8", "replace").strip()


def command(name: str, spec: str | None = None, timeout: float = 5.0) -> str:
    """Send a simple command (PING, VERSION) and return the reply."""
    s = _connect(spec, timeout)
    try:
        s.sendall(b"z" + name.encode() + b"\0")
        return _reply(s)
    except OSError as e:
        raise ScanUnavailable(f"the virus scanner did not answer ({e})")
    finally:
        s.close()


def scan_stream(stream: BinaryIO, spec: str | None = None) -> ScanResult:
    """Stream a file to clamd. Returns clean / infected; raises ScanUnavailable when there is no reliable answer."""
    s = _connect(spec)
    try:
        try:
            s.sendall(b"zINSTREAM\0")
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                s.sendall(struct.pack("!L", len(chunk)) + chunk)
            s.sendall(struct.pack("!L", 0))
        except OSError as e:
            # clamd closes the connection early when a stream is over its size limit; read why if it said so
            try:
                why = _reply(s)
            except OSError:
                why = ""
            raise ScanUnavailable(_explain(why) if why else f"the virus scanner stopped receiving the file ({e})")
        try:
            reply = _reply(s)
        except OSError as e:
            raise ScanUnavailable(f"the virus scanner did not answer in time ({e})")
    finally:
        s.close()
    if reply.endswith("FOUND"):
        sig = reply[:-len("FOUND")].strip()
        if sig.startswith("stream:"):
            sig = sig[len("stream:"):].strip()
        return ScanResult(False, sig or "unknown")
    if reply.endswith("OK"):
        return ScanResult(True)
    raise ScanUnavailable(_explain(reply))


def _explain(reply: str) -> str:
    if "size limit" in reply.lower():
        return ("the file is larger than the virus scanner accepts (StreamMaxLength in clamd.conf); "
                "ask your admin to raise that limit")
    return f"the virus scanner could not check the file ({reply or 'no answer'})"


def status() -> dict:
    """For the admin diagnostics: is a scanner configured, and does it answer?"""
    if not configured():
        return {"configured": False, "reachable": None, "address": "", "version": "",
                "message": "No virus scanner is configured (KOSHA_CLAMD). Uploads are not scanned."}
    out = {"configured": True, "address": config.CLAMD, "reachable": False, "version": ""}
    try:
        if command("PING") != "PONG":
            raise ScanUnavailable("the virus scanner gave an unexpected answer to PING")
        out["reachable"] = True
        try:
            out["version"] = command("VERSION")
        except ScanUnavailable:
            pass
        out["message"] = "Every upload is scanned for viruses."
    except ScanUnavailable as e:
        out["message"] = f"Uploads are refused until it is back: {e}."
    return out
