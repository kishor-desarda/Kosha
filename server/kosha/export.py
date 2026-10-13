# SPDX-License-Identifier: AGPL-3.0-or-later
"""Full data export, so a company can always take all of its data out of Kosha.

    python -m kosha.export <file.zip>            write every table and every stored file
    python -m kosha.export --verify <file.zip>   check an export against its manifest

The ZIP holds every database table as JSON lines, every stored file (all versions of every document, previews,
reports) under its SHA-256 fingerprint, an index that maps each file version to its folder path and name, and a
manifest with row counts and SHA-256 checksums. The format is described in docs/export-format.md. It is safe to
run while Kosha is running: tables are read in one consistent snapshot, and stored files never change.

The export contains everything, including password hashes and add-in settings such as ERP keys: keep it as safe as
the server itself.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, config

FORMAT = "kosha-export"
FORMAT_VERSION = 1
CHUNK = 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
SHA_COLUMNS = (("versions", "blob_sha"), ("versions", "preview_sha"), ("versions", "viewer_sha"),
               ("import_jobs", "report_sha"))

README = """Kosha data export
=================

manifest.json          what is in this file, with row counts and SHA-256 checksums
tables/<table>.jsonl   every database table, one JSON object per row (column name -> value)
files/<ab>/<sha256>    every stored file, named by its SHA-256 fingerprint
files.jsonl            the list of stored files: {"sha256", "size", "path"}
index/versions.jsonl   every file version with its folder path, file name, version and revision

Check it with:  python -m kosha.export --verify <this file>
The format is described in docs/export-format.md in the Kosha source code.
"""


class ExportError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_value(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$base64": base64.b64encode(bytes(v)).decode("ascii")}
    return v


def _line(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class _HashingWriter:
    """Write to a ZIP member while counting lines and computing the SHA-256 of what was written."""

    def __init__(self, fh):
        self.fh, self.h, self.rows = fh, hashlib.sha256(), 0

    def write(self, data: bytes) -> None:
        self.fh.write(data)
        self.h.update(data)
        self.rows += data.count(b"\n")


def _folder_paths(conn) -> dict[int, str]:
    folders = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, parent_id, name FROM folders")}
    out: dict[int, str] = {}

    def path(fid, seen=()):
        if fid in out:
            return out[fid]
        if fid not in folders or fid in seen:
            return ""
        parent, name = folders[fid]
        p = (path(parent, seen + (fid,)) + "/" if parent else "") + name
        out[fid] = p
        return p

    for fid in folders:
        path(fid)
    return out


def _tables(conn) -> list[tuple[str, str]]:
    return [(r[0], r[1] or "") for r in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def export(dest: Path, progress=None) -> dict:
    """Write the export to ``dest`` (a new file). Returns the manifest; ``manifest["problems"]`` lists anything the
    export could not include in full (a missing or damaged stored file)."""
    from . import db
    say = progress or (lambda msg: None)
    dest = Path(dest)
    if dest.exists():
        raise ExportError(f"{dest} already exists. Choose a new file name; Kosha never overwrites an export.")
    if not Path(config.DB_PATH).is_file():
        raise ExportError(f"No Kosha database at {config.DB_PATH}. Set KOSHA_DATA_DIR to the data folder.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    t0 = time.monotonic()
    manifest: dict = {"format": FORMAT, "format_version": FORMAT_VERSION, "created_at": _now(),
                      "kosha_version": __version__, "database": "sqlite", "tables": [], "problems": []}
    referenced: set[str] = set()
    conn = db.connect()
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as zf:
            zf.writestr("README.txt", README)
            conn.execute("BEGIN")             # one consistent snapshot of every table
            try:
                for name, sql in _tables(conn):
                    cols = [dict(name=r[1], type=r[2], notnull=bool(r[3]), pk=bool(r[5]))
                            for r in conn.execute(f'PRAGMA table_info("{name}")')]
                    names = [c["name"] for c in cols]
                    arc = f"tables/{name}.jsonl"
                    with zf.open(arc, "w", force_zip64=True) as fh:
                        w = _HashingWriter(fh)
                        for row in conn.execute(f'SELECT * FROM "{name}"'):
                            w.write(_line({k: _json_value(v) for k, v in zip(names, row)}))
                    manifest["tables"].append({"name": name, "file": arc, "rows": w.rows, "sha256": w.h.hexdigest(),
                                               "columns": cols, "sql": sql})
                    say(f"  table {name}: {w.rows} rows")
                    for table, column in SHA_COLUMNS:
                        if table == name and column in names:
                            referenced.update(r[0] for r in conn.execute(
                                f'SELECT DISTINCT "{column}" FROM "{name}" WHERE "{column}" IS NOT NULL'))
                tables = {t["name"] for t in manifest["tables"]}
                if "schema_migrations" in tables:
                    manifest["schema_migrations"] = [r[0] for r in conn.execute(
                        "SELECT id FROM schema_migrations ORDER BY id")]
                if {"versions", "documents", "folders"} <= tables:
                    paths = _folder_paths(conn)
                    with zf.open("index/versions.jsonl", "w", force_zip64=True) as fh:
                        w = _HashingWriter(fh)
                        for r in conn.execute(
                                "SELECT d.id, d.folder_id, d.filename, d.part_number, d.state, d.deleted, v.version, "
                                "v.revision, v.blob_sha, v.size, v.created_at, v.comment FROM versions v JOIN documents d "
                                "ON d.id = v.document_id ORDER BY d.id, v.version"):
                            folder = paths.get(r[1], "")
                            w.write(_line({"path": f"{folder}/{r[2]}" if folder else r[2], "document_id": r[0],
                                           "filename": r[2], "part_number": r[3], "state": r[4],
                                           "deleted": bool(r[5]), "version": r[6], "revision": r[7],
                                           "sha256": r[8], "size": r[9], "created_at": r[10], "comment": r[11],
                                           "file": f"files/{r[8][:2]}/{r[8]}"}))
                    manifest["index"] = {"file": "index/versions.jsonl", "rows": w.rows, "sha256": w.h.hexdigest()}
            finally:
                conn.execute("COMMIT")
            say("  stored files ...")
            files: list[tuple[str, int]] = []
            present: set[str] = set()
            blob_dir = Path(config.BLOB_DIR)
            for f in sorted(blob_dir.rglob("*")) if blob_dir.is_dir() else []:
                if not f.is_file() or not _SHA.match(f.name):
                    continue
                size = f.stat().st_size
                h = hashlib.sha256()
                with f.open("rb") as src, zf.open(f"files/{f.name[:2]}/{f.name}", "w",
                                                  force_zip64=size > 1024 ** 3) as out:
                    for chunk in iter(lambda: src.read(CHUNK), b""):
                        h.update(chunk)
                        out.write(chunk)
                if h.hexdigest() != f.name:
                    manifest["problems"].append(f"Stored file {f.name} does not match its fingerprint (damaged on "
                                                "disk); it was exported as it is.")
                files.append((f.name, size))
                present.add(f.name)
            for sha in sorted(referenced - present):
                manifest["problems"].append(f"Stored file {sha} is referred to by the database but missing on disk.")
            with zf.open("files.jsonl", "w", force_zip64=True) as fh:
                w = _HashingWriter(fh)
                for sha, size in files:
                    w.write(_line({"sha256": sha, "size": size, "path": f"files/{sha[:2]}/{sha}"}))
            manifest["files"] = {"list": "files.jsonl", "list_sha256": w.h.hexdigest(), "count": len(files),
                                 "bytes": sum(s for _, s in files)}
            manifest["seconds"] = round(time.monotonic() - t0, 1)
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        with partial.open("rb+") as fh:
            os.fsync(fh.fileno())
        os.replace(partial, dest)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        db.close_thread_connection()
    return manifest


def _member_sha(zf: zipfile.ZipFile, name: str) -> tuple[str, int, int]:
    h, size, lines = hashlib.sha256(), 0, 0
    with zf.open(name) as fh:      # reading to the end also checks the ZIP's own CRC
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
            size += len(chunk)
            lines += chunk.count(b"\n")
    return h.hexdigest(), size, lines


def verify(path: Path) -> dict:
    """Check an export: every table and the index against the manifest, every stored file against its fingerprint,
    and every file version in the database present among the files."""
    path = Path(path)
    problems: list[str] = []
    report = {"file": str(path), "tables": 0, "rows": 0, "files": 0, "bytes": 0, "problems": problems, "ok": False}
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        problems.append(f"Not a readable ZIP file: {e}")
        return report
    with zf:
        names = set(zf.namelist())
        try:
            manifest = json.loads(zf.read("manifest.json"))
        except (KeyError, ValueError) as e:
            problems.append(f"manifest.json is missing or unreadable: {e}")
            return report
        if manifest.get("format") != FORMAT or manifest.get("format_version", 0) > FORMAT_VERSION:
            problems.append(f"Not a Kosha export this version can read (format {manifest.get('format')!r}, "
                            f"version {manifest.get('format_version')!r}).")
            return report
        report["created_at"] = manifest.get("created_at")
        report["kosha_version"] = manifest.get("kosha_version")
        version_shas: set[str] = set()
        try:
            for t in manifest.get("tables", []):
                if t["file"] not in names:
                    problems.append(f"Table {t['name']}: {t['file']} is missing.")
                    continue
                sha, _, lines = _member_sha(zf, t["file"])
                if sha != t["sha256"]:
                    problems.append(f"Table {t['name']}: checksum does not match the manifest.")
                elif lines != t["rows"]:
                    problems.append(f"Table {t['name']}: {lines} rows, the manifest says {t['rows']}.")
                report["tables"] += 1
                report["rows"] += lines
                if t["name"] == "versions" and sha == t["sha256"]:
                    with zf.open(t["file"]) as fh:
                        for raw in fh:
                            sha_v = json.loads(raw).get("blob_sha")
                            if sha_v:
                                version_shas.add(sha_v)
            idx = manifest.get("index")
            if idx:
                if idx["file"] not in names:
                    problems.append(f"{idx['file']} is missing.")
                else:
                    sha, _, lines = _member_sha(zf, idx["file"])
                    if sha != idx["sha256"] or lines != idx["rows"]:
                        problems.append(f"{idx['file']} does not match the manifest.")
            fl = manifest.get("files") or {}
            listed: set[str] = set()
            if fl.get("list") not in names:
                problems.append("files.jsonl (the list of stored files) is missing.")
            else:
                sha, _, lines = _member_sha(zf, fl["list"])
                if sha != fl.get("list_sha256") or lines != fl.get("count"):
                    problems.append("files.jsonl does not match the manifest.")
                with zf.open(fl["list"]) as fh:
                    for raw in fh:
                        e = json.loads(raw)
                        listed.add(e["sha256"])
                        if e["path"] not in names:
                            problems.append(f"Stored file {e['sha256']} is listed but not in the export.")
                            continue
                        got, size, _ = _member_sha(zf, e["path"])
                        if got != e["sha256"] or size != e["size"]:
                            problems.append(f"Stored file {e['sha256']} does not match its fingerprint.")
                        report["files"] += 1
                        report["bytes"] += size
            for sha in sorted(version_shas - listed):
                problems.append(f"File version {sha} is in the database but not among the exported files.")
        except (zipfile.BadZipFile, OSError, ValueError, KeyError) as e:
            problems.append(f"The export is damaged: {e}")
        for p in manifest.get("problems", []):
            problems.append(f"Noted when the export was written: {p}")
    report["ok"] = not problems
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m kosha.export",
                                description="Export all Kosha data (every table and every stored file) to a ZIP file.")
    p.add_argument("file", help="the .zip file to write (or to check with --verify)")
    p.add_argument("--verify", action="store_true", help="check an existing export instead of writing one")
    args = p.parse_args(argv)
    if args.verify:
        r = verify(Path(args.file))
        for line in r["problems"][:50]:
            print(f"  {line}", file=sys.stderr)
        if r["ok"]:
            print(f"EXPORT OK: {r['tables']} tables ({r['rows']} rows) and {r['files']} stored files "
                  f"({r['bytes'] / 1e6:.1f} MB) match the manifest.")
            return 0
        print(f"EXPORT DAMAGED: {len(r['problems'])} problem(s) found.", file=sys.stderr)
        return 1
    try:
        m = export(Path(args.file), progress=print)
    except ExportError as e:
        print(f"EXPORT NOT DONE: {e}", file=sys.stderr)
        return 2
    rows = sum(t["rows"] for t in m["tables"])
    print(f"Exported {len(m['tables'])} tables ({rows} rows) and {m['files']['count']} stored files "
          f"({m['files']['bytes'] / 1e6:.1f} MB) to {args.file}.")
    if m["problems"]:
        for line in m["problems"][:50]:
            print(f"  {line}", file=sys.stderr)
        print("EXPORT WRITTEN WITH PROBLEMS: the data on the server is incomplete (see above). Restore the missing "
              "files from a backup and export again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
