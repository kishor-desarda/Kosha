# SPDX-License-Identifier: AGPL-3.0-or-later
"""Projects API: a light record (code, name, customer, status, dates) that inputs and parts belong to."""
from __future__ import annotations

from .. import db, projects
from ..inputs import library
from ..web import Req, api, bad, not_found


def _body(req: Req) -> dict:
    return dict(req.body) if isinstance(req.body, dict) else {}


def _out(user: dict, p: dict, counts: dict | None = None) -> dict:
    out = projects.summary(p)
    out["input_count"] = (counts if counts is not None else library.count_by(user, "project", [p["id"]])).get(p["id"], 0)
    return out


@api("GET", "/api/projects")
def list_projects(req: Req):
    """Projects, open ones first. ``q`` searches code, name and customer; ``status`` is open or closed."""
    try:
        limit = int(req.q("limit") or 200)
    except ValueError:
        raise bad("limit must be a number.")
    rows = projects.search(req.q("q") or "", req.q("status") or "", limit)
    counts = library.count_by(req.user, "project", [r["id"] for r in rows])     # only inputs you may see
    return {"projects": [_out(req.user, r, counts) for r in rows]}


@api("POST", "/api/projects", role="engineer")
def create_project(req: Req):
    return {"project": _out(req.user, projects.create(req.user, _body(req)))}


@api("GET", "/api/projects/{id:int}")
def get_project(req: Req):
    p = projects.project_or_404(req.int_param("id"))
    out = _out(req.user, p)
    out["items"] = projects.items_of(p["id"])
    out["created_by_name"] = db.scalar("SELECT full_name FROM users WHERE id = ?", (p["created_by"],)) or ""
    return {"project": out}


@api("PATCH", "/api/projects/{id:int}", role="engineer")
def update_project(req: Req):
    p = projects.update(req.user, req.int_param("id"), _body(req))
    return {"project": _out(req.user, p)}


@api("POST", "/api/projects/{id:int}/items", role="engineer")
def link_item(req: Req):
    """Link a part to the project, by ``item_id`` or ``part_number``."""
    pid = req.int_param("id")
    item_id = req.b("item_id")
    if not item_id and req.b("part_number"):
        item_id = db.scalar("SELECT id FROM items WHERE part_number = ? COLLATE NOCASE",
                            (str(req.b("part_number")).strip(),))
        if not item_id:
            raise not_found(f"There is no part {req.b('part_number')}.")
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        raise bad("Choose a part to link.")
    projects.link_item(req.user, pid, item_id)
    return {"items": projects.items_of(pid)}


@api("DELETE", "/api/projects/{id:int}/items/{item_id:int}", role="engineer")
def unlink_item(req: Req):
    pid = req.int_param("id")
    projects.unlink_item(req.user, pid, req.int_param("item_id"))
    return {"items": projects.items_of(pid)}
