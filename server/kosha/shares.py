# SPDX-License-Identifier: AGPL-3.0-or-later
"""Share links for people outside the company (specification 15.3).

A share link gives a customer or supplier some **released** files without a Kosha account. Each link names who it is
for, ends on a date (14 days by default, at most 90), can need a password, can be view-only (preview, no download),
and can be withdrawn at once. The files are the revisions released when the link was made; when a newer revision is
released later, the page says so. Every PDF opened through a link carries a watermark with the recipient's name and
the date. Every share, opening and download is in the audit log.

The link's secret is shown once (``#/share/<token>``); Kosha keeps only its SHA-256 fingerprint. The part after ``#``
never reaches the server's access log.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, timedelta, timezone

import jwt

from . import config, db, security, store
from .web import ApiError, bad, conflict, forbidden, not_found

DEFAULT_DAYS = 14
MAX_DAYS = 90
KEY_HOURS = 8


def _hash(token: str) -> str:
    return hashlib.sha256(("kosha-share|" + token).encode()).hexdigest()


def _clean(body: dict) -> dict:
    who = " ".join(str(body.get("recipient") or "").split())[:120]
    if not who:
        raise bad("Say who the link is for (a person and their company), so the watermark and the record name them.")
    try:
        days = int(body.get("days") or DEFAULT_DAYS)
    except (TypeError, ValueError):
        raise bad("Give the number of days the link works.") from None
    if not 1 <= days <= MAX_DAYS:
        raise bad(f"A share link works for 1 to {MAX_DAYS} days.")
    pw = str(body.get("password") or "")
    if pw and len(pw) < 8:
        raise bad("A link password needs at least 8 characters. Send it to the recipient another way than the link.")
    return {"recipient": who, "note": " ".join(str(body.get("note") or "").split())[:500],
            "expires_on": (date.today() + timedelta(days=days)).isoformat(),
            "password_hash": security.hash_password(pw) if pw else None, "view_only": 1 if body.get("view_only") else 0}


def create(user: dict, body: dict) -> dict:
    from . import access, lifecycle
    if user.get("kind") == "vendor":
        raise forbidden("Vendor accounts cannot share files further.")
    ids = body.get("document_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids) or len(ids) > 200:
        raise bad("Choose the released files to share (at most 200).")
    files = []
    for i in dict.fromkeys(ids):
        d = lifecycle.doc_or_404(i)
        if d["state"] not in ("released", "spares_only"):
            raise conflict(f"{d['filename']} is not released. Only released files are shared outside the company.",
                           "not_released")
        files.append({"document_id": d["id"], "version": d["latest_version"], "revision": d["revision"]})
    access.check_download()
    vals = _clean(body)
    token = secrets.token_urlsafe(24)
    with db.tx():
        sid = db.insert("share_links", {**vals, "token_hash": _hash(token), "token_prefix": token[:6],
                                        "files_json": json.dumps(files), "created_by": user["id"],
                                        "created_at": db.now(), "opened_count": 0, "download_count": 0})
        db.audit(user["id"], "share_create", "share", sid,
                 f"{len(files)} file(s) for {vals['recipient']} until {vals['expires_on']}"
                 + (", view only" if vals["view_only"] else "") + (", password" if vals["password_hash"] else ""))
    return {**listing(user, sid)[0], "token": token}


def listing(user: dict, only: int | None = None) -> list[dict]:
    from .access import has_role
    from .lifecycle import user_names
    names = user_names()
    where, params = [], []
    if only:
        where.append("id = ?")
        params.append(only)
    if not has_role(user, "admin"):
        where.append("created_by = ?")
        params.append(user["id"])
    rows = db.rows("SELECT * FROM share_links" + (" WHERE " + " AND ".join(where) if where else "") +
                   " ORDER BY id DESC LIMIT 300", params)
    today = date.today().isoformat()
    out = []
    for r in rows:
        files = json.loads(r.pop("files_json"))
        r.pop("token_hash", None)
        has_pw = bool(r.pop("password_hash", None))
        names_of = {d["id"]: d["filename"] for d in db.rows(
            f"SELECT id, filename FROM documents WHERE id IN ({','.join('?' * len(files))})",
            [f["document_id"] for f in files])} if files else {}
        state = "withdrawn" if r["withdrawn_at"] else "expired" if r["expires_on"] < today else "active"
        out.append({**r, "files": [{**f, "filename": names_of.get(f["document_id"], "?")} for f in files],
                    "password": has_pw, "state": state, "created_by_name": names.get(r["created_by"]),
                    "withdrawn_by_name": names.get(r["withdrawn_by"]) if r["withdrawn_by"] else None})
    return out


def withdraw(user: dict, share_id: int) -> dict:
    from .access import has_role
    r = db.one("SELECT * FROM share_links WHERE id = ?", (share_id,))
    if not r:
        raise not_found("Share link not found")
    if r["created_by"] != user["id"] and not has_role(user, "admin"):
        raise forbidden("Only the person who made the link or an admin can withdraw it.")
    if not r["withdrawn_at"]:
        with db.tx():
            db.update("share_links", share_id, {"withdrawn_at": db.now(), "withdrawn_by": user["id"]})
            db.audit(user["id"], "share_withdraw", "share", share_id, r["recipient"])
    return listing(user, share_id)[0]


# ------------------------------------------------------------ outside view --

def _live(token: str) -> dict:
    r = db.one("SELECT * FROM share_links WHERE token_hash = ?", (_hash(str(token or "")),))
    if not r:
        raise not_found("This link does not exist. Check that it was copied whole.")
    if r["withdrawn_at"]:
        raise ApiError(410, "This link was withdrawn by the sender. Ask them for the files again if you still need "
                            "them.", "share_withdrawn")
    if r["expires_on"] < date.today().isoformat():
        raise ApiError(410, f"This link ended on {r['expires_on']}. Ask the sender for a new one.", "share_expired")
    return r


def _key_ok(r: dict, key: str | None) -> None:
    if not r["password_hash"]:
        return
    try:
        k = jwt.decode(str(key or ""), config.secret_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ApiError(401, "Type the password the sender gave you.", "share_password") from None
    if k.get("purpose") != "share" or k.get("sid") != r["id"]:
        raise ApiError(401, "Type the password the sender gave you.", "share_password")


def unlock(token: str, password: str, ip: str) -> dict:
    r = _live(token)
    if not r["password_hash"]:
        return {"key": ""}
    from .signin import _clean_name, _locked_until, _paused, record_failure
    name = f"share:{r['id']}"
    until = _locked_until(_clean_name(name), ip)
    if until:
        raise _paused(until)
    if not security.verify_password(str(password or "")[:256], r["password_hash"]):
        paused = record_failure(name, ip, None, f"wrong password for share link {r['id']}")
        if paused:
            raise paused
        raise bad("That password is not right.", "bad_password")
    exp = datetime.now(timezone.utc) + timedelta(hours=KEY_HOURS)
    return {"key": jwt.encode({"purpose": "share", "sid": r["id"], "exp": exp}, config.secret_key(), algorithm="HS256")}


def view(token: str, key: str | None) -> dict:
    """What the recipient sees: who shared what, until when, and the files."""
    r = _live(token)
    if r["password_hash"] and not key:
        return {"password": True, "recipient": r["recipient"]}
    _key_ok(r, key)
    sender = db.one("SELECT full_name FROM users WHERE id = ?", (r["created_by"],))
    company = (db.get_setting("company_name") or "").strip()
    files = []
    for f in json.loads(r["files_json"]):
        d = db.one("SELECT id, filename, part_number, description, revision, state, ext, deleted FROM documents "
                   "WHERE id = ?", (f["document_id"],))
        v = db.one("SELECT size, preview_kind FROM versions WHERE document_id = ? AND version = ?",
                   (f["document_id"], f["version"]))
        if not d or not v or d["deleted"]:
            continue
        outs = db.rows("SELECT id, filename, size FROM release_outputs WHERE document_id = ? AND revision = ? AND "
                       "kind = 'released_pdf' AND status = 'ok' ORDER BY version DESC LIMIT 1",
                       (d["id"], f["revision"]))
        files.append({"id": d["id"], "filename": d["filename"], "part_number": d["part_number"],
                      "description": d["description"], "revision": f["revision"], "size": v["size"],
                      "preview": bool(v["preview_kind"]), "outputs": outs,
                      "superseded": d["revision"] != f["revision"] or d["state"] == "obsolete"})
    with db.tx():
        db.execute("UPDATE share_links SET opened_count = opened_count + 1, last_opened_at = ? WHERE id = ?",
                   (db.now(), r["id"]))
        db.audit(None, "share_open", "share", r["id"], r["recipient"])
    return {"password": False, "recipient": r["recipient"], "from": sender["full_name"] if sender else "",
            "company": company, "note": r["note"], "expires_on": r["expires_on"], "view_only": bool(r["view_only"]),
            "files": files}


def _file(r: dict, doc_id: int) -> tuple[dict, dict]:
    f = next((f for f in json.loads(r["files_json"]) if f["document_id"] == doc_id), None)
    if not f:
        raise not_found("This file is not part of the link.")
    d = db.one("SELECT * FROM documents WHERE id = ? AND deleted = 0", (doc_id,))
    v = db.one("SELECT * FROM versions WHERE document_id = ? AND version = ?", (doc_id, f["version"]))
    if not d or not v:
        raise not_found("This file is no longer available. Ask the sender.")
    return d, v


def output(token: str, key: str | None, output_id: int) -> tuple[bytes, str]:
    """The PDF stamped at release of a shared file's revision, as the recipient downloads it."""
    from . import outputs
    r = _live(token)
    _key_ok(r, key)
    o = db.one("SELECT * FROM release_outputs WHERE id = ? AND kind = 'released_pdf' AND status = 'ok'", (output_id,))
    f = next((f for f in json.loads(r["files_json"]) if o and f["document_id"] == o["document_id"]), None)
    if not o or not f or f["revision"] != o["revision"]:
        raise not_found("This file is not part of the link.")
    if r["view_only"]:
        raise forbidden("This link is for viewing only; the sender did not allow downloads.")
    if not store.exists(o["blob_sha"]):
        raise not_found("The stored file is missing on the server. Ask the sender.")
    data = store.read_bytes(o["blob_sha"])
    data = watermark_line(data, f"Uncontrolled when printed — {date.today():%d-%b-%Y}", r["recipient"])
    d = db.one("SELECT * FROM documents WHERE id = ?", (o["document_id"],))
    newer = outputs.superseded_by(d, o["revision"]) if d else None
    if newer:
        from . import watermark
        data = watermark.superseded_band(data, "Superseded — ask for the new drawing") or data
    with db.tx():
        db.execute("UPDATE share_links SET download_count = download_count + 1 WHERE id = ?", (r["id"],))
        db.audit(None, "share_download", "share", r["id"], f"{o['filename']} (stamped PDF)")
    return data, o["filename"]


def watermark_line(data: bytes, printed: str, recipient: str) -> bytes:
    from . import watermark
    data = watermark.printed_line(data, printed) or data
    return watermark.stamp_pdf(data, watermark.line_for(recipient)) or data


def content(token: str, key: str | None, doc_id: int, kind: str) -> tuple[bytes, str, str]:
    """(bytes, media type, file name) of a shared file's preview or the file itself (PDFs watermarked)."""
    from . import watermark
    r = _live(token)
    _key_ok(r, key)
    d, v = _file(r, doc_id)
    if kind == "preview":
        if not v["preview_sha"] or not store.exists(v["preview_sha"]):
            raise not_found("No preview for this file")
        data = store.read_bytes(v["preview_sha"])
        kind_ext = v["preview_kind"]
        if kind_ext == "pdf":
            data = watermark.stamp_pdf(data, watermark.line_for(r["recipient"])) or data
        db.audit(None, "share_view", "share", r["id"], f"{d['filename']} rev {d['revision']}")
        return data, kind_ext, f"{d['filename']}.{kind_ext}"
    if r["view_only"]:
        raise forbidden("This link is for viewing only; the sender did not allow downloads.")
    if not store.exists(v["blob_sha"]):
        raise not_found("The stored file is missing on the server. Ask the sender.")
    data = store.read_bytes(v["blob_sha"])
    marked = ""
    if d["ext"] == "pdf":
        stamped = watermark.stamp_pdf(data, watermark.line_for(r["recipient"]))
        data, marked = (stamped, ", watermarked") if stamped else (data, ", not watermarked")
    with db.tx():
        db.execute("UPDATE share_links SET download_count = download_count + 1 WHERE id = ?", (r["id"],))
        db.audit(None, "share_download", "share", r["id"], f"{d['filename']} v{v['version']}{marked}")
    return data, "pdf" if d["ext"] == "pdf" else "", d["filename"]
