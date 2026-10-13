# SPDX-License-Identifier: AGPL-3.0-or-later
"""File safety (specification 8.4 and 16.2): blocked program files, the recycle bin, the two-admin purge and the
clean-up of files that were never fully stored.

* **Blocked types.** Programs and scripts (EXE, DLL, MSI, BAT, CMD, PS1, VBS, JS, SCR ...) are refused on every way
  in. Macro-enabled Office files (XLSM, DOCM) are allowed: calculation sheets need them and the server never runs them.
* **Recycle bin.** A deleted file waits in the bin with the folder it came from. The person who deleted it, its
  creator or an approver can put it back within ``recycle_bin_days`` (30 by default); after that an admin can.
  Nothing leaves the bin by itself.
* **Purge.** Removing a file's content for good needs two admins: one asks, another confirms. The record, its
  history and the audit entries stay; only the stored content goes (when nothing else uses it).
* **Clean-up.** A file whose upload or database transaction never finished is moved out of the store after seven
  days (into ``orphans/``, deleted 30 days later). Nothing that a user was told is safe is ever touched: a file
  counts as used when any ``*_sha`` column of any table names it.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

from . import config, db, store
from .web import bad, conflict, forbidden, not_found

log = logging.getLogger("kosha.filesafety")

BLOCKED_EXT = frozenset({
    "exe", "dll", "msi", "msp", "bat", "cmd", "com", "ps1", "psm1", "psd1", "vbs", "vbe", "js", "jse", "wsf", "wsh",
    "scr", "pif", "cpl", "hta", "jar", "lnk", "reg", "sys", "drv", "ocx", "gadget", "application", "appx", "msix",
    "sh", "run", "app", "deb", "rpm", "apk", "dmg", "pkg", "iso", "inf",
})
ORPHAN_DAYS = 7          # a stored file nothing refers to, after this long, was never committed
ORPHAN_KEEP_DAYS = 30    # moved files are kept this long in orphans/ before they are deleted
TEMP_HOURS = 24          # temporary files of uploads that broke off


def refuse_blocked(filename: str) -> None:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in BLOCKED_EXT:
        raise bad(f"“{filename}” is a program or script (.{ext}). Kosha does not keep programs, so nothing was "
                  "saved. If it is a document, save it in its usual format; to share a tool, use your software "
                  "distribution instead.", "blocked_type")


def _days() -> int:
    try:
        return max(1, int(db.get_setting("recycle_bin_days") or 30))
    except (TypeError, ValueError):
        return 30


def _cutoff(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


# ------------------------------------------------------------- recycle bin --

def folder_text(folder_id: int | None) -> str:
    from .lifecycle import folder_path
    return "/".join(f["name"] for f in folder_path(folder_id)[1:])


def to_bin(user_id: int, doc: dict) -> None:
    """Inside a transaction: put one file into the recycle bin."""
    db.update("documents", doc["id"], {"deleted": 1, "deleted_at": db.now(), "deleted_by": user_id,
                                       "deleted_path": folder_text(doc["folder_id"]), "checked_out_by": None,
                                       "updated_at": db.now()})


def _restorable(user: dict, d: dict) -> str:
    """Why this person may not put the file back ("" when they may)."""
    from .access import has_role
    if d["purged_at"]:
        return "This file was purged; its content is gone. Restore it from a backup if it is needed."
    if has_role(user, "admin"):
        return ""
    if (d["deleted_at"] or "") < _cutoff(_days()):
        return f"Files older than {_days()} days in the recycle bin are put back by an admin."
    if user["id"] in (d["deleted_by"], d["created_by"]) or has_role(user, "approver"):
        return ""
    return "Only the person who deleted it, its creator or an approver can put it back."


def bin_listing(user: dict) -> list[dict]:
    from . import access
    from .access import has_role
    from .lifecycle import user_names
    names = user_names()
    mine = "" if has_role(user, "approver") else " AND (d.deleted_by = ? OR d.created_by = ?)"
    rows = db.rows("SELECT d.id, d.filename, d.part_number, d.description, d.revision, d.latest_version, d.deleted_at, "
                   "d.deleted_by, d.deleted_path, d.created_by, d.purged_at, d.folder_id FROM documents d "
                   "WHERE d.deleted = 1" + mine + access.scope().documents("d") + " ORDER BY d.deleted_at DESC, d.id DESC",
                   () if not mine else (user["id"], user["id"]))
    cutoff = _cutoff(_days())
    waiting = {i for r in db.rows("SELECT document_ids_json FROM purge_requests WHERE status = 'waiting'")
               for i in json.loads(r["document_ids_json"])}
    out = []
    for r in rows:
        why = _restorable(user, r)
        out.append({**r, "deleted_by_name": names.get(r["deleted_by"]), "where": r["deleted_path"] or "",
                    "past_retention": (r["deleted_at"] or "") < cutoff, "can_restore": not why, "why_not": why,
                    "purge_waiting": r["id"] in waiting})
    return out


def restore(user: dict, doc_id: int) -> dict:
    from . import access
    from .lifecycle import ensure_subfolder
    with db.tx():
        d = db.one("SELECT * FROM documents WHERE id = ? AND deleted = 1", (doc_id,))
        if not d:
            raise not_found("This file is not in the recycle bin.")
        access.check_document(d)
        why = _restorable(user, d)
        if why:
            raise forbidden(why)
        folder = d["folder_id"]
        if d["deleted_path"] is not None and folder_text(folder) != d["deleted_path"]:
            # its folder was deleted too: recreate the path under the top folder
            root = db.scalar("SELECT id FROM folders WHERE parent_id IS NULL ORDER BY id LIMIT 1")
            folder = ensure_subfolder(root, d["deleted_path"], user["id"])
        name = d["filename"]
        if db.one("SELECT id FROM documents WHERE folder_id = ? AND filename = ? COLLATE NOCASE AND deleted = 0",
                  (folder, name)):
            raise conflict(f"{name} already exists in that folder. Rename or move that file first, then put this one "
                           "back.", "name_taken")
        upd = {"deleted": 0, "deleted_at": None, "deleted_by": None, "folder_id": folder, "updated_at": db.now()}
        if not d["item_id"] and d["part_number"]:        # its part was removed while it was in the bin (Undo batch)
            from .lifecycle import _link_item
            upd["item_id"] = _link_item(d["part_number"], d["description"], d["doc_type"], user["id"])
        db.update("documents", doc_id, upd)
        db.audit(user["id"], "restore", "document", doc_id, f"{name} to {folder_text(folder) or 'the top folder'}")
    return db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))


def delete_folder(user: dict, folder_id: int, is_approver: bool) -> int:
    """Delete a folder and what is in it: its files go to the recycle bin, the (then empty) folders are removed.
    Refused as a whole when one file may not be deleted. Returns how many files went to the bin."""
    from .lifecycle import folder_or_404
    with db.tx():
        f = folder_or_404(folder_id)
        if not f["parent_id"]:
            raise conflict("The top folder cannot be deleted.", "top_folder")
        ids, todo = [], [folder_id]
        while todo:
            fid = todo.pop()
            ids.append(fid)
            todo += [r["id"] for r in db.rows("SELECT id FROM folders WHERE parent_id = ?", (fid,))]
        marks = ",".join("?" * len(ids))
        docs = db.rows(f"SELECT * FROM documents WHERE folder_id IN ({marks}) AND deleted = 0", ids)
        problems = []
        for d in docs:
            if db.scalar("SELECT 1 FROM lifecycle_events WHERE document_id = ? AND to_state = 'released' LIMIT 1",
                         (d["id"],)):
                problems.append(f"{d['filename']} has been released (make it obsolete instead)")
            elif d["checked_out_by"] and d["checked_out_by"] != user["id"]:
                problems.append(f"{d['filename']} is checked out by someone else")
            elif d["created_by"] != user["id"] and not is_approver:
                problems.append(f"{d['filename']} was added by someone else (an approver can delete it)")
        if problems:
            more = f" and {len(problems) - 5} more" if len(problems) > 5 else ""
            raise conflict("The folder was not deleted: " + "; ".join(problems[:5]) + more + ".", "folder_not_empty",
                           problems=problems)
        for d in docs:
            to_bin(user["id"], d)
            db.audit(user["id"], "delete", "document", d["id"], d["filename"])
        # every file of these folders waits in the bin with its folder path, kept under the top folder meanwhile
        root = db.scalar("SELECT id FROM folders WHERE parent_id IS NULL ORDER BY id LIMIT 1")
        for d in db.rows(f"SELECT id, folder_id, deleted_path FROM documents WHERE folder_id IN ({marks})", ids):
            db.update("documents", d["id"], {"folder_id": root,
                                             "deleted_path": d["deleted_path"] or folder_text(d["folder_id"])})
        for fid in reversed(ids):          # children first
            db.execute("DELETE FROM folders WHERE id = ?", (fid,))
        db.audit(user["id"], "folder_delete", "folder", folder_id,
                 f"{f['name']}: {len(docs)} file{'s' if len(docs) != 1 else ''} to the recycle bin")
    return len(docs)


# -------------------------------------------------------------------- purge --

def purge_requests() -> list[dict]:
    from .lifecycle import user_names
    names = user_names()
    out = []
    for r in db.rows("SELECT * FROM purge_requests ORDER BY id DESC LIMIT 50"):
        ids = json.loads(r.pop("document_ids_json"))
        files = db.rows(f"SELECT id, filename FROM documents WHERE id IN ({','.join('?' * len(ids))})", ids) if ids else []
        out.append({**r, "document_ids": ids, "files": files, "requested_by_name": names.get(r["requested_by"]),
                    "decided_by_name": names.get(r["decided_by"]) if r["decided_by"] else None})
    return out


def ask_purge(admin_id: int, doc_ids: list, note: str) -> dict:
    if not isinstance(doc_ids, list) or not doc_ids or not all(isinstance(i, int) for i in doc_ids):
        raise bad("Choose the files in the recycle bin to purge.")
    note = " ".join(str(note or "").split())[:500]
    if not note:
        raise bad("Say why these files must be removed for good; the reason is kept in the audit log.")
    found = db.rows(f"SELECT id, filename, deleted, purged_at FROM documents WHERE id IN ({','.join('?' * len(doc_ids))})",
                    doc_ids)
    if len(found) != len(set(doc_ids)) or any(not d["deleted"] or d["purged_at"] for d in found):
        raise conflict("Only files in the recycle bin that are not purged yet can be purged.", "not_in_bin")
    with db.tx():
        rid = db.insert("purge_requests", {"document_ids_json": json.dumps(sorted(set(doc_ids))), "note": note,
                                           "requested_by": admin_id, "requested_at": db.now(), "status": "waiting"})
        db.audit(admin_id, "purge_request", "purge", rid, f"{len(found)} files: {note}")
    from . import notify_outbox
    others = [r["id"] for r in db.rows("SELECT id FROM users WHERE active = 1 AND role = 'admin' AND id != ?",
                                       (admin_id,))]
    notify_outbox.enqueue(others, "A purge waits for a second admin",
                          f"{len(found)} file(s) from the recycle bin would be removed for good. Reason: {note}",
                          "#/bin", severity="warning", key=f"purge:{rid}", source=f"purge:{rid}")
    return next(r for r in purge_requests() if r["id"] == rid)


def decide_purge(admin_id: int, request_id: int, confirm: bool) -> dict:
    r = db.one("SELECT * FROM purge_requests WHERE id = ?", (request_id,))
    if not r:
        raise not_found("Purge request not found")
    if r["status"] != "waiting":
        raise conflict("This purge was already decided.", "decided")
    if confirm and r["requested_by"] == admin_id:
        raise forbidden("A second admin confirms a purge; the admin who asked for it cannot.")
    ids = json.loads(r["document_ids_json"])
    with db.tx():
        db.update("purge_requests", request_id, {"status": "done" if confirm else "cancelled", "decided_by": admin_id,
                                                 "decided_at": db.now()})
        if confirm:
            marks = ",".join("?" * len(ids))
            back = db.rows(f"SELECT id, filename FROM documents WHERE id IN ({marks}) AND deleted = 0", ids)
            if back:
                raise conflict(f"{back[0]['filename']} was put back from the recycle bin meanwhile. Ask for a new "
                               "purge without it.", "restored")
            for i in ids:
                db.update("documents", i, {"purged_at": db.now()})
                db.audit(admin_id, "purge", "document", i, f"confirmed request {request_id}")
        else:
            db.audit(admin_id, "purge_cancel", "purge", request_id, "")
    removed = _remove_unused_content(ids) if confirm else 0
    return {**next(x for x in purge_requests() if x["id"] == request_id), "removed_files": removed}


def _sha_columns() -> list[tuple[str, str]]:
    tables = [r["name"] for r in db.rows("SELECT name FROM sqlite_master WHERE type = 'table'")]
    out = []
    for t in tables:
        if t.startswith("sqlite_"):
            continue
        for c in db.rows(f"PRAGMA table_info({t})"):
            if c["name"] == "sha" or c["name"].endswith("_sha"):
                out.append((t, c["name"]))
    return out


def used_shas(skip_documents: list[int] | None = None) -> set[str]:
    """Every stored file some record refers to (the versions of ``skip_documents`` left out)."""
    used: set[str] = set()
    skip = set(skip_documents or [])
    for table, col in _sha_columns():
        if table == "versions" and skip:
            rows = db.rows(f"SELECT document_id, {col} AS s FROM versions WHERE {col} IS NOT NULL")
            used.update(r["s"] for r in rows if r["document_id"] not in skip)
        else:
            used.update(r["s"] for r in db.rows(f"SELECT DISTINCT {col} AS s FROM {table} WHERE {col} IS NOT NULL"))
    return used


def _remove_unused_content(doc_ids: list[int]) -> int:
    marks = ",".join("?" * len(doc_ids))
    mine = set()
    for v in db.rows(f"SELECT blob_sha, preview_sha, viewer_sha FROM versions WHERE document_id IN ({marks})", doc_ids):
        mine.update(s for s in v.values() if s)
    keep = used_shas(skip_documents=doc_ids)
    n = 0
    for sha in mine - keep:
        try:
            store.path_for(sha).unlink(missing_ok=True)
            n += 1
        except (OSError, ValueError):
            log.exception("Could not remove purged file %s", sha)
    return n


# ----------------------------------------------------------------- clean-up --

def cleanup(now: float | None = None) -> dict:
    """Remove what never finished: temporary upload files, abandoned resumable uploads and stored files no record
    refers to. Returns counts for the admin page and the log."""
    now = now or time.time()
    out = {"temp_files": 0, "uploads": 0, "orphans_moved": 0, "orphans_deleted": 0}
    work = config.WORK_DIR
    if work.is_dir():
        for p in work.iterdir():
            if p.is_file() and p.name.startswith(("up_", "put_")) and now - p.stat().st_mtime > TEMP_HOURS * 3600:
                p.unlink(missing_ok=True)
                out["temp_files"] += 1
    from . import resumable
    out["uploads"] = resumable.expire(now)
    orphans = config.DATA_DIR / "orphans"
    if config.BLOB_DIR.is_dir():
        candidates = [p for p in config.BLOB_DIR.glob("*/*")
                      if p.is_file() and now - p.stat().st_mtime > ORPHAN_DAYS * 86400]
        if candidates:
            used = used_shas()
            for p in candidates:
                if p.name in used:
                    continue
                with store.LOCK:        # a check-in of the same content touches the file under this lock
                    try:
                        if time.time() - p.stat().st_mtime <= ORPHAN_DAYS * 86400:
                            continue
                    except FileNotFoundError:
                        continue
                    orphans.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(orphans / p.name))
                os.utime(orphans / p.name, (now, now))
                out["orphans_moved"] += 1
    if orphans.is_dir():
        for p in orphans.iterdir():
            if p.is_file() and now - p.stat().st_mtime > ORPHAN_KEEP_DAYS * 86400:
                p.unlink(missing_ok=True)
                out["orphans_deleted"] += 1
    if out["orphans_moved"]:
        db.audit(None, "cleanup", "store", None, f"{out['orphans_moved']} stored files no record refers to moved to "
                                                  "orphans/")
    return out
