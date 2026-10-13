# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resumable uploads (specification 8.4 and 13.5).

A large file arrives in pieces, so a dropped connection costs one piece, not the whole file:

1. ``POST /api/uploads`` with the file's name, size and the SHA-256 of the whole file: Kosha answers with an upload id.
2. ``PUT /api/uploads/{id}?offset=N`` with each piece (raw bytes) in order. A piece sent again after a break is
   accepted where it fits; ``GET /api/uploads/{id}`` says how much has arrived, so the client continues from there.
3. ``POST /api/uploads/{id}/finish`` with what an ordinary upload takes (folder, comment, or the file to check in
   over). Kosha checks size and fingerprint before anything is stored: a damaged transfer is never kept as a good
   file. Then the virus scan, the store and the check-in run as for any upload.

Pieces wait in the data folder's ``work/uploads``; an upload left unfinished for seven days is removed.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from datetime import datetime, timezone

from . import config, db, filesafety
from .uploads import clean_filename
from .web import bad, conflict, not_found

PIECE_MAX = 16 * 1024 * 1024        # what one PUT may carry (the client sends 8 MB)
PIECE = 8 * 1024 * 1024
KEEP_DAYS = 7


def _dir():
    d = config.WORK_DIR / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _part(uid: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", uid):
        raise not_found("Upload not found")
    return _dir() / f"{uid}.part"


def _row(user: dict, uid: str) -> dict:
    r = db.one("SELECT * FROM upload_sessions WHERE id = ?", (uid,))
    if not r or r["user_id"] != user["id"]:
        raise not_found("Upload not found. It may have been finished, or removed after seven days; start it again.")
    return r


def _status(r: dict) -> dict:
    return {"id": r["id"], "filename": r["filename"], "size": r["size"], "received": r["received"],
            "status": r["status"], "piece_size": PIECE, "document_id": r["document_id"]}


def start(user: dict, filename: str, size, sha256: str) -> dict:
    try:
        filename = clean_filename(filename)
    except ValueError as e:
        raise bad(str(e)) from None
    filesafety.refuse_blocked(filename)
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise bad("Send the file's size in bytes.") from None
    limit = int(config.MAX_UPLOAD_MB * 1024 * 1024)
    if size <= 0:
        raise bad("The file is empty.")
    if size > limit:
        raise bad(f"{filename} ({size / 1048576:.0f} MB) is larger than the {config.MAX_UPLOAD_MB:g} MB this server "
                  "accepts, so nothing was saved. Ask your admin to raise the limit (KOSHA_MAX_UPLOAD_MB).", "too_large")
    sha256 = str(sha256 or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise bad("Send the SHA-256 fingerprint of the whole file (64 hexadecimal characters).")
    uid = secrets.token_urlsafe(18)
    _part(uid).write_bytes(b"")
    with db.tx():
        db.insert("upload_sessions", {"id": uid, "user_id": user["id"], "filename": filename, "size": size,
                                      "sha256": sha256, "received": 0, "status": "open", "created_at": db.now(),
                                      "updated_at": db.now()})
    return _status(db.one("SELECT * FROM upload_sessions WHERE id = ?", (uid,)))


def status(user: dict, uid: str) -> dict:
    return _status(_row(user, uid))


def put_piece(user: dict, uid: str, offset, data: bytes) -> dict:
    r = _row(user, uid)
    if r["status"] != "open":
        raise conflict("This upload is already finished.", "finished")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise bad("Send the piece as raw bytes (Content-Type: application/octet-stream).")
    if len(data) > PIECE_MAX:
        raise bad(f"Send pieces of at most {PIECE_MAX // 1048576} MB.")
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        raise bad("Say where the piece goes: ?offset=<bytes already sent>.") from None
    if offset > r["received"] or offset < 0:
        raise conflict(f"Kosha has {r['received']} bytes of this file; continue from there.", "wrong_offset",
                       received=r["received"])
    end = offset + len(data)
    if end > r["size"]:
        raise bad("The pieces are longer than the size given when the upload started.")
    if end <= r["received"]:            # sent again after a break: already here
        return _status(r)
    p = _part(uid)
    with open(p, "r+b") as f:
        f.truncate(offset)
        f.seek(offset)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    db.execute("UPDATE upload_sessions SET received = ?, updated_at = ? WHERE id = ?", (end, db.now(), uid))
    return _status({**r, "received": end})


def finish(user: dict, uid: str, body: dict) -> dict:
    """Check the whole file, then add it (``folder_id``, optional ``path``) or check it in over ``document_id``.
    Returns the document row."""
    from . import lifecycle, scan
    r = _row(user, uid)
    if r["status"] == "done":
        return lifecycle.doc_or_404(r["document_id"])   # finish sent twice after a break
    p = _part(uid)
    if r["received"] != r["size"] or p.stat().st_size != r["size"]:
        raise conflict(f"Only {r['received']} of {r['size']} bytes have arrived. Send the rest, then finish.",
                       "incomplete", received=r["received"])
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != r["sha256"]:
        with db.tx():
            db.execute("UPDATE upload_sessions SET status = 'damaged', updated_at = ? WHERE id = ?", (db.now(), uid))
        p.unlink(missing_ok=True)
        raise conflict(f"{r['filename']} arrived damaged (its fingerprint does not match), so Kosha did not keep it. "
                       "Nothing was changed; upload the file again.", "fingerprint_mismatch")
    if scan.configured():
        from .uploads import UploadRefused, scan_files

        class _F:       # the shape scan_files expects
            def __init__(self, fh):
                self.filename, self.file = r["filename"], fh
        with open(p, "rb") as f:
            try:
                scan_files({"file": [_F(f)]}, user["id"], f"/api/uploads/{uid}")
            except UploadRefused as e:
                from .web import ApiError
                raise ApiError(e.status, e.message, e.code, e.extra) from None
    props = {k: body.get(k) for k in ("part_number", "description", "material") if body.get(k)}
    keep = bool(body.get("keep_checked_out"))
    comment = str(body.get("comment") or "")
    with open(p, "rb") as f:
        if body.get("document_id"):
            did = int(body["document_id"])
            d = lifecycle.checkin(user, lifecycle.doc_or_404(did)["folder_id"], r["filename"], f, comment, keep,
                                  props, document_id=did)
        else:
            if not body.get("folder_id"):
                raise bad("Choose a folder")
            fid = int(body["folder_id"])
            if body.get("path"):
                fid = lifecycle.ensure_subfolder(fid, str(body["path"]), user["id"])
            d = lifecycle.checkin(user, fid, r["filename"], f, comment, keep, props)
    with db.tx():
        db.execute("UPDATE upload_sessions SET status = 'done', document_id = ?, updated_at = ? WHERE id = ?",
                   (d["id"], db.now(), uid))
    p.unlink(missing_ok=True)
    return d


def cancel(user: dict, uid: str) -> None:
    r = _row(user, uid)
    if r["status"] == "open":
        db.execute("UPDATE upload_sessions SET status = 'cancelled', updated_at = ? WHERE id = ?", (db.now(), uid))
    _part(uid).unlink(missing_ok=True)


def expire(now: float | None = None) -> int:
    """Remove the pieces of uploads nobody touched for seven days."""
    now = now or time.time()
    cutoff = datetime.fromtimestamp(now - KEEP_DAYS * 86400, timezone.utc).replace(microsecond=0).isoformat()
    rows = db.rows("SELECT id FROM upload_sessions WHERE status = 'open' AND updated_at < ?", (cutoff,))
    for r in rows:
        db.execute("UPDATE upload_sessions SET status = 'expired', updated_at = ? WHERE id = ?", (db.now(), r["id"]))
        _part(r["id"]).unlink(missing_ok=True)
    # pieces whose upload row is gone (a crash between writing and recording)
    known = {r["id"] for r in db.rows("SELECT id FROM upload_sessions WHERE status = 'open'")}
    for p in _dir().glob("*.part"):
        if p.stem not in known and now - p.stat().st_mtime > KEEP_DAYS * 86400:
            p.unlink(missing_ok=True)
    return len(rows)
