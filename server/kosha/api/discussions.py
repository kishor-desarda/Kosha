# SPDX-License-Identifier: AGPL-3.0-or-later
"""Discussion threads on items, document revisions, change orders, deviations and BOMs."""
from __future__ import annotations

from .. import discussions
from ..addins import registry
from ..web import Req, api, bad


def _entity(req: Req, source: str = "query") -> tuple[str, int]:
    get = req.q if source == "query" else req.b
    etype = (get("entity_type") or "").strip()
    try:
        eid = int(get("entity_id"))
    except (TypeError, ValueError):
        raise bad("entity_id is required")
    return etype, eid


@api("GET", "/api/discussions")
def list_threads(req: Req):
    etype, eid = _entity(req)
    return discussions.threads(req.user, etype, eid)


@api("POST", "/api/discussions")
def start_thread(req: Req):
    etype, eid = _entity(req, "body")
    tid = discussions.start(req.user, etype, eid, req.b("body") or "", req.b("title") or "", req.b("revision"))
    registry.emit("comment.added", {"thread_id": tid, "entity_type": etype, "entity_id": eid, "user_id": req.uid})
    return {**discussions.threads(req.user, etype, eid), "thread_id": tid}


def _thread_view(req: Req, thread_id: int, **extra) -> dict:
    t = discussions.thread_or_404(thread_id)
    return {**discussions.threads(req.user, t["entity_type"], t["entity_id"]), "thread_id": t["id"], **extra}


@api("POST", "/api/discussions/{id:int}/comments")
def reply(req: Req):
    cid = discussions.reply(req.user, req.int_param("id"), req.b("body") or "")
    t = discussions.thread_or_404(req.int_param("id"))
    registry.emit("comment.added", {"thread_id": t["id"], "comment_id": cid, "entity_type": t["entity_type"],
                                    "entity_id": t["entity_id"], "user_id": req.uid})
    return _thread_view(req, req.int_param("id"), comment_id=cid)


@api("POST", "/api/discussions/{id:int}/resolve")
def resolve(req: Req):
    discussions.set_status(req.user, req.int_param("id"), True)
    return _thread_view(req, req.int_param("id"))


@api("POST", "/api/discussions/{id:int}/reopen")
def reopen(req: Req):
    discussions.set_status(req.user, req.int_param("id"), False)
    return _thread_view(req, req.int_param("id"))


@api("PATCH", "/api/discussions/comments/{id:int}")
def edit_comment(req: Req):
    discussions.edit(req.user, req.int_param("id"), req.b("body") or "")
    return _thread_view(req, discussions.comment_or_404(req.int_param("id"))["thread_id"])


@api("DELETE", "/api/discussions/comments/{id:int}")
def remove_comment(req: Req):
    discussions.remove(req.user, req.int_param("id"))
    return _thread_view(req, discussions.comment_or_404(req.int_param("id"))["thread_id"])
