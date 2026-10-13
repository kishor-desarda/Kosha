# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inputs library API: reference material per part or per project.

Every handler goes through ``InputLibrary``, which filters restricted inputs in SQL, so a restricted input
answers "not found" to people without access, everywhere: lists, search, counts, files and thumbnails.
"""
from __future__ import annotations

from starlette.responses import Response

from .. import db, store
from ..inputs import INLINE_MIME, Upload, library
from ..web import ApiError, Req, api, as_bool, bad, not_found
from .files import SAFE_HEADERS, _disposition


def _data(req: Req) -> dict:
    """The request fields as a dict, from JSON or from a multipart form."""
    if isinstance(req.body, dict):
        return dict(req.body)
    if req.form is not None:
        return {k: v for k, v in req.form.items() if isinstance(v, str)}
    return {}


def _stored_uploads(req: Req) -> tuple[list[dict], list[dict]]:
    """Write every uploaded file to the store. Returns (stored files, per-file errors)."""
    stored, errors = [], []
    for f in (req.files.get("file") or []) + (req.files.get("files") or []):
        try:
            stored.append(library.store_upload(Upload(f.filename or "file", f.file)))
        except Exception as e:
            errors.append({"filename": f.filename, "error": getattr(e, "message", str(e))})
    return stored, errors


@api("GET", "/api/inputs")
def list_inputs(req: Req):
    """Search and filter: q, kind, type, tag (comma-separated lists), project, item, by (user id), from, to (dates),
    restricted=1, deleted=1, sort (relevance, date, added, title, kind), dir (asc, desc), limit, offset, facets=0."""
    library.worker.start()           # picks up anything left waiting after a restart
    return library.search(req.user, req.query)


@api("POST", "/api/inputs", role="engineer")
def create_inputs(req: Req):
    """Create inputs. With several files each becomes its own input (named after the file), or all of them
    one input when ``group=1``. Without files: one input with a web link or a note. Identical files are
    stored once; ``duplicates`` lists other inputs (that you can see) holding the same file, and
    ``skip_duplicates=1`` leaves out files that are already in the library."""
    data = _data(req)
    sent = bool(req.files.get("file") or req.files.get("files"))
    stored, errors = _stored_uploads(req)
    if not sent:
        return {"inputs": _summaries([library.create(req.user, data, [])]), "errors": [], "duplicates": []}
    if not stored:
        return {"inputs": [], "errors": errors, "duplicates": []}
    if as_bool(data.get("group")) or len(stored) == 1:
        groups = [stored]
    else:
        groups = [[f] for f in stored]
        data = {k: v for k, v in data.items() if k != "title"}     # each input is named after its file
    skip_known = as_bool(data.get("skip_duplicates"))
    created: list[int] = []
    duplicates: list[dict] = []
    first_error: ApiError | None = None
    for files in groups:
        dups = {f["sha"]: library.duplicates_of(req.user, f["sha"]) for f in files}
        if skip_known and all(dups[f["sha"]] for f in files):
            duplicates += [{"filename": f["filename"], "inputs": dups[f["sha"]], "skipped": True} for f in files]
            continue
        try:
            created.append(library.create(req.user, data, files))
        except ApiError as e:
            first_error = first_error or e
            errors += [{"filename": f["filename"], "error": e.message} for f in files]
            continue
        duplicates += [{"filename": f["filename"], "inputs": dups[f["sha"]], "skipped": False}
                       for f in files if dups[f["sha"]]]
    if first_error and not created and not duplicates:
        raise first_error                    # nothing was added: say why, as a normal error
    if created:
        library.worker.wake()                # read the text after this answer, never before it
    return {"inputs": _summaries(created), "errors": errors, "duplicates": duplicates}


def _summaries(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    return library._summaries(db.rows(f"SELECT * FROM inputs WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id", ids))


@api("GET", "/api/inputs/settings")
def get_settings(req: Req):
    return library.public_settings()


@api("PUT", "/api/inputs/settings", role="admin")
def put_settings(req: Req):
    return library.save_settings(req.user, _data(req))


@api("POST", "/api/inputs/reindex", role="admin")
def reindex(req: Req):
    """Rebuild the search index; with reread=unread also read again the files whose text was not read."""
    out = library.reindex_all()
    mode = _data(req).get("reread") or ""
    if mode in ("unread", "all"):
        out["queued"] = library.reread(req.user, only_unread=mode == "unread")
        library.worker.wake()
    return out


@api("POST", "/api/inputs/bulk", role="engineer")
def bulk(req: Req):
    data = _data(req)
    try:
        ids = [int(x) for x in data.get("ids") or []]
    except (TypeError, ValueError):
        raise bad("ids must be a list of input numbers.")
    return library.bulk(req.user, ids, str(data.get("action") or ""), data)


@api("GET", "/api/inputs/{id:int}")
def get_input(req: Req):
    return library.detail(req.user, req.int_param("id"))


@api("PATCH", "/api/inputs/{id:int}", role="engineer")
def update_input(req: Req):
    iid = req.int_param("id")
    library.update(req.user, iid, _data(req))
    return library.detail(req.user, iid)


@api("DELETE", "/api/inputs/{id:int}", role="engineer")
def delete_input(req: Req):
    library.set_deleted(req.user, req.int_param("id"), True)
    return {"ok": True}


@api("POST", "/api/inputs/{id:int}/restore", role="engineer")
def restore_input(req: Req):
    iid = req.int_param("id")
    library.set_deleted(req.user, iid, False)
    return library.detail(req.user, iid)


@api("POST", "/api/inputs/{id:int}/link", role="engineer")
def link(req: Req):
    from ..inputs import id_list
    d = _data(req)
    iid = req.int_param("id")
    library.link(req.user, iid, id_list(d.get("item_ids"), "item_ids"), id_list(d.get("project_ids"), "project_ids"))
    return library.detail(req.user, iid)


@api("POST", "/api/inputs/{id:int}/unlink", role="engineer")
def unlink(req: Req):
    from ..inputs import id_list
    d = _data(req)
    iid = req.int_param("id")
    library.link(req.user, iid, id_list(d.get("item_ids"), "item_ids"), id_list(d.get("project_ids"), "project_ids"),
                 unlink=True)
    return library.detail(req.user, iid)


@api("POST", "/api/inputs/{id:int}/files", role="engineer")
def add_files(req: Req):
    iid = req.int_param("id")
    library.get(req.user, iid)                   # check access before storing anything
    stored, errors = _stored_uploads(req)
    if not stored:
        raise bad(errors[0]["error"] if errors else "No file was sent.")
    duplicates = [{"filename": f["filename"], "inputs": d} for f in stored
                  if (d := library.duplicates_of(req.user, f["sha"], iid))]
    library.add_files(req.user, iid, stored)
    library.worker.wake()
    return {**library.detail(req.user, iid), "errors": errors, "duplicates_found": duplicates}


@api("DELETE", "/api/inputs/{id:int}/files/{n:int}", role="engineer")
def remove_file(req: Req):
    iid = req.int_param("id")
    library.set_file_removed(req.user, iid, req.int_param("n"), True)
    return library.detail(req.user, iid)


@api("POST", "/api/inputs/{id:int}/files/{n:int}/restore", role="engineer")
def restore_file(req: Req):
    iid = req.int_param("id")
    library.set_file_removed(req.user, iid, req.int_param("n"), False)
    return library.detail(req.user, iid)


@api("POST", "/api/inputs/{id:int}/files/{n:int}/reread", role="engineer")
def reread_file(req: Req):
    iid = req.int_param("id")
    library.reread(req.user, iid, req.int_param("n"))
    library.worker.wake()
    return library.detail(req.user, iid)


@api("GET", "/api/inputs/{id:int}/files/{n:int}")
def download(req: Req):
    """The file itself (with access check). ``inline=1`` shows PDFs and images in the browser."""
    iid = req.int_param("id")
    f = library.file_row(req.user, iid, req.int_param("n"))
    if not store.exists(f["blob_sha"]):
        raise not_found("The stored file is missing on the server. Ask your admin to restore it from the backup.")
    inline = as_bool(req.q("inline")) and f["ext"] in INLINE_MIME and f["ext"] != "svg"
    db.audit(req.uid, "input_download", "input", iid, f["filename"])
    return Response(store.read_bytes(f["blob_sha"]),
                    media_type=INLINE_MIME[f["ext"]] if inline else "application/octet-stream",
                    headers={"Content-Disposition": _disposition("inline" if inline else "attachment", f["filename"]),
                             "Cache-Control": "private, no-store", **SAFE_HEADERS})


@api("GET", "/api/inputs/{id:int}/files/{n:int}/thumb")
def thumb(req: Req):
    f = library.file_row(req.user, req.int_param("id"), req.int_param("n"))
    if not f["thumb_sha"] or not store.exists(f["thumb_sha"]):
        raise not_found("No preview for this file yet")
    return Response(store.read_bytes(f["thumb_sha"]), media_type=INLINE_MIME.get(f["thumb_kind"], "application/octet-stream"),
                    headers={"Cache-Control": "private, max-age=86400", "ETag": f["thumb_sha"][:32], **SAFE_HEADERS})


@api("GET", "/api/inputs/{id:int}/files/{n:int}/text")
def text(req: Req):
    """The text Kosha read from the file, as a plain-text download."""
    f = library.file_row(req.user, req.int_param("id"), req.int_param("n"))
    if not f["text_sha"] or not store.exists(f["text_sha"]):
        raise not_found("No text has been read from this file.")
    return Response(store.read_bytes(f["text_sha"]), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": _disposition("attachment", f["filename"] + ".txt"), **SAFE_HEADERS})


@api("GET", "/api/tags")
def tags(req: Req):
    """Tags of the inputs you can see, most used first (for autocomplete)."""
    try:
        limit = int(req.q("limit") or 20)
    except ValueError:
        raise bad("limit must be a number.")
    return {"tags": library.tags(req.user, req.q("q") or "", limit)}


@api("GET", "/api/items/{id:int}/inputs")
def item_inputs(req: Req):
    """Inputs linked to a part, plus the projects the part belongs to (for the part page)."""
    from .. import projects
    iid = req.int_param("id")
    if not db.scalar("SELECT id FROM items WHERE id = ?", (iid,)):
        raise not_found("Part not found")
    res = library.search(req.user, {"item": iid, "limit": req.q("limit") or 50, "facets": "0"})
    return {"inputs": res["inputs"], "total": res["total"], "projects": projects.projects_of_item(iid)}

