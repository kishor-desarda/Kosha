# SPDX-License-Identifier: AGPL-3.0-or-later
"""File safety: resumable uploads, the recycle bin, the two-admin purge and the clean-up of unfinished files."""
from __future__ import annotations

from .. import filesafety, lifecycle, resumable
from ..addins import registry
from ..web import Req, api


# ------------------------------------------------------- resumable uploads --

@api("POST", "/api/uploads", role="engineer")
def start_upload(req: Req):
    """Start a resumable upload: ``filename``, ``size`` (bytes) and ``sha256`` of the whole file."""
    return resumable.start(req.user, req.b("filename") or "", req.b("size"), req.b("sha256") or "")


@api("GET", "/api/uploads/{id}", role="engineer")
def upload_status(req: Req):
    return resumable.status(req.user, req.params["id"])


@api("PUT", "/api/uploads/{id}", role="engineer")
def upload_piece(req: Req):
    """One piece of the file, as raw bytes (``Content-Type: application/octet-stream``), at ``?offset=``."""
    return resumable.put_piece(req.user, req.params["id"], req.q("offset"), req.body)


@api("POST", "/api/uploads/{id}/finish", role="engineer")
def finish_upload(req: Req):
    """Check the whole file and add it to ``folder_id`` (optional ``path`` below it), or check it in over
    ``document_id``. Takes ``comment``, ``keep_checked_out`` and the properties an ordinary upload takes."""
    body = req.body if isinstance(req.body, dict) else {}
    d = resumable.finish(req.user, req.params["id"], body)
    if body.get("document_id"):
        registry.emit("file.checked_in", {"document_id": d["id"], "item_id": d.get("item_id"),
                                          "filename": d["filename"], "revision": d["revision"],
                                          "version": d.get("latest_version"), "user_id": req.uid})
    return lifecycle.doc_summary(d, lifecycle.user_names())


@api("DELETE", "/api/uploads/{id}", role="engineer")
def cancel_upload(req: Req):
    resumable.cancel(req.user, req.params["id"])
    return {"ok": True}


# -------------------------------------------------------------- recycle bin --

@api("GET", "/api/recycle-bin", role="engineer")
def recycle_bin(req: Req):
    return {"files": filesafety.bin_listing(req.user), "days": filesafety._days()}


@api("POST", "/api/recycle-bin/{id:int}/restore", role="engineer")
def restore(req: Req):
    d = filesafety.restore(req.user, req.int_param("id"))
    return lifecycle.doc_summary(d, lifecycle.user_names())


# -------------------------------------------------------------------- purge --

@api("GET", "/api/purges", role="admin")
def purges(req: Req):
    return {"requests": filesafety.purge_requests()}


@api("POST", "/api/purges", role="admin")
def ask_purge(req: Req):
    """Ask to remove files in the recycle bin for good; a second admin confirms."""
    return filesafety.ask_purge(req.uid, req.b("document_ids") or [], req.b("note") or "")


@api("POST", "/api/purges/{id:int}/confirm", role="admin")
def confirm_purge(req: Req):
    return filesafety.decide_purge(req.uid, req.int_param("id"), True)


@api("POST", "/api/purges/{id:int}/cancel", role="admin")
def cancel_purge(req: Req):
    return filesafety.decide_purge(req.uid, req.int_param("id"), False)


# ----------------------------------------------------------------- clean-up --

@api("POST", "/api/maintenance/cleanup", role="admin")
def cleanup(req: Req):
    """Run the clean-up of unfinished uploads and stored files no record refers to now (it also runs every six
    hours in the background)."""
    return filesafety.cleanup()
