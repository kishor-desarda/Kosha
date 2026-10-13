# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folders and documents: the Explorer side of Kosha."""
from __future__ import annotations

import json
import re
import urllib.parse

from starlette.responses import Response, StreamingResponse

from .. import access, boms, db, lifecycle, store
from ..addins import registry
from ..cad import process as cadproc
from ..web import ROLE_LEVEL, Req, api, as_bool, bad, conflict, not_found
from .core import DOC_SELECT, _with_preview

MIME = {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
        "bmp": "image/bmp", "webp": "image/webp", "pdf": "application/pdf", "stl": "model/stl"}

# Stored files never run as part of Kosha; Kosha's own pages may show them in a frame (the PDF viewer).
SAFE_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                                           "frame-ancestors 'self'"}


# ---------------------------------------------------------------- folders --

@api("GET", "/api/folders")
def folders(req: Req):
    sc = access.scope()
    rows = db.rows("SELECT f.id, f.parent_id, f.name, (SELECT COUNT(*) FROM documents d WHERE d.folder_id = f.id "
                   "AND d.deleted = 0" + sc.documents() + ") AS files FROM folders f WHERE 1 = 1" + sc.folders() +
                   " ORDER BY f.name COLLATE NOCASE")
    return {"folders": rows}


@api("POST", "/api/folders", role="engineer")
def create_folder(req: Req):
    name = (req.b("name") or "").strip()
    parent = req.b("parent_id")
    if not name or any(c in name for c in '\\/:*?"<>|'):
        raise bad("Folder names can't be empty or use \\ / : * ? \" < > |")
    if parent:
        lifecycle.folder_or_404(int(parent))
    if db.one("SELECT id FROM folders WHERE IFNULL(parent_id,0) = ? AND name = ? COLLATE NOCASE", (int(parent or 0), name)):
        raise conflict("A folder with that name already exists here.")
    fid = db.insert("folders", {"parent_id": int(parent) if parent else None, "name": name, "created_by": req.uid,
                                "created_at": db.now()})
    db.audit(req.uid, "folder_create", "folder", fid, name)
    return {"folder": db.one("SELECT * FROM folders WHERE id = ?", (fid,))}


@api("PATCH", "/api/folders/{id:int}", role="engineer")
def update_folder(req: Req):
    fid = req.int_param("id")
    f = lifecycle.folder_or_404(fid)
    upd = {}
    if req.b("name"):
        name = req.b("name").strip()
        if any(c in name for c in '\\/:*?"<>|'):
            raise bad("Folder names can't use \\ / : * ? \" < > |")
        upd["name"] = name
    if req.b("parent_id", "unset") != "unset":
        parent = req.b("parent_id")
        parent = int(parent) if parent else None
        chain = [x["id"] for x in lifecycle.folder_path(parent)] if parent else []
        if fid in chain:
            raise bad("A folder can't be moved inside itself.")
        upd["parent_id"] = parent
    try:
        db.update("folders", fid, upd)
    except Exception:
        raise conflict("A folder with that name already exists there.")
    db.audit(req.uid, "folder_update", "folder", fid, f["name"])
    return {"folder": db.one("SELECT * FROM folders WHERE id = ?", (fid,))}


@api("DELETE", "/api/folders/{id:int}", role="engineer")
def delete_folder(req: Req):
    """Delete a folder: its files go to the recycle bin (refused as a whole when one of them may not be deleted)."""
    from .. import filesafety
    return {"ok": True, "files_to_bin": filesafety.delete_folder(req.user, req.int_param("id"), req.has("approver"))}


@api("GET", "/api/folders/{id:int}/contents")
def folder_contents(req: Req):
    fid = req.int_param("id")
    f = lifecycle.folder_or_404(fid)
    sc = access.scope()
    subs = db.rows("SELECT f.id, f.name, f.created_at, (SELECT COUNT(*) FROM documents d WHERE d.folder_id = f.id AND "
                   "d.deleted = 0" + sc.documents() + ") AS files FROM folders f WHERE f.parent_id = ?" + sc.folders() +
                   " ORDER BY f.name COLLATE NOCASE", (fid,))
    docs = db.rows(DOC_SELECT + "WHERE d.folder_id = ? AND d.deleted = 0" + access.scope().documents() +
                   " ORDER BY d.filename COLLATE NOCASE", (fid,))
    names = lifecycle.user_names()
    out = _with_preview(docs, names)
    std = {r["id"]: r["part_number"] for r in db.rows(
        "SELECT a.id, b.part_number FROM items a JOIN items b ON b.id = a.standard_item_id")}
    for d in out:
        if d["item_id"] in std:
            d["standard_part"] = std[d["item_id"]]
    return {"folder": f, "path": lifecycle.folder_path(fid), "folders": subs, "documents": out}


@api("GET", "/api/documents")
def list_documents(req: Req):
    where = ["d.deleted = 0" + access.scope().documents()]
    params: list = []
    if req.q("checked_out") == "me":
        where.append("d.checked_out_by = ?")
        params.append(req.uid)
    if req.q("state"):
        where.append("d.state = ?")
        params.append(req.q("state"))
    if req.q("type"):
        where.append("d.doc_type = ?")
        params.append(req.q("type"))
    if req.q("item_id"):
        where.append("d.item_id = ?")
        params.append(int(req.q("item_id")))
    limit = min(int(req.q("limit", 200)), 1000)
    docs = db.rows(DOC_SELECT + "WHERE " + " AND ".join(where) + " ORDER BY d.updated_at DESC LIMIT ?", [*params, limit])
    return {"documents": _with_preview(docs, lifecycle.user_names())}


# -------------------------------------------------------------- documents --

def actions_for(d: dict, user: dict) -> dict:
    lvl = ROLE_LEVEL[user["role"]]
    mine = d["checked_out_by"] == user["id"]
    other = bool(d["checked_out_by"]) and not mine
    st = d["state"]
    nxt = lifecycle.next_revision(d["revision"])

    def a(ok, why=""):
        return {"ok": bool(ok), "why": "" if ok else why}

    need_eng = "You have view-only access." if lvl < 1 else ""
    eco_req = db.get_setting("require_eco_for_release")
    from .. import release_rules
    not_ready = release_rules.review_problems(d) if st == "in_work" and lvl >= 1 else []
    own = release_rules.release_problems(user, d) if st in ("in_work", "in_review") and lvl >= ROLE_LEVEL["approver"] else []
    # a reason can accept what release_problems found when the settings allow it (self-approval, open duplicates)
    reason_helps = bool(own) and (db.get_setting("allow_self_approval_with_reason") or "duplicates" in " ".join(own)) \
        and not any("someone else must approve" in p for p in own)
    return {
        "checkout": a(lvl >= 1 and not d["checked_out_by"] and st == "in_work",
                      need_eng or ("Checked out by someone else." if other else
                                   "Already checked out to you." if mine else
                                   f"Released revisions are locked. Press Revise to start revision {nxt}." if st == "released" else
                                   "Waiting for review; send it back to In work first." if st == "in_review" else
                                   "Obsolete files can't be changed.")),
        "checkin": a(mine, "Check the file out first." if not other else "Checked out by someone else."),
        "undo_checkout": a(mine or (d["checked_out_by"] and lvl >= ROLE_LEVEL["admin"]), "It isn't checked out to you."),
        "submit": a(lvl >= 1 and st == "in_work" and not d["checked_out_by"] and not not_ready,
                    need_eng or ("Check it in first." if d["checked_out_by"] else
                                 " ".join(not_ready) if not_ready else "Only files in work can be submitted.")),
        "send_back": a(st == "in_review" and lvl >= 1, "Only files in review can be sent back."),
        "release": a(lvl >= ROLE_LEVEL["approver"] and st in ("in_work", "in_review") and not d["checked_out_by"]
                     and not eco_req and (not own or reason_helps) and not (st == "in_work" and not_ready),
                     "Releasing needs the approver role." if lvl < ROLE_LEVEL["approver"] else
                     "Check it in first." if d["checked_out_by"] else
                     "Release through a change order." if eco_req else
                     " ".join(not_ready) if st == "in_work" and not_ready else
                     " ".join(own) if own else "Already released or obsolete."),
        "release_needs_reason": reason_helps, "release_why": " ".join(own),
        "revise": a(lvl >= 1 and st == "released", need_eng or "Only released files are revised; this one can be edited directly."),
        "obsolete": a(lvl >= ROLE_LEVEL["approver"] and st in lifecycle.LIVE_STATES
                      and not db.get_setting("obsolete_needs_change_order"),
                      "Needs the approver role and a released file." if lvl < ROLE_LEVEL["approver"] or
                      st not in lifecycle.LIVE_STATES else "Make it obsolete through a change order."),
        "delete": a(lvl >= 1 and not other and not db.scalar(
            "SELECT 1 FROM lifecycle_events WHERE document_id = ? AND to_state = 'released'", (d["id"],)),
            "Released files can't be deleted; make them obsolete." if lvl >= 1 else need_eng),
        "edit": a(mine, "Check the file out to edit its properties."),
        "generate_bom": a(lvl >= 1 and d["doc_type"] == "assembly", "Only assemblies have a BOM."),
        "next_revision": nxt,
    }


@api("GET", "/api/documents/{id:int}")
def document(req: Req):
    did = req.int_param("id")
    d = lifecycle.doc_or_404(did)
    names = lifecycle.user_names()
    out = lifecycle.doc_summary(d, names)
    versions = db.rows("SELECT id, version, revision, size, comment, created_by, created_at, preview_kind, viewer_kind, "
                       "meta_json, blob_sha FROM versions WHERE document_id = ? ORDER BY version DESC", (did,))
    # which released revisions are superseded: everything released before the last released revision
    released = [r["revision"] for r in db.rows("SELECT revision FROM lifecycle_events WHERE document_id = ? AND "
                                               "to_state = 'released' ORDER BY id", (did,))]
    current = d["revision"] if d["state"] in lifecycle.LIVE_STATES else (released[-1] if released else None)
    for v in versions:
        v["superseded_by"] = current if current and v["revision"] != current and v["revision"] in released else None
        v["meta"] = json.loads(v.pop("meta_json") or "{}")
        v["by"] = names.get(v["created_by"], "?")
        v["sha"] = v.pop("blob_sha")[:12]
    events = db.rows("SELECT e.*, c.number AS change_number FROM lifecycle_events e LEFT JOIN change_orders c "
                     "ON c.id = e.change_order_id WHERE e.document_id = ? ORDER BY e.id DESC", (did,))
    for e in events:
        e["by"] = names.get(e["user_id"], "?")
    item = None
    related = []
    where_used = []
    bom = None
    if d["item_id"]:
        item = db.one("SELECT * FROM items WHERE id = ?", (d["item_id"],))
        if item:
            item["props"] = json.loads(item.pop("props_json") or "{}")
            if item["standard_item_id"]:
                item["standard_part"] = db.one("SELECT id, part_number, description FROM items WHERE id = ?",
                                               (item["standard_item_id"],))
        related = _with_preview(db.rows(DOC_SELECT + "WHERE d.item_id = ? AND d.id != ? AND d.deleted = 0" +
                                        access.scope().documents(),
                                        (d["item_id"], did)), names)
        where_used = boms.where_used(d["item_id"])
        b = boms.bom_for_item(d["item_id"], d["revision"])
        if b:
            bom = db.one("SELECT id, revision, state, source, updated_at, (SELECT COUNT(*) FROM bom_lines WHERE bom_id = "
                         "boms.id) AS lines FROM boms WHERE id = ?", (b["id"],))
    # sent back from review by someone else: their comment is shown on top of the file
    returned = None
    last = events[0] if events else None
    if last and d["state"] == "in_work" and last["from_state"] == "in_review" and last["to_state"] == "in_work":
        submitter = next((e["user_id"] for e in events if e["to_state"] == "in_review"), None)
        if last["user_id"] != submitter:
            returned = {"by": last["by"], "at": last["at"], "comment": last["comment"]}
    replaced_by = None
    if d["item_id"]:
        replaced_by = db.one("SELECT r.id, r.part_number, r.description FROM items i JOIN items r ON "
                             "r.id = i.replaced_by_item_id WHERE i.id = ?", (d["item_id"],))
    cos = db.rows("SELECT c.id, c.number, c.title, c.state, ci.action FROM co_items ci JOIN change_orders c ON "
                  "c.id = ci.co_id WHERE ci.document_id = ? ORDER BY c.id DESC", (did,))
    latest = versions[0] if versions else None
    out.update({
        "path": lifecycle.folder_path(d["folder_id"]), "versions": versions, "events": events, "item": item,
        "related": related, "where_used": where_used, "bom": bom, "changes": cos,
        "actions": actions_for(d, req.user), "preview_kind": latest["preview_kind"] if latest else None,
        "viewer_kind": latest["viewer_kind"] if latest else None, "meta": latest["meta"] if latest else {},
        "returned": returned, "replaced_by": replaced_by,
    })
    return out


def _upload_props(req: Req) -> dict:
    raw = req.b("props")
    props = {}
    if raw:
        try:
            props = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except ValueError:
            raise bad("props must be JSON")
    for k in ("part_number", "description", "material"):
        if req.b(k):
            props[k] = req.b(k)
    return props


@api("POST", "/api/documents/upload", role="engineer")
def upload(req: Req):
    """Add files to a folder (new files, or new versions of files you have checked out)."""
    folder_id = req.b("folder_id")
    if not folder_id:
        raise bad("Choose a folder")
    files = req.files.get("file") or req.files.get("files") or []
    if not files:
        raise bad("No file was sent")
    results, errors = [], []
    keep = as_bool(req.b("keep_checked_out"))
    comment = req.b("comment") or ""
    props = _upload_props(req) if len(files) == 1 else {}
    names = lifecycle.user_names()
    try:
        paths = json.loads(req.b("paths") or "[]")
    except ValueError:
        raise bad("paths must be a JSON list")
    folder_cache: dict[str, int] = {}
    for idx, f in enumerate(files):
        try:
            fid = int(folder_id)
            rel = (paths[idx] if idx < len(paths) else "") or ""
            if rel:
                if rel not in folder_cache:
                    folder_cache[rel] = lifecycle.ensure_subfolder(fid, rel, req.uid)
                fid = folder_cache[rel]
            d = lifecycle.checkin(req.user, fid, f.filename, f.file, comment, keep, props)
            s = lifecycle.doc_summary(d, names)
            s["unchanged"] = d.get("unchanged", False)
            results.append(s)
        except Exception as e:  # report per file
            msg = getattr(e, "message", str(e))
            errors.append({"filename": f.filename, "error": msg, "code": getattr(e, "code", "error")})
    return {"documents": results, "errors": errors}


@api("POST", "/api/documents/{id:int}/checkin", role="engineer")
def checkin(req: Req):
    did = req.int_param("id")
    files = req.files.get("file") or []
    keep = as_bool(req.b("keep_checked_out"))
    if not files:
        d = lifecycle.doc_or_404(did)
        if d["checked_out_by"] != req.uid:
            raise conflict("Check the file out first.")
        # no new file: just release the lock
        return lifecycle.doc_summary(lifecycle.undo_checkout(req.user, did), lifecycle.user_names())
    f = files[0]
    d = lifecycle.checkin(req.user, 0 or lifecycle.doc_or_404(did)["folder_id"], f.filename, f.file,
                          req.b("comment") or "", keep, _upload_props(req), document_id=did)
    registry.emit("file.checked_in", {"document_id": did, "item_id": d.get("item_id"), "filename": d["filename"],
                                      "revision": d["revision"], "version": d.get("latest_version"), "user_id": req.uid})
    return lifecycle.doc_summary(d, lifecycle.user_names())


@api("POST", "/api/documents/{id:int}/checkout", role="engineer")
def checkout(req: Req):
    d = lifecycle.checkout(req.user, req.int_param("id"))
    registry.emit("file.checked_out", {"document_id": d["id"], "item_id": d.get("item_id"), "filename": d["filename"],
                                       "user_id": req.uid})
    return lifecycle.doc_summary(d, lifecycle.user_names())


@api("POST", "/api/documents/{id:int}/undo-checkout", role="engineer")
def undo(req: Req):
    return lifecycle.doc_summary(lifecycle.undo_checkout(req.user, req.int_param("id"), req.has("admin")),
                                 lifecycle.user_names())


@api("POST", "/api/documents/{id:int}/state", role="engineer")
def state(req: Req):
    d = lifecycle.set_state(req.user, req.int_param("id"), req.b("to") or "", req.b("comment") or "",
                            reason=req.b("reason") or "", replacement=req.b("replacement") or "")
    return lifecycle.doc_summary(d, lifecycle.user_names())


@api("POST", "/api/documents/{id:int}/revise", role="engineer")
def revise(req: Req):
    d = lifecycle.revise(req.user, req.int_param("id"), req.b("comment") or "")
    return lifecycle.doc_summary(d, lifecycle.user_names())


@api("PATCH", "/api/documents/{id:int}", role="engineer")
def update_document(req: Req):
    did = req.int_param("id")
    d = lifecycle.doc_or_404(did)
    from .. import apisafety
    apisafety.guard(req, "documents", did, d["filename"])
    body = req.body if isinstance(req.body, dict) else {}
    upd: dict = {}
    if "folder_id" in body:
        lifecycle.folder_or_404(int(body["folder_id"]))
        if d["checked_out_by"] and d["checked_out_by"] != req.uid:
            raise conflict("Someone else has this file checked out.")
        upd["folder_id"] = int(body["folder_id"])
    prop_keys = [k for k in ("part_number", "description", "filename") if k in body]
    if prop_keys:
        if d["checked_out_by"] != req.uid and not req.has("admin"):
            raise conflict("Check the file out to change its properties.", "not_checked_out")
        if "filename" in body:
            new = lifecycle.clean_filename(body["filename"])
            if cadproc.ext_of(new) != d["ext"]:
                raise bad("Keep the same file extension when renaming.")
            upd["filename"] = new
        if "description" in body:
            upd["description"] = (body["description"] or "").strip()
        if "part_number" in body:
            pn = (body["part_number"] or "").strip().upper()
            upd["part_number"] = pn
            with db.tx():
                upd["item_id"] = lifecycle._link_item(pn, upd.get("description", d["description"]), d["doc_type"],
                                                      req.uid) if pn else None
    if not upd:
        return lifecycle.doc_summary(d, lifecycle.user_names())
    upd["updated_at"] = db.now()
    try:
        db.update("documents", did, upd)
    except Exception:
        raise conflict("A file with that name already exists in the destination folder.")
    if "description" in upd and d["item_id"]:
        db.execute("UPDATE items SET description = ?, updated_at = ? WHERE id = ? AND state != 'released'",
                   (upd["description"], db.now(), upd.get("item_id", d["item_id"])))
    apisafety.bump("documents", did, req.uid)
    db.audit(req.uid, "document_update", "document", did, ",".join(k for k in upd if k != "updated_at"))
    return lifecycle.doc_summary(lifecycle.doc_or_404(did), lifecycle.user_names())


@api("DELETE", "/api/documents/{id:int}", role="engineer")
def delete_document(req: Req):
    lifecycle.delete(req.user, req.int_param("id"), req.has("approver"))
    return {"ok": True}


def _version(did: int, req: Req) -> dict:
    d = lifecycle.doc_or_404(did)
    v = req.q("version")
    ver = db.one("SELECT * FROM versions WHERE document_id = ? AND version = ?", (did, int(v) if v else d["latest_version"]))
    if not ver:
        raise not_found("Version not found")
    return {**ver, "filename": d["filename"], "doc": d}


def _disposition(kind: str, filename: str) -> str:
    quoted = urllib.parse.quote(filename)
    ascii_name = filename.encode("ascii", "replace").decode().replace('"', "")
    return f'{kind}; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'


@api("GET", "/api/documents/{id:int}/file")
def download(req: Req):
    v = _version(req.int_param("id"), req)
    access.check_download()
    if v["doc"].get("purged_at"):
        raise not_found("This file was purged by two admins; its content is no longer kept.")
    if not store.exists(v["blob_sha"]):
        raise not_found("The stored file is missing on the server.")
    path = store.path_for(v["blob_sha"])
    from .. import watermark
    if v["doc"]["ext"] == "pdf" and v["doc"]["state"] in watermark.STATES and watermark.enabled():
        stamped = watermark.stamp_pdf(path.read_bytes(), watermark.line_for(req.user["full_name"]))
        db.audit(req.uid, "download", "document", v["document_id"],
                 f"v{v['version']}" + (", watermarked" if stamped else ", not watermarked (the PDF could not be stamped)"))
        return Response(stamped or path.read_bytes(), media_type="application/pdf", headers={
            "Content-Disposition": _disposition("attachment", v["filename"]), **SAFE_HEADERS})
    size = path.stat().st_size
    start, end = _byte_range(req.request.headers.get("range", ""), size)
    if start is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    if start == 0:          # a resumed download is one download
        db.audit(req.uid, "download", "document", v["document_id"], f"v{v['version']}")
    headers = {"Content-Disposition": _disposition("attachment", v["filename"]), "Accept-Ranges": "bytes",
               "Content-Length": str(end - start + 1), "ETag": f'"{v["blob_sha"][:32]}"',
               "Content-Encoding": "identity", **SAFE_HEADERS}
    partial = (start, end) != (0, size - 1)
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(_read_range(path, start, end), status_code=206 if partial else 200,
                             media_type="application/octet-stream", headers=headers)


def _byte_range(header: str, size: int) -> tuple[int | None, int]:
    """(first, last) byte to send for a Range header (the whole file without one); (None, 0) when unsatisfiable."""
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header or "")
    if not m or (not m.group(1) and not m.group(2)) or size == 0:
        return 0, size - 1
    if not m.group(1):                  # the last N bytes
        n = int(m.group(2))
        return (max(0, size - n), size - 1) if n else (None, 0)
    start = int(m.group(1))
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if start >= size or end < start:
        return None, 0
    return start, end


def _read_range(path, start: int, end: int):
    with open(path, "rb") as f:
        f.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = f.read(min(1024 * 1024, left))
            if not chunk:
                break
            left -= len(chunk)
            yield chunk


def _blob_response(sha: str | None, kind: str | None, filename: str) -> Response:
    if not sha or not kind or not store.exists(sha):
        raise not_found("No preview for this file")
    return Response(store.read_bytes(sha), media_type=MIME.get(kind, "application/octet-stream"),
                    headers={"Cache-Control": "private, max-age=604800", "ETag": sha[:32],
                             "Content-Disposition": _disposition("inline", filename + "." + kind), **SAFE_HEADERS})


@api("GET", "/api/documents/{id:int}/preview")
def preview(req: Req):
    v = _version(req.int_param("id"), req)
    return _blob_response(v["preview_sha"], v["preview_kind"], v["filename"])


@api("GET", "/api/documents/{id:int}/viewer")
def viewer(req: Req):
    v = _version(req.int_param("id"), req)
    return _blob_response(v["viewer_sha"], v["viewer_kind"], v["filename"])


@api("POST", "/api/documents/{id:int}/reprocess", role="engineer")
def reprocess(req: Req):
    """Regenerate preview/signature (e.g. after installing the DWG converter)."""
    v = _version(req.int_param("id"), req)
    data = store.read_bytes(v["blob_sha"])
    r = cadproc.process(v["filename"], data)
    upd = {"meta_json": json.dumps({**json.loads(v["meta_json"] or "{}"), **r["meta"]})}
    if r["preview"]:
        upd["preview_kind"] = r["preview"][0]
        upd["preview_sha"] = store.put_bytes(r["preview"][1])[0]
    if r["viewer"]:
        upd["viewer_kind"] = r["viewer"][0]
        upd["viewer_sha"] = store.put_bytes(r["viewer"][1])[0]
    if r["signature"]:
        upd["signature_json"] = json.dumps(r["signature"])
    db.update("versions", v["id"], upd)
    return {"ok": True, "preview": bool(r["preview"]), "note": r["meta"].get("preview_note", "")}


@api("POST", "/api/documents/reprocess-all", role="admin")
def reprocess_all(req: Req):
    import threading

    def work():
        for row in db.rows("SELECT v.id, v.blob_sha, v.meta_json, d.filename FROM versions v JOIN documents d ON "
                           "d.id = v.document_id AND v.version = d.latest_version WHERE d.deleted = 0 AND "
                           "(v.preview_sha IS NULL OR d.ext IN ('dwg','dxf'))"):
            try:
                r = cadproc.process(row["filename"], store.read_bytes(row["blob_sha"]))
                upd = {"meta_json": json.dumps({**json.loads(row["meta_json"] or "{}"), **r["meta"]})}
                if r["preview"]:
                    upd.update(preview_kind=r["preview"][0], preview_sha=store.put_bytes(r["preview"][1])[0])
                if r["viewer"]:
                    upd.update(viewer_kind=r["viewer"][0], viewer_sha=store.put_bytes(r["viewer"][1])[0])
                if r["signature"]:
                    upd["signature_json"] = json.dumps(r["signature"])
                db.update("versions", row["id"], upd)
            except Exception:
                continue
        db.close_thread_connection()

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}
