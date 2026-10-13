# SPDX-License-Identifier: AGPL-3.0-or-later
"""Change requests (ECR): *please change this* (specification 7, 6.11).

Anyone who can see a part raises one from the part or its file, with a reason; the discussion stays with it. An
engineer turns it into a change order (ECO), or adds it to one already open; an approver declines it with a reason.
Numbers are ``ECR-YY-NNNN`` and never reused. Requests are never deleted: a withdrawn or declined request stays in
the list with its reason."""
from __future__ import annotations

from . import access, changes, db, lifecycle
from .web import ROLE_LEVEL, bad, conflict, forbidden, not_found

STATES = {"open": "Open", "accepted": "In a change order", "declined": "Declined", "withdrawn": "Withdrawn"}


def _visible(cr: dict) -> bool:
    sc = access.scope()
    if cr.get("document_id"):
        d = db.one("SELECT * FROM documents WHERE id = ?", (cr["document_id"],))
        if d and not sc.can_see_document(d):
            return False
    if cr.get("item_id") and not sc.can_see_item(cr["item_id"]):
        return False
    return True


def cr_or_404(cr_id: int) -> dict:
    cr = db.one("SELECT * FROM change_requests WHERE id = ?", (cr_id,))
    if not cr or not _visible(cr):
        raise not_found("Change request not found")
    return cr


def create(user: dict, title: str, reason: str, document_id=None, item_id=None) -> int:
    title, reason = (title or "").strip(), (reason or "").strip()
    if not title:
        raise bad("Say in a few words what should change.")
    if not reason:
        raise bad("Say why it should change (a site report, a cost, a customer request ...).")
    if document_id:
        d = lifecycle.doc_or_404(int(document_id))
        item_id = d["item_id"] or item_id
    if item_id:
        if not db.scalar("SELECT id FROM items WHERE id = ?", (int(item_id),)):
            raise not_found("Item not found")
        access.check_item(int(item_id))
    with db.tx():
        cr_id = db.insert("change_requests", {
            "number": changes._next_number("ECR", "change_requests"), "title": title[:200], "reason": reason[:4000],
            "document_id": int(document_id) if document_id else None, "item_id": int(item_id) if item_id else None,
            "state": "open", "raised_by": user["id"], "raised_at": db.now()})
        db.audit(user["id"], "ecr_raise", "change_request", cr_id, title)
    return cr_id


def _level(user: dict) -> int:
    return ROLE_LEVEL.get(user["role"], 0)


def actions(user: dict, cr: dict) -> dict:
    is_open = cr["state"] == "open"
    eng = _level(user) >= ROLE_LEVEL["engineer"]
    appr = _level(user) >= ROLE_LEVEL["approver"]
    closed = "This request is already closed." if not is_open else ""
    return {
        "convert": {"ok": is_open and eng, "why": closed or ("" if eng else "Turning a request into a change order "
                                                                         "needs the engineer role.")},
        "decline": {"ok": is_open and appr, "why": closed or ("" if appr else "Declining a request needs the "
                                                                            "approver role.")},
        "withdraw": {"ok": is_open and (cr["raised_by"] == user["id"] or appr),
                     "why": closed or "Only the person who raised it, or an approver, can withdraw it."},
    }


def detail(cr_id: int, user: dict) -> dict:
    cr = cr_or_404(cr_id)
    names = lifecycle.user_names()
    doc = db.one("SELECT id, filename, revision, state FROM documents WHERE id = ?", (cr["document_id"],)) \
        if cr["document_id"] else None
    item = db.one("SELECT id, part_number, description, revision, state FROM items WHERE id = ?", (cr["item_id"],)) \
        if cr["item_id"] else None
    co = db.one("SELECT id, number, title, state FROM change_orders WHERE id = ?", (cr["co_id"],)) if cr["co_id"] else None
    return {**cr, "state_label": STATES.get(cr["state"], cr["state"]), "raised_by_name": names.get(cr["raised_by"], "?"),
            "decided_by_name": names.get(cr["decided_by"]) if cr["decided_by"] else None, "document": doc,
            "item": item, "change_order": co, "actions": actions(user, cr)}


def listing(state: str = "", mine_only: bool = False, user: dict | None = None) -> list[dict]:
    names = lifecycle.user_names()
    where, params = [], []
    if state:
        where.append("r.state = ?")
        params.append(state)
    if mine_only and user:
        where.append("r.raised_by = ?")
        params.append(user["id"])
    rows = db.rows("SELECT r.*, i.part_number, c.number AS co_number FROM change_requests r LEFT JOIN items i ON "
                   "i.id = r.item_id LEFT JOIN change_orders c ON c.id = r.co_id" +
                   (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY r.id DESC LIMIT 500", params)
    return [{**r, "raised_by_name": names.get(r["raised_by"], "?"), "state_label": STATES.get(r["state"], r["state"])}
            for r in rows if _visible(r)]


def _close(user: dict, cr: dict, state: str, note: str, co_id: int | None = None) -> None:
    db.update("change_requests", cr["id"], {"state": state, "decided_by": user["id"], "decided_at": db.now(),
                                            "decision_note": note, "co_id": co_id})


def decline(user: dict, cr_id: int, note: str) -> None:
    cr = cr_or_404(cr_id)
    a = actions(user, cr)["decline"]
    if not a["ok"]:
        raise conflict(a["why"]) if cr["state"] != "open" else forbidden(a["why"])
    if not (note or "").strip():
        raise bad("Say why it is declined, so the person who raised it understands.")
    with db.tx():
        _close(user, cr, "declined", note.strip())
        db.audit(user["id"], "ecr_decline", "change_request", cr_id, note.strip()[:200])
    _tell_raiser(cr, f"{cr['number']} was declined: {note.strip()}", actor=user["id"])


def withdraw(user: dict, cr_id: int) -> None:
    cr = cr_or_404(cr_id)
    a = actions(user, cr)["withdraw"]
    if not a["ok"]:
        raise conflict(a["why"]) if cr["state"] != "open" else forbidden(a["why"])
    with db.tx():
        _close(user, cr, "withdrawn", "")
        db.audit(user["id"], "ecr_withdraw", "change_request", cr_id, cr["number"])


def convert(user: dict, cr_id: int, co_id=None, approver_ids=None) -> int:
    """Accept the request into a new change order (or a draft one already open). The request's file is added."""
    cr = cr_or_404(cr_id)
    a = actions(user, cr)["convert"]
    if not a["ok"]:
        raise conflict(a["why"]) if cr["state"] != "open" else forbidden(a["why"])
    with db.tx():
        if co_id:
            co = changes.co_or_404(int(co_id))
            if co["state"] not in ("draft", "rejected"):
                raise conflict(f"{co['number']} is no longer a draft; choose another change order or start a new one.")
            if cr["document_id"] and not db.scalar("SELECT 1 FROM co_items WHERE co_id = ? AND document_id = ?",
                                                   (co["id"], cr["document_id"])):
                changes.add_item(user, co["id"], cr["document_id"])
            if not co["ecr_id"]:
                db.update("change_orders", co["id"], {"ecr_id": cr_id})
            new_id = co["id"]
        else:
            new_id = changes.create(user, cr["title"], f"{cr['reason']}\n\nFrom {cr['number']}.",
                                    doc_ids=[cr["document_id"]] if cr["document_id"] else [],
                                    approver_ids=approver_ids or [], ecr_id=cr_id)
        number = db.scalar("SELECT number FROM change_orders WHERE id = ?", (new_id,))
        _close(user, cr, "accepted", f"Taken into {number}", new_id)
        db.audit(user["id"], "ecr_accept", "change_request", cr_id, number)
    _tell_raiser(cr, f"{cr['number']} is being worked on in change order {number}", f"#/change/{new_id}", user["id"])
    return new_id


def _tell_raiser(cr: dict, title: str, link: str = "", actor: int | None = None) -> None:
    from . import notifications
    if cr["raised_by"] == actor:
        return
    try:
        notifications.notify(cr["raised_by"], "change_request", title, link or f"#/ecr/{cr['id']}", actor)
    except Exception:       # a notice never undoes the decision behind it
        pass
