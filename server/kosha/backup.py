# SPDX-License-Identifier: AGPL-3.0-or-later
"""Online backup:  python -m kosha.backup /srv/backups/kosha

Copies the database with SQLite's backup API (safe while Kosha is running), checks the
copy with SQLite's integrity check, and mirrors the content-addressed file store. Stored
files never change, so only new ones are copied, and every copied file is checked against
its fingerprint before it counts. The last 14 database copies are kept.

On PostgreSQL (KOSHA_DATABASE_URL) the database copy is made with pg_dump (a consistent
snapshot, also while Kosha runs) and checked by reading it back with pg_restore --list.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from . import config

KEEP_DB_COPIES = 14


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_db_postgres(dest: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (dest / "db").mkdir(parents=True, exist_ok=True)
    final = dest / "db" / f"kosha-{stamp}.dump"
    partial = final.with_suffix(".partial")
    pg_dump, pg_restore = shutil.which("pg_dump"), shutil.which("pg_restore")
    if not pg_dump or not pg_restore:
        raise BackupError("pg_dump and pg_restore were not found. Install the PostgreSQL client tools "
                          "(the same or a newer version than the server); the previous backups are untouched.")
    p = urlsplit(config.DATABASE_URL)
    env = dict(os.environ)
    if p.password:          # never on the command line, where other users could read it
        env["PGPASSWORD"] = unquote(p.password)
        host = p.hostname or ""
        host = (f"[{host}]" if ":" in host else host) + (f":{p.port}" if p.port else "")
        p = p._replace(netloc=(f"{p.username}@" if p.username else "") + host)
    url = urlunsplit(p)
    done = subprocess.run([pg_dump, "--format=custom", "--no-owner", "--file", str(partial), "--dbname", url],
                          env=env, capture_output=True, text=True)
    listed = subprocess.run([pg_restore, "--list", str(partial)], capture_output=True, text=True) \
        if done.returncode == 0 else done
    if done.returncode != 0 or listed.returncode != 0 or "TABLE DATA" not in listed.stdout:
        partial.unlink(missing_ok=True)
        raise BackupError("The PostgreSQL copy failed or could not be read back "
                          f"({(listed.stderr or done.stderr).strip()[:300]}); the previous backups are untouched.")
    try:
        with partial.open("rb") as f:
            os.fsync(f.fileno())
    except OSError:
        pass
    os.replace(partial, final)
    copies = sorted((dest / "db").glob("kosha-*.dump"))
    for old in copies[:-KEEP_DB_COPIES]:
        old.unlink()
    return final


def _copy_db(dest: Path) -> Path:
    if config.DATABASE_URL:
        return _copy_db_postgres(dest)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (dest / "db").mkdir(parents=True, exist_ok=True)
    final = dest / "db" / f"kosha-{stamp}.db"
    partial = final.with_suffix(".partial")
    src = sqlite3.connect(str(config.DB_PATH))
    out = sqlite3.connect(str(partial))
    try:
        with out:
            src.backup(out)
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        src.close()
        out.close()
    if ok != "ok":
        partial.unlink(missing_ok=True)
        raise BackupError(f"The database copy failed its integrity check ({ok}); the previous backups are untouched.")
    os.replace(partial, final)
    copies = sorted((dest / "db").glob("kosha-*.db"))
    for old in copies[:-KEEP_DB_COPIES]:
        old.unlink()
    return final


def _copy_blobs(dest: Path) -> int:
    blobs_out = dest / "blobs"
    copied = 0
    for f in config.BLOB_DIR.rglob("*"):
        if not f.is_file():
            continue
        target = blobs_out / f.relative_to(config.BLOB_DIR)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        shutil.copy2(f, partial)
        if _sha256(partial) != f.name:
            partial.unlink(missing_ok=True)
            raise BackupError(f"File {f.name} did not copy correctly. Check the disks; nothing was removed.")
        os.replace(partial, target)
        copied += 1
    return copied


def backup(dest: Path) -> Path:
    dest = Path(dest)
    db_out = _copy_db(dest)
    copied = _copy_blobs(dest)
    secret = config.DATA_DIR / "secret.key"
    if secret.exists():
        shutil.copy2(secret, dest / "secret.key")
    print(f"Database saved and checked: {db_out}; {copied} new file versions copied and checked.")
    return db_out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m kosha.backup <backup folder>")
        sys.exit(2)
    try:
        backup(Path(sys.argv[1]))
    except BackupError as e:
        print(f"BACKUP FAILED: {e}", file=sys.stderr)
        sys.exit(1)
