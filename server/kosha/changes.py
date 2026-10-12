# SPDX-License-Identifier: AGPL-3.0-or-later
"""Engineering change orders (ECO): draft -> submitted -> approved -> implemented."""
from __future__ import annotations

from . import db, lifecycle
from .web import ROLE_LEVEL, bad, conflict, forbidden, not_found

CO_STATES = ("draft", "submitted", "approved", "rejected", "implemented", "cancelled")


def co_or_404(co_id: int) -> dict:
    c = db.one("SELECT * FROM change_orders WHERE id = ?", (co_id,))
    if not c:
        raise not_found("Change order not found")
    return c


def _next_number() -> str:
    last = db.scalar("SELECT number FROM change_orders ORDER BY id DESC LIMIT 1")
    n = int(last.split("-")[-1]) + 1 if last and last.split("-")[-1].isdigit() else 1
    return f"ECO-{n:04d}"


def create(user: dict, title: str, reason: str = "", description: str = "", priority: str = "normal",
           doc_ids: list[int] | None = None, approver_ids: list[int] | None = None) -> int:
    if not (title or "").strip():
        raise bad("Give the change a short title.")
    with db.tx():
        co_id = db.insert("change_orders", {
            "number": _next_number(), "title": title.strip(), "reason": reason or "", "description": description or "",
            "priority": priority if priority in ("low", "normal", "high", "urgent") else "normal",
            "state": "draft", "requested_by": user["id"], "created_at": db.now(),
        })
        for d in doc_ids or []:
            add_item(user, co_id, int(d))
        for a in approver_ids or []:
            add_approver(user, co_id, int(a))
        db.audit(user["id"], "eco_create", "change_order", co_id, title)
    return co_id


def _editable(co: dict, user: dict) -> None:
    if co["state"] not in ("draft", "rejected"):
        raise conflict("Only draft or rejected change orders can be edited.", "not_draft")
    if co["requested_by"] != user["id"] and ROLE_LEVEL[user["role"]] < ROLE_LEVEL["approver"]:
        raise forbidden("Only the requester or an approver can edit this change order.")


def add_item(user: dict, co_id: int, doc_id: int, action: str = "release") -> None:
    co = co_or_404(co_id)
    _editable(co, user)
    d = lifecycle.doc_or_404(doc_id)
    if action not in ("release", "obsolete"):
        raise bad("Action must be release or obsolete")
    other = db.one("SELECT c.number FROM co_items ci JOIN change_orders c ON c.id = ci.co_id WHERE ci.document_id = ? "
                   "AND c.state IN ('draft','submitted','approved') AND c.id != ?", (doc_id, co_id))
    if other:
        raise conflict(f"{d['filename']} is already part of {other['number']}.", "in_other_eco")
    to_rev = d["revision"]
    if action == "release" and d["state"] == "released":
        to_rev = lifecycle.next_revision(d["revision"])
    with db.tx():
        db.execute("INSERT OR REPLACE INTO co_items (co_id, document_id, action, from_revision, to_revision) "
                   "VALUES (?, ?, ?, ?, ?)", (co_id, doc_id, action,
                                              d["revision"] if d["state"] == "released" else
                                              _last_released(doc_id), to_rev))


def _last_released(doc_id: int) -> str | None:
    return db.scalar("SELECT revision FROM lifecycle_events WHERE document_id = ? AND to_state = 'released' "
                     "ORDER BY id DESC LIMIT 1", (doc_id,))


def remove_item(user: dict, co_id: int, doc_id: int) -> None:
    co = co_or_404(co_id)
    _editable(co, user)
    db.execute("DELETE FROM co_items WHERE co_id = ? AND document_id = ?", (co_id, doc_id))


def add_approver(user: dict, co_id: int, user_id: int) -> None:
    co = co_or_404(co_id)
    _editable(co, user)
    u = db.one("SELECT id, role, active FROM users WHERE id = ?", (user_id,))
    if not u or not u["active"]:
        raise bad("Unknown user")
    if ROLE_LEVEL[u["role"]] < ROLE_LEVEL["approver"]:
        raise bad("Approvers need the approver or admin role.")
    db.execute("INSERT OR IGNORE INTO co_approvers (co_id, user_id) VALUES (?, ?)", (co_id, user_id))


def remove_approver(user: dict, co_id: int, user_id: int) -> None:
    co = co_or_404(co_id)
    _editable(co, user)
    db.execute("DELETE FROM co_approvers WHERE co_id = ? AND user_id = ?", (co_id, user_id))


def revise_items(user: dict, co_id: int) -> int:
    """Start new revisions of released files listed with action 'release'."""
    co = co_or_404(co_id)
    _editable(co, user)
    n = 0
    for ci in db.rows("SELECT * FROM co_items WHERE co_id = ? AND action = 'release'", (co_id,)):
        d = lifecycle.doc_or_404(ci["document_id"])
        if d["state"] == "released":
            nd = lifecycle.revise(user, d["id"], f"Revised for {co['number']}", co_id)
            db.execute("UPDATE co_items SET to_revision = ? WHERE id = ?", (nd["revision"], ci["id"]))
            n += 1
    return n


def submit(user: dict, co_id: int) -> None:
    co = co_or_404(co_id)
    _editable(co, user)
    items = db.rows("SELECT * FROM co_items WHERE co_id = ?", (co_id,))
    if not items:
        raise bad("Add at least one affected file.")
    if not db.scalar("SELECT COUNT(*) FROM co_approvers WHERE co_id = ?", (co_id,)):
        raise bad("Add at least one approver.")
    problems = []
    for ci in items:
        d = lifecycle.doc_or_404(ci["document_id"])
        if d["checked_out_by"]:
            problems.append(f"{d['filename']} is still checked out")
        if ci["action"] == "release" and d["state"] == "released":
            problems.append(f"{d['filename']} is released; press 'Start new revisions' and make your changes first")
        if ci["action"] == "obsolete" and d["state"] != "released":
            problems.append(f"{d['filename']} must be released to be made obsolete")
    if problems:
        raise conflict("Can't submit yet: " + "; ".join(problems) + ".", "not_ready")
    with db.tx():
        for ci in items:
            d = lifecycle.doc_or_404(ci["document_id"])
            if ci["action"] == "release" and d["state"] == "in_work":
                db.update("documents", d["id"], {"state": "in_review", "updated_at": db.now()})
                db.insert("lifecycle_events", {"document_id": d["id"], "revision": d["revision"], "from_state": "in_work",
                                               "to_state": "in_review", "user_id": user["id"], "change_order_id": co_id,
                                               "comment": f"Submitted with {co['number']}", "at": db.now()})
            db.execute("UPDATE co_items SET to_revision = ? WHERE id = ?", (d["revision"], ci["id"]))
        db.execute("UPDATE co_approvers SET decision = 'pending', comment = '', decided_at = NULL WHERE co_id = ?", (co_id,))
        db.update("change_orders", co_id, {"state": "submitted", "submitted_at": db.now(), "decided_at": None})
        db.audit(user["id"], "eco_submit", "change_order", co_id, co["number"])


def decide(user: dict, co_id: int, decision: str, comment: str = "") -> None:
    co = co_or_404(co_id)
    if co["state"] != "submitted":
        raise conflict("This change order is not waiting for approval.", "not_submitted")
    if decision not in ("approved", "rejected"):
        raise bad("Decision must be approved or rejected")
    row = db.one("SELECT * FROM co_approvers WHERE co_id = ? AND user_id = ?", (co_id, user["id"]))
    if not row and user["role"] != "admin":
        raise forbidden("You are not an approver on this change order.")
    if decision == "rejected" and not (comment or "").strip():
        raise bad("Please say why you are rejecting it, so the requester knows what to fix.")
    with db.tx():
        if row:
            db.execute("UPDATE co_approvers SET decision = ?, comment = ?, decided_at = ? WHERE id = ?",
                       (decision, comment or "", db.now(), row["id"]))
        else:
            db.execute("INSERT INTO co_approvers (co_id, user_id, decision, comment, decided_at) VALUES (?,?,?,?,?)",
                       (co_id, user["id"], decision, comment or "", db.now()))
        if comment:
            db.insert("co_comments", {"co_id": co_id, "user_id": user["id"],
                                      "text": f"[{decision}] {comment}", "at": db.now()})
        if decision == "rejected":
            db.update("change_orders", co_id, {"state": "rejected", "decided_at": db.now()})
            for ci in db.rows("SELECT * FROM co_items WHERE co_id = ?", (co_id,)):
                d = lifecycle.doc_or_404(ci["document_id"])
                if d["state"] == "in_review":
                    db.update("documents", d["id"], {"state": "in_work", "updated_at": db.now()})
                    db.insert("lifecycle_events", {"document_id": d["id"], "revision": d["revision"],
                                                   "from_state": "in_review", "to_state": "in_work", "user_id": user["id"],
                                                   "change_order_id": co_id, "comment": f"{co['number']} rejected",
                                                   "at": db.now()})
        else:
            pending = db.scalar("SELECT COUNT(*) FROM co_approvers WHERE co_id = ? AND decision != 'approved'", (co_id,))
            if not pending:
                db.update("change_orders", co_id, {"state": "approved", "decided_at": db.now()})
        db.audit(user["id"], f"eco_{decision}", "change_order", co_id, co["number"])


def implement(user: dict, co_id: int) -> dict:
    co = co_or_404(co_id)
    if co["state"] != "approved":
        raise conflict("Only approved change orders can be implemented.", "not_approved")
    if ROLE_LEVEL[user["role"]] < ROLE_LEVEL["approver"]:
        raise forbidden("Implementing a change needs the approver role.")
    done = []
    for ci in db.rows("SELECT * FROM co_items WHERE co_id = ?", (co_id,)):
        d = lifecycle.doc_or_404(ci["document_id"])
        if ci["action"] == "release" and d["state"] in ("in_work", "in_review"):
            lifecycle.set_state(user, d["id"], "released", f"Released by {co['number']}", co_id, via_change=True)
            done.append(f"{d['filename']} released at {d['revision']}")
        elif ci["action"] == "obsolete" and d["state"] == "released":
            lifecycle.set_state(user, d["id"], "obsolete", f"Made obsolete by {co['number']}", co_id, via_change=True)
            done.append(f"{d['filename']} obsolete")
    db.update("change_orders", co_id, {"state": "implemented", "implemented_at": db.now()})
    db.audit(user["id"], "eco_implement", "change_order", co_id, co["number"])
    return {"done": done}


def cancel(user: dict, co_id: int) -> None:
    co = co_or_404(co_id)
    if co["state"] in ("implemented", "cancelled"):
        raise conflict("This change order is already closed.")
    if co["requested_by"] != user["id"] and ROLE_LEVEL[user["role"]] < ROLE_LEVEL["approver"]:
        raise forbidden()
    with db.tx():
        for ci in db.rows("SELECT * FROM co_items WHERE co_id = ?", (co_id,)):
            d = lifecycle.doc_or_404(ci["document_id"])
            if d["state"] == "in_review":
                db.update("documents", d["id"], {"state": "in_work", "updated_at": db.now()})
        db.update("change_orders", co_id, {"state": "cancelled", "decided_at": db.now()})
        db.audit(user["id"], "eco_cancel", "change_order", co_id, co["number"])


def detail(co_id: int) -> dict:
    co = co_or_404(co_id)
    names = lifecycle.user_names()
    items = []
    for ci in db.rows("SELECT * FROM co_items WHERE co_id = ? ORDER BY id", (co_id,)):
        d = db.one("SELECT * FROM documents WHERE id = ?", (ci["document_id"],))
        items.append({**ci, "document": lifecycle.doc_summary(d, names) if d else None})
    approvers = [{**a, "name": names.get(a["user_id"], "?")}
                 for a in db.rows("SELECT * FROM co_approvers WHERE co_id = ? ORDER BY id", (co_id,))]
    comments = [{**c, "name": names.get(c["user_id"], "?")}
                for c in db.rows("SELECT * FROM co_comments WHERE co_id = ? ORDER BY id", (co_id,))]
    return {**co, "requested_by_name": names.get(co["requested_by"], "?"), "items": items,
            "approvers": approvers, "comments": comments}
