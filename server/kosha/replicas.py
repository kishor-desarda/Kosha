# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copies of the stored files in storage add-ins (kind ``storage``): another disk, a NAS or an object store.

Kosha's own file store stays the master copy. Every hour the background worker copies the files that a storage
add-in does not have yet (a limited amount per run, oldest first), and records each confirmed copy in
``storage_copies``. When a stored file is missing or damaged, ``fetch`` gets it back from a copy, but only after
checking that its SHA-256 fingerprint still matches; a copy that does not match is never used.

Every call goes through the add-in guard (time-out, failure count, automatic switch-off). A failed copy is tried
again on the next runs, up to MAX_TRIES, and the admin sees why it failed.
"""
from __future__ import annotations

import hashlib
import logging
import threading

from . import db, store

log = logging.getLogger("kosha.replicas")

KIND = "storage"
RUN_FILES = 500
RUN_BYTES = 2 * 1024 ** 3
MAX_TRIES = 5
_busy = threading.Lock()


def _providers() -> list:
    from .addins import registry
    return registry.by_kind(KIND)


def copy_due(max_files: int = RUN_FILES, max_bytes: int = RUN_BYTES) -> dict:
    """Copy the stored files each storage add-in does not have yet. Never raises."""
    from kosha_sdk import AddinError

    from .addins import registry
    from .health import stored_files
    if not _busy.acquire(blocking=False):
        return {"busy": True}
    try:
        out = {"at": db.now(), "copied": 0, "failed": 0, "providers": []}
        provs = _providers()
        if not provs:
            return out
        files = stored_files()
        for a in provs:
            done = {r["sha"]: r for r in db.rows("SELECT sha, status, tries FROM storage_copies WHERE addin_id = ?",
                                                 (a.id,))}
            todo = [s for s in sorted(files) if s not in done or (done[s]["status"] != "copied"
                                                                   and done[s]["tries"] < MAX_TRIES)]
            n = sent = failed = 0
            for sha in todo:
                if n >= max_files or sent >= max_bytes:
                    break
                n += 1
                try:
                    data = store.read_bytes(sha)
                    if hashlib.sha256(data).hexdigest() != sha:
                        raise ValueError("the stored file does not match its fingerprint; the health check repairs it")
                    registry.call(a.id, "put", sha, data, timeout=300)
                    ok, err = True, ""
                    sent += len(data)
                except (AddinError, OSError, ValueError) as e:
                    ok, err = False, str(e)
                    failed += 1
                db.execute("INSERT INTO storage_copies(sha, addin_id, status, tries, error, size, copied_at) "
                           "VALUES (?, ?, ?, 1, ?, ?, ?) ON CONFLICT(sha, addin_id) DO UPDATE SET "
                           "status = excluded.status, tries = storage_copies.tries + 1, error = excluded.error, "
                           "size = excluded.size, copied_at = COALESCE(excluded.copied_at, storage_copies.copied_at)",
                           (sha, a.id, "copied" if ok else "failed", err[:500], files.get(sha, 0),
                            db.now() if ok else None))
                if not ok and "switched off" in err:
                    break           # the guard switched the add-in off: stop asking it
            out["copied"] += n - failed
            out["failed"] += failed
            out["providers"].append({"id": a.id, "name": a.manifest.name, "copied": n - failed, "failed": failed,
                                     "waiting": max(0, len(todo) - n)})
        db.set_setting("storage_copies_last", out)
        return out
    except Exception:
        log.exception("Copying to storage add-ins failed")
        return {"at": db.now(), "error": "see the server log"}
    finally:
        _busy.release()


def fetch(sha: str) -> bytes | None:
    """A good copy of a stored file from a storage add-in, or None. The fingerprint is checked first."""
    from kosha_sdk import AddinError

    from .addins import registry
    have = {r["addin_id"] for r in db.rows("SELECT addin_id FROM storage_copies WHERE sha = ? AND status = 'copied'",
                                           (sha,))}
    for a in sorted(_providers(), key=lambda x: x.id not in have):
        try:
            data = registry.call(a.id, "get", sha, timeout=300)
        except AddinError as e:
            log.info("No copy of %s in %s: %s", sha[:12], a.id, e)
            continue
        if isinstance(data, (bytes, bytearray)) and hashlib.sha256(data).hexdigest() == sha:
            return bytes(data)
        log.warning("The copy of %s in %s does not match its fingerprint; not used", sha[:12], a.id)
    return None


def restore_missing(limit: int = 200) -> dict:
    """Bring back stored files that the records use but the store no longer has, from storage add-in copies."""
    if not _providers():
        return {"missing": 0, "restored": 0, "not_restored": []}
    shas = set()
    for col in ("blob_sha", "preview_sha", "viewer_sha"):
        shas |= {r["s"] for r in db.rows(f"SELECT DISTINCT {col} AS s FROM versions WHERE {col} IS NOT NULL")}
    missing = [s for s in sorted(shas) if len(s or "") == 64 and not store.exists(s)][:limit]
    restored, failed = [], []
    for sha in missing:
        data = fetch(sha)
        if data is None:
            failed.append(sha)
            continue
        got, _ = store.put_bytes(data)
        (restored if got == sha else failed).append(sha)
    for sha in restored:
        db.audit(None, "file_restored", "blob", None, f"{sha} from a storage add-in copy")
    return {"missing": len(missing), "restored": len(restored), "not_restored": failed[:50]}


def status() -> dict:
    """Per storage add-in: how many stored files have a confirmed copy, how many failed, and the last run."""
    from .health import stored_files
    total = len(stored_files())
    provs = []
    for a in _providers():
        c = {r["status"]: r["n"] for r in db.rows("SELECT status, COUNT(*) AS n FROM storage_copies WHERE addin_id = ? "
                                                    "GROUP BY status", (a.id,))}
        last_err = db.one("SELECT error, sha FROM storage_copies WHERE addin_id = ? AND status = 'failed' "
                          "ORDER BY tries DESC LIMIT 1", (a.id,))
        provs.append({"id": a.id, "name": a.manifest.name, "copied": c.get("copied", 0), "failed": c.get("failed", 0),
                      "missing": max(0, total - c.get("copied", 0)), "last_error": (last_err or {}).get("error", "")})
    return {"files": total, "providers": provs, "last": db.get_setting("storage_copies_last") or {}}
