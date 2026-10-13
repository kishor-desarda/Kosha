# SPDX-License-Identifier: AGPL-3.0-or-later
"""Online backup:  python -m kosha.backup /srv/backups/kosha
Restore test:   python -m kosha.backup --verify /srv/backups/kosha [--work /srv/restore-test]

Copies the database with SQLite's backup API (safe while Kosha is running), checks the
copy with SQLite's integrity check, and mirrors the content-addressed file store. Stored
files never change, so only new ones are copied, and every copied file is checked against
its fingerprint before it counts. The last 14 database copies are kept.

``--verify`` restores the backup into a temporary folder (see ``kosha.restore``), checks the database and every
stored file, reports, and removes the temporary folder. The result of the last backup and of the last restore
test is recorded in Kosha's settings and shown under Admin › Backups.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config

log = logging.getLogger("kosha.backup")
LAST_BACKUP = "backup_last"
LAST_RESTORE_TEST = "restore_test_last"

KEEP_DB_COPIES = 14


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_db(dest: Path) -> Path:
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


def record(key: str, result: dict) -> bool:
    """Store a result in the live Kosha database (shown under Admin › Backups). Never fails the caller."""
    from . import db
    try:
        if not Path(config.DB_PATH).is_file():     # no Kosha database here (e.g. checking a backup elsewhere)
            return False
        db.close_thread_connection()
        db.set_setting(key, result)
        return True
    except Exception as e:  # a missing table or a busy database must not turn a good backup into a failure
        log.warning("Could not record %s in the database: %s", key, e)
        return False
    finally:
        db.close_thread_connection()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def backup(dest: Path) -> Path:
    dest = Path(dest)
    t0 = time.monotonic()
    try:
        db_out = _copy_db(dest)
        copied = _copy_blobs(dest)
        secret = config.DATA_DIR / "secret.key"
        if secret.exists():
            shutil.copy2(secret, dest / "secret.key")
    except Exception as e:
        record(LAST_BACKUP, {"at": _now(), "ok": False, "folder": str(dest.resolve()), "message": str(e)})
        raise
    message = f"Database saved and checked: {db_out}; {copied} new file versions copied and checked."
    record(LAST_BACKUP, {"at": _now(), "ok": True, "folder": str(dest.resolve()), "database_copy": db_out.name,
                         "new_files": copied, "seconds": round(time.monotonic() - t0, 1), "message": message})
    print(message)
    return db_out


def verify_backup(dest: Path, work: Path | None = None, db_name: str | None = None, progress=None) -> dict:
    """Restore test: restore into a temporary folder, check everything, remove the folder, record the result."""
    from . import restore
    dest = Path(dest)
    if work is not None:
        Path(work).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="kosha-restore-test-", dir=str(work) if work else None))
    try:
        report = restore.restore(dest, tmp / "data", db_name, progress=progress)
    except restore.RestoreError as e:
        report = {"ok": False, "message": f"Restore test not done: {e}", "backup_folder": str(dest.resolve())}
    except Exception as e:  # report every failure; the restore test must never pass silently
        report = {"ok": False, "message": f"Restore test failed: {e}", "backup_folder": str(dest.resolve())}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    result = {"at": _now(), "ok": bool(report.get("ok")), "backup_folder": report.get("backup_folder", str(dest)),
              "database_copy": report.get("database_copy", ""), "integrity": report.get("integrity", ""),
              "files_checked": report.get("files_checked", 0), "file_versions": report.get("file_versions", 0),
              "missing": len(report.get("missing", [])), "damaged": len(report.get("damaged", [])),
              "seconds": report.get("seconds", 0), "message": report["message"]}
    record(LAST_RESTORE_TEST, result)
    report["recorded"] = result
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m kosha.backup", description="Back up Kosha, or test a restore.")
    p.add_argument("folder", help="backup folder")
    p.add_argument("--verify", action="store_true", help="restore the backup into a temporary folder and check it")
    p.add_argument("--work", help="where the temporary restore goes (needs as much free space as the backup)")
    p.add_argument("--db", help="with --verify: database copy to test (file name in <folder>/db); default: newest")
    args = p.parse_args(argv)
    if args.verify:
        from . import restore
        r = verify_backup(Path(args.folder), Path(args.work) if args.work else None, args.db, progress=print)
        if "integrity" in r:
            restore.print_report(r)
        else:
            print(r["message"], file=sys.stderr)
        print("RESTORE TEST PASSED" if r["ok"] else "RESTORE TEST FAILED", file=sys.stdout if r["ok"] else sys.stderr)
        return 0 if r["ok"] else 1
    try:
        backup(Path(args.folder))
    except (BackupError, OSError, sqlite3.Error) as e:
        print(f"BACKUP FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
