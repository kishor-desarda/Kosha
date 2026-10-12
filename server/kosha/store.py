# SPDX-License-Identifier: AGPL-3.0-or-later
"""Content-addressed file store: every file version is kept once, by SHA-256.

Write order protects against power cuts and crashes: content is written to a temporary
file in the data folder, flushed to disk, checked, and only then moved into place in one
atomic step. A file in the store is therefore always complete and matches its name.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from . import config

CHUNK = 1024 * 1024


def path_for(sha: str) -> Path:
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("not a SHA-256 fingerprint")
    return config.BLOB_DIR / sha[:2] / sha


def _fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _commit(tmp: str, sha: str) -> None:
    """Move a fully written, flushed temporary file into place (atomic on one file system)."""
    dest = path_for(sha)
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)
    _fsync_dir(dest.parent)


def put_stream(stream: BinaryIO) -> tuple[str, int]:
    """Store a file-like object. Returns (sha256, size)."""
    config.ensure_dirs()
    h = hashlib.sha256()
    size = 0
    fd, tmp = tempfile.mkstemp(dir=config.WORK_DIR, prefix="up_")
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                out.write(chunk)
                size += len(chunk)
            out.flush()
            os.fsync(out.fileno())
        sha = h.hexdigest()
        _commit(tmp, sha)
        return sha, size
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def put_bytes(data: bytes) -> tuple[str, int]:
    config.ensure_dirs()
    sha = hashlib.sha256(data).hexdigest()
    if path_for(sha).exists():
        return sha, len(data)
    fd, tmp = tempfile.mkstemp(dir=config.WORK_DIR, prefix="put_")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        _commit(tmp, sha)
        return sha, len(data)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_bytes(sha: str) -> bytes:
    return path_for(sha).read_bytes()


def exists(sha: str) -> bool:
    return path_for(sha).exists()


def verify(sha: str) -> bool:
    """True when the stored content still matches its fingerprint (used by backups and checks)."""
    p = path_for(sha)
    if not p.is_file():
        return False
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest() == sha
