# SPDX-License-Identifier: AGPL-3.0-or-later
"""Restore a backup into a new, empty data folder, and prove that it is complete.

    python -m kosha.restore <backup folder> <new data folder> [--db kosha-20261013-013000.db]

Takes the newest database copy in ``<backup folder>/db`` (or the one named with ``--db``), the file store and the
secret key, and writes them into the new folder. It refuses a folder that is not empty, so nothing is ever
overwritten. Then it checks the restored database with SQLite's integrity check and reads every file that the
database refers to, checking each against its SHA-256 fingerprint. Exit code 0 means the restore is complete.

To run Kosha on the restored data, stop Kosha, set KOSHA_DATA_DIR to the new folder and start it again.
``python -m kosha.backup --verify <backup folder>`` runs the same restore into a temporary folder as a test.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

CHUNK = 1024 * 1024
# Columns that hold a file-store fingerprint. ``versions.blob_sha`` are the file versions themselves.
SHA_COLUMNS = (("versions", "blob_sha", "file version"), ("versions", "preview_sha", "preview"),
               ("versions", "viewer_sha", "viewer file"), ("import_jobs", "report_sha", "import report"))


class RestoreError(RuntimeError):
    """The restore can't be done (nothing usable was written, or the target was not touched)."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as f:
        os.fsync(f.fileno())


def _fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def database_copies(backup: Path) -> list[Path]:
    return sorted((Path(backup) / "db").glob("kosha-*.db"))


def choose_db(backup: Path, db_name: str | None = None) -> Path:
    backup = Path(backup)
    if not backup.is_dir():
        raise RestoreError(f"The backup folder {backup} does not exist.")
    if db_name:
        p = Path(db_name)
        cand = p if p.is_absolute() or p.exists() else backup / "db" / p.name
        if not cand.is_file():
            raise RestoreError(f"The database copy {db_name} was not found in {backup / 'db'}.")
        return cand
    copies = database_copies(backup)
    if not copies:
        raise RestoreError(f"{backup} holds no database copy (db/kosha-*.db). Is it a Kosha backup folder?")
    return copies[-1]


def _blob_files(backup: Path) -> list[Path]:
    root = Path(backup) / "blobs"
    if not root.is_dir():
        return []
    return [f for f in root.rglob("*") if f.is_file() and not f.name.endswith(".partial")]


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    shutil.copyfile(src, partial)
    _fsync_file(partial)
    os.replace(partial, dest)


def referenced(db_path: Path) -> dict[str, str]:
    """Every fingerprint the database refers to, with what it is (file versions win over previews)."""
    con = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        out: dict[str, str] = {}
        for table, column, what in reversed(SHA_COLUMNS):
            if table not in tables:
                continue
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                continue
            for (sha,) in con.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"):
                out[str(sha)] = what
        return out
    finally:
        con.close()


def verify(data_dir: Path) -> dict:
    """Check a data folder: SQLite integrity, and every referenced file present with the right fingerprint."""
    data_dir = Path(data_dir)
    db_path = data_dir / "kosha.db"
    result = {"integrity": "", "files_checked": 0, "file_versions": 0, "missing": [], "damaged": [], "ok": False}
    if not db_path.is_file():
        result["integrity"] = "no database"
        return result
    try:
        con = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = con.execute("PRAGMA integrity_check").fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        result["integrity"] = f"unreadable: {e}"
        return result
    result["integrity"] = "; ".join(str(r[0]) for r in rows[:5])
    if result["integrity"] != "ok":
        return result
    refs = referenced(db_path)
    result["file_versions"] = sum(1 for w in refs.values() if w == "file version")
    for sha, what in sorted(refs.items()):
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            result["damaged"].append({"sha256": sha, "what": what, "why": "not a SHA-256 fingerprint"})
            continue
        p = data_dir / "blobs" / sha[:2] / sha
        result["files_checked"] += 1
        if not p.is_file():
            result["missing"].append({"sha256": sha, "what": what})
        elif _sha256(p) != sha:
            result["damaged"].append({"sha256": sha, "what": what, "why": "content does not match its fingerprint"})
    result["ok"] = not result["missing"] and not result["damaged"]
    return result


def restore(backup: Path, target: Path, db_name: str | None = None, progress=None) -> dict:
    """Restore into an empty folder and verify it. Returns a report; raises RestoreError when it can't start."""
    t0 = time.monotonic()
    backup, target = Path(backup).resolve(), Path(target).resolve()
    db_copy = choose_db(backup, db_name)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise RestoreError(f"{target} is not an empty folder. Restore only into a new or empty folder, so nothing "
                           "is overwritten.")
    if target == backup or backup in target.parents:
        raise RestoreError("Restore into a folder outside the backup folder.")
    blobs = _blob_files(backup)
    need = db_copy.stat().st_size + sum(f.stat().st_size for f in blobs)
    target.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target).free
    if free < need * 1.05 + 50 * 1024 * 1024:
        raise RestoreError(f"Not enough free space in {target}: the restore needs {need / 1e9:.1f} GB and "
                           f"{free / 1e9:.1f} GB are free. Choose a folder on a bigger disk.")
    say = progress or (lambda msg: None)
    say(f"Restoring database copy {db_copy.name} ...")
    _copy(db_copy, target / "kosha.db")
    say(f"Restoring {len(blobs)} stored files ...")
    root = backup / "blobs"
    for f in blobs:
        _copy(f, target / "blobs" / f.relative_to(root))
    for d in {(target / "blobs" / f.relative_to(root)).parent for f in blobs}:
        _fsync_dir(d)
    if (backup / "secret.key").is_file():
        _copy(backup / "secret.key", target / "secret.key")
        try:
            os.chmod(target / "secret.key", 0o600)
        except OSError:
            pass
    _fsync_dir(target)
    say("Checking the restored database and every stored file ...")
    report = verify(target)
    report.update({"backup_folder": str(backup), "database_copy": db_copy.name, "target": str(target),
                   "files_restored": len(blobs), "bytes_restored": need,
                   "seconds": round(time.monotonic() - t0, 1)})
    report["message"] = summary(report)
    return report


def summary(r: dict) -> str:
    if r.get("integrity") != "ok":
        return f"The restored database failed SQLite's integrity check ({r.get('integrity')})."
    if r.get("ok"):
        return (f"Restore complete and checked: database {r.get('database_copy')} passed the integrity check, and all "
                f"{r.get('files_checked')} stored files ({r.get('file_versions')} file versions) are present and "
                "match their fingerprints.")
    return (f"Restore incomplete: {len(r.get('missing', []))} stored files are missing and {len(r.get('damaged', []))} "
            f"do not match their fingerprints, out of {r.get('files_checked')} the database refers to.")


def print_report(r: dict, out=None) -> None:
    out = out or sys.stdout
    print(r["message"], file=out)
    for m in r.get("missing", [])[:20]:
        print(f"  missing {m['what']}: {m['sha256']}", file=out)
    for m in r.get("damaged", [])[:20]:
        print(f"  damaged {m['what']}: {m['sha256']} ({m['why']})", file=out)
    extra = len(r.get("missing", [])) + len(r.get("damaged", [])) - 40
    if extra > 0:
        print(f"  ... and {extra} more", file=out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m kosha.restore",
                                description="Restore a Kosha backup into a new, empty data folder and check it.")
    p.add_argument("backup", help="backup folder written by python -m kosha.backup")
    p.add_argument("target", help="new, empty data folder")
    p.add_argument("--db", help="database copy to restore (file name in <backup>/db); default: the newest")
    args = p.parse_args(argv)
    try:
        r = restore(Path(args.backup), Path(args.target), args.db, progress=print)
    except RestoreError as e:
        print(f"RESTORE NOT DONE: {e}", file=sys.stderr)
        return 2
    print_report(r)
    if r["ok"]:
        print(f"To use it: stop Kosha, set KOSHA_DATA_DIR={r['target']} and start Kosha again.")
        return 0
    print("The restored folder was kept for inspection. Try an older database copy with --db, or another backup.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
