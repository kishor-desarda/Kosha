# SPDX-License-Identifier: AGPL-3.0-or-later
"""Engineering change orders (ECO): draft -> submitted -> approved -> implemented.

Every change order answers the three interchangeability questions when it revises a released part (7.4), says when
the change takes effect (7.5), and is approved along its route (7.6, see ``approval``). Change requests (ECR) that
lead to it are in ``change_requests``."""
from __future__ import annotations

from datetime import date, datetime, timezone

from . import approval, db, lifecycle
from .web import ROLE_LEVEL, bad, conflict, forbidden, not_found

CO_STATES = ("draft", "submitted", "approved", "rejected", "implemented", "cancelled")


def co_or_404(co_id: int) -> dict:
    c = db.one("SELECT * FROM change_orders WHERE id = ?", (co_id,))
    if not c:
        raise not_found("Change order not found")
    return c


def _next_number(kind: str = "ECO", table: str = "change_orders") -> str:
    """ECO-26-0031 (or ECR-26-0007): the year and a running number that starts again each year. Older ECO-0001
    numbers stay."""
    head = f"{kind}-{datetime.now(timezone.utc).year % 100:02d}-"
    seqs = [int(r["number"][len(head):]) for r in db.rows(f"SELECT number FROM {table} WHERE number LIKE ?",
                                                           (head + "%",)) if r["number"][len(head):].isdigit()]
    return f"{head}{max(seqs, default=0) + 1:04d}"


QUESTIONS = (
    ("ic_fit", "Will the new part fit everywhere the old one is used, including machines already at customers' sites?"),
    ("ic_mix", "Can old and new stock be mixed in the store without any problem?"),
    ("ic_function", "Will it do the same job, with the same life and performance?"),
)
EFFECTIVITY = {
    "implemented": "From the day it is implemented, for new work orders",
    "date": "From a date, for new work orders",
    "serial": "From a machine serial number onwards",
    "immediate": "Immediately, including open work orders (the old design is unsafe)",
}


def interchangeability(co: dict) -> dict:
    """The form, fit, function test: same number and next revision only when all three answers are yes."""
    answers = [co.get(k) or "" for k, _ in QUESTIONS]
    if co.get("customer_only"):
        result, text = "customer", ("Customer-specific change: the part gets a new number, marked customer "
                                    "restricted.")
    elif "" in answers:
        result, text = "", "Answer the three questions."
    elif all(a == "yes" for a in answers):
        result, text = "same", "Interchangeable: same number, next revision."
    else:
        result, text = "new", ("Not interchangeable: the part gets a new number. The old number becomes spares only "
                               "if installed machines still need it, otherwise obsolete.")
    return {"questions": [{"key": k, "text": t, "answer": co.get(k) or ""} for k, t in QUESTIONS],
            "customer_only": bool(co.get("customer_only")), "result": result, "text": text}


def effectivity_text(co: dict) -> str:
    e = co.get("effectivity") or "implemented"
    if e == "date" and co.get("effective_date"):
        return f"From {co['effective_date']}, for new work orders"
    if e == "serial" and co.get("effective_serial"):
        return f"From machine {co['effective_serial']} onwards"
    return EFFECTIVITY.get(e, e)


def clean_fields(body: dict) -> dict:
    """The fields of a change order that can be edited while it is a draft."""
    upd = {k: body[k] for k in ("title", "reason", "description", "priority") if body.get(k) is not None}
    if "title" in upd and not str(upd["title"]).strip():
        raise bad("Give the change a short title.")
    for k, _ in QUESTIONS:
        if k in body:
            if body[k] not in ("yes", "no", ""):
                raise bad("Answer yes or no.")
            upd[k] = body[k]
    if "customer_only" in body:
        upd["customer_only"] = 1 if body["customer_only"] else 0
    if "effectivity" in body:
        if body["effectivity"] not in EFFECTIVITY:
            raise bad("Say when the change takes effect: when implemented, from a date, from a machine serial, or "
                      "immediately.")
        upd["effectivity"] = body["effectivity"]
    if body.get("effective_date") not in (None, ""):
        try:
            upd["effective_date"] = date.fromisoformat(str(body["effective_date"])).isoformat()
        except ValueError:
            raise bad("The effective date must be a date, for example 2026-11-01.") from None
    elif "effective_date" in body:
        upd["effective_date"] = None
    if "effective_serial" in body:
        upd["effective_serial"] = str(body["effective_serial"] or "").strip().upper()[:40]
    return upd


def update(user: dict, co_id: int, body: dict) -> None:
    co = co_or_404(co_id)
    _editable(co, user)
    upd = clean_fields(body)
    if upd:
        with db.tx():
            db.update("change_orders", co_id, upd)


def create(user: dict, title: str, reason: str = "", description: str = "", priority: str = "normal",
           doc_ids: list[int] | None = None, approver_ids: list[int] | None = None, ecr_id: int | None = None,
           fields: dict | None = None) -> int:
    """A new draft. Without approvers named, the approval route of its parts is used."""
    if not (title or "").strip():
        raise bad("Give the change a short title.")
    extra = {k: v for k, v in clean_fields(fields or {}).items() if k not in ("title", "reason", "description",
                                                                             "priority")}
    with db.tx():
        co_id = db.insert("change_orders", {
            "number": _next_number(), "title": title.strip(), "reason": reason or "", "description": description or "",
            "priority": priority if priority in ("low", "normal", "high", "urgent") else "normal",
            "state": "draft", "requested_by": user["id"], "created_at": db.now(), "ecr_id": ecr_id, **extra,
        })
        for d in doc_ids or []:
            add_item(user, co_id, int(d))
        for a in approver_ids or []:
            add_approver(user, co_id, int(a))
        if not approver_ids:
            approval.apply_route(co_or_404(co_id))
        db.audit(user["id"], "eco_create", "change_order", co_id, title)
    return co_id


def _editable(co: dict, user: dict) -> None:
    if co["state"] not in ("draft", "rejected"):
        raise conflict("Only draft or rejected change orders can be edited.", "not_draft")
    if co["requested_by"] != user["id"] and ROLE_LEVEL[user["role"]] < ROLE_LEVEL["approver"]:
        raise forbidden("Only the requester or an approver can edit this change order.")


ACTIONS = {"release": "release", "spares_only": "make spares only", "obsolete": "make obsolete"}


def add_item(user: dict, co_id: int, doc_id: int, action: str = "release", replacement: str = "") -> None:
    co = co_or_404(co_id)
    _editable(co, user)
    d = lifecycle.doc_or_404(doc_id)
    if action not in ACTIONS:
        raise bad("Action must be release, spares only or obsolete.")
    replacement = (replacement or "").strip().upper()
    if action == "spares_only":
        from .release_rules import refuse, spares_only_problems
        refuse(spares_only_problems(d, replacement)[0], "Can't add it")
    other = db.one("SELECT c.number FROM co_items ci JOIN change_orders c ON c.id = ci.co_id WHERE ci.document_id = ? "
                   "AND c.state IN ('draft','submitted','approved') AND c.id != ?", (doc_id, co_id))
    if other:
        raise conflict(f"{d['filename']} is already part of {other['number']}.", "in_other_eco")
    to_rev = d["revision"]
    if action == "release" and d["state"] == "released":
        to_rev = lifecycle.next_revision(d["revision"])
    with db.tx():
        db.execute("INSERT OR REPLACE INTO co_items (co_id, document_id, action, from_revision, to_revision, replacement) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (co_id, doc_id, action,
                                                 d["revision"] if d["state"] == "released" else
                                                 _last_released(doc_id), to_rev, replacement))


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
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not u or not u["active"]:
        raise bad("Unknown user")
    if not approval.can_approve(u):
        raise bad("Approvers need the approver role, or the extra role Quality and purchase.")
    step = db.scalar("SELECT MAX(step) FROM co_approvers WHERE co_id = ?", (co_id,)) or 0
    if co["route_mode"] != "sequential":
        step = 1
    else:
        step += 1
    db.execute("INSERT OR IGNORE INTO co_approvers (co_id, user_id, step) VALUES (?, ?, ?)", (co_id, user_id, step))


def use_route(user: dict, co_id: int) -> list[str]:
    co = co_or_404(co_id)
    _editable(co, user)
    with db.tx():
        used = approval.apply_route(co)
    if not used:
        raise bad("No approval route fits this change. An administrator sets routes up under Admin › Rules; until "
                  "then, add approvers by hand.")
    return used


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


def _parents_to_update(co_id: int) -> list[dict]:
    """Released assemblies that use a part this change makes spares only or obsolete: they get a new revision with
    the new part, in the same change (7.4)."""
    in_co = {r["item_id"] for r in db.rows("SELECT d.item_id FROM co_items ci JOIN documents d ON d.id = ci.document_id "
                                           "WHERE ci.co_id = ? AND ci.action = 'release' AND d.item_id IS NOT NULL",
                                           (co_id,))}
    out, seen = [], set()
    for ci in db.rows("SELECT ci.*, d.item_id, i.part_number FROM co_items ci JOIN documents d ON d.id = ci.document_id "
                      "JOIN items i ON i.id = d.item_id WHERE ci.co_id = ? AND ci.action IN ('spares_only', 'obsolete')",
                      (co_id,)):
        for p in db.rows("SELECT DISTINCT p.id, p.part_number, p.description FROM bom_lines bl JOIN boms b ON "
                         "b.id = bl.bom_id JOIN items p ON p.id = b.item_id WHERE bl.child_item_id = ? AND "
                         "b.state = 'released' AND p.state IN ('released', 'spares_only') ORDER BY p.part_number",
                         (ci["item_id"],)):
            if (p["id"], ci["part_number"]) in seen:
                continue
            seen.add((p["id"], ci["part_number"]))
            has_doc = bool(db.scalar("SELECT 1 FROM documents WHERE item_id = ? AND deleted = 0", (p["id"],)))
            out.append({"item_id": p["id"], "part_number": p["part_number"], "description": p["description"],
                        "uses": ci["part_number"], "in_change": p["id"] in in_co, "has_document": has_doc})
    return out


def submit_problems(co: dict, items: list[dict]) -> list[str]:
    problems = []
    revised = []
    for ci in items:
        d = lifecycle.doc_or_404(ci["document_id"])
        if d["checked_out_by"]:
            problems.append(f"{d['filename']} is still checked out")
        if ci["action"] == "release" and d["state"] == "released":
            problems.append(f"{d['filename']} is released; press 'Start new revisions' and make your changes first")
        if ci["action"] == "obsolete" and d["state"] not in lifecycle.LIVE_STATES:
            problems.append(f"{d['filename']} must be released to be made obsolete")
        if ci["action"] == "spares_only" and d["state"] != "released":
            problems.append(f"{d['filename']} must be released to be made spares only")
        if ci["action"] == "release" and d["state"] == "in_work":
            from .release_rules import review_problems
            problems += [f"{d['filename']}: {p.rstrip('.')}" for p in review_problems(d, co["id"])]
        if ci["action"] == "release" and ci["from_revision"]:
            revised.append(d)
    ic = interchangeability(co)
    if revised and not ic["result"]:
        problems.append("answer the three interchangeability questions (fit, mixing stock, same job)")
    elif revised and ic["result"] in ("new", "customer"):
        for d in revised:
            problems.append(f"the answers say {d['filename']} is not interchangeable, so it needs a new part number "
                            "instead of a new revision: make the new part (Copy design), add it to this change, and "
                            "add the old part as spares only or obsolete")
    for p in _parents_to_update(co["id"]):
        if not p["in_change"] and p["has_document"]:
            problems.append(f"{p['part_number']} uses {p['uses']}; add its assembly to this change so it gets a new "
                            "revision with the new part")
    e = co.get("effectivity") or "implemented"
    if e == "date" and not co.get("effective_date"):
        problems.append("say from which date the change takes effect")
    if e == "serial" and not co.get("effective_serial"):
        problems.append("say from which machine serial number the change takes effect")
    if e == "immediate" and not (co.get("reason") or "").strip():
        problems.append("say in the reason why it cannot wait (it also changes open work orders)")
    return problems


def submit(user: dict, co_id: int) -> None:
    co = co_or_404(co_id)
    _editable(co, user)
    items = db.rows("SELECT * FROM co_items WHERE co_id = ?", (co_id,))
    if not items:
        raise bad("Add at least one affected file.")
    if not db.scalar("SELECT COUNT(*) FROM co_approvers WHERE co_id = ?", (co_id,)):
        with db.tx():
            approval.apply_route(co)
        co = co_or_404(co_id)
    if not db.scalar("SELECT COUNT(*) FROM co_approvers WHERE co_id = ?", (co_id,)):
        raise bad("Add at least one approver.")
    problems = submit_problems(co, items)
    if problems:
        raise conflict("Can't submit yet: " + "; ".join(problems) + ".", "not_ready", problems=problems)
    with db.tx():
        for ci in items:
            d = lifecycle.doc_or_404(ci["document_id"])
            if ci["action"] == "release" and d["state"] == "in_work":
                db.update("documents", d["id"], {"state": "in_review", "updated_at": db.now()})
                db.insert("lifecycle_events", {"document_id": d["id"], "revision": d["revision"], "from_state": "in_work",
                                               "to_state": "in_review", "user_id": user["id"], "change_order_id": co_id,
                                               "comment": f"Submitted with {co['number']}", "at": db.now()})
            db.execute("UPDATE co_items SET to_revision = ? WHERE id = ?", (d["revision"], ci["id"]))
        rnd = (co["round"] or 1) + (1 if co["state"] == "rejected" else 0)
        db.update("change_orders", co_id, {"state": "submitted", "submitted_at": db.now(), "decided_at": None,
                                           "round": rnd})
        approval.start_round(co_id, co["route_mode"])
        db.audit(user["id"], "eco_submit", "change_order", co_id, co["number"])


def _revisions(co_id: int) -> str:
    return ", ".join(f"{r['filename']} rev {r['to_revision'] or r['revision']}" for r in db.rows(
        "SELECT d.filename, d.revision, ci.to_revision, ci.action FROM co_items ci JOIN documents d ON "
        "d.id = ci.document_id WHERE ci.co_id = ? ORDER BY ci.id", (co_id,)))


def decide(user: dict, co_id: int, decision: str, comment: str = "") -> None:
    """Approve or reject. Each decision is also written to ``co_decisions``, which is never changed afterwards."""
    co = co_or_404(co_id)
    if co["state"] != "submitted":
        raise conflict("This change order is not waiting for approval.", "not_submitted")
    if decision not in ("approved", "rejected"):
        raise bad("Decision must be approved or rejected")
    row = approval.acting_row(user, co_id)
    if not row and user["role"] != "admin":
        mine = db.one("SELECT decision FROM co_approvers WHERE co_id = ? AND user_id = ?", (co_id, user["id"]))
        if mine and mine["decision"] == "waiting":
            raise conflict("It is not your turn yet: the approvers before you on the route decide first.", "not_your_turn")
        if mine:
            raise conflict("You have already decided on this change order.", "already_decided")
        raise forbidden("You are not an approver on this change order.")
    if row and row["user_id"] != user["id"] and co["requested_by"] == user["id"]:
        raise forbidden("You asked for this change, so you cannot approve it as a stand-in (four-eyes rule).")
    if decision == "rejected" and not (comment or "").strip():
        raise bad("Please say why you are rejecting it, so the requester knows what to fix.")
    asked: list[int] = []
    with db.tx():
        if row:
            db.execute("UPDATE co_approvers SET decision = ?, comment = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                       (decision, comment or "", db.now(), user["id"], row["id"]))
            for_whom = row["user_id"]
        else:
            db.execute("INSERT INTO co_approvers (co_id, user_id, decision, comment, decided_at, decided_by, step) "
                       "VALUES (?,?,?,?,?,?,1) ON CONFLICT(co_id, user_id) DO UPDATE SET decision = excluded.decision, "
                       "comment = excluded.comment, decided_at = excluded.decided_at, decided_by = excluded.decided_by",
                       (co_id, user["id"], decision, comment or "", db.now(), user["id"]))
            for_whom = user["id"]
        db.insert("co_decisions", {"co_id": co_id, "round": co["round"] or 1, "user_id": user["id"],
                                   "on_behalf_of": for_whom if for_whom != user["id"] else None, "decision": decision,
                                   "comment": comment or "", "revisions": _revisions(co_id), "at": db.now()})
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
            asked = approval.next_step(co_id)
            pending = db.scalar("SELECT COUNT(*) FROM co_approvers WHERE co_id = ? AND decision != 'approved'", (co_id,))
            if not pending:
                db.update("change_orders", co_id, {"state": "approved", "decided_at": db.now()})
        detail_txt = co["number"] + (f" for {lifecycle.user_names().get(for_whom, '?')}" if for_whom != user["id"] else "")
        db.audit(user["id"], f"eco_{decision}", "change_order", co_id, detail_txt)
    approval.notify_asked(co_id, asked)


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
        elif ci["action"] == "obsolete" and d["state"] in lifecycle.LIVE_STATES:
            lifecycle.set_state(user, d["id"], "obsolete", f"Made obsolete by {co['number']}", co_id, via_change=True)
            done.append(f"{d['filename']} obsolete")
        elif ci["action"] == "spares_only" and d["state"] == "released":
            lifecycle.set_state(user, d["id"], "spares_only", f"Spares only by {co['number']}", co_id, via_change=True,
                                replacement=ci["replacement"])
            done.append(f"{d['filename']} spares only, replaced by {ci['replacement']}")
    db.update("change_orders", co_id, {"state": "implemented", "implemented_at": db.now()})
    db.audit(user["id"], "eco_implement", "change_order", co_id, co["number"])
    if co["effectivity"] == "immediate":
        done.append("This change applies to open work orders too: check them in the ERP.")
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


def detail(co_id: int, user: dict | None = None) -> dict:
    co = co_or_404(co_id)
    names = lifecycle.user_names()
    items = []
    for ci in db.rows("SELECT * FROM co_items WHERE co_id = ? ORDER BY id", (co_id,)):
        d = db.one("SELECT * FROM documents WHERE id = ?", (ci["document_id"],))
        items.append({**ci, "document": lifecycle.doc_summary(d, names) if d else None})
    approvers = []
    for a in db.rows("SELECT * FROM co_approvers WHERE co_id = ? ORDER BY step, id", (co_id,)):
        dlg = approval.active_delegate(a["user_id"])
        approvers.append({**a, "name": names.get(a["user_id"], "?"),
                          "decided_by_name": names.get(a["decided_by"]) if a["decided_by"] not in (None, a["user_id"])
                          else None, "delegate_name": names.get(dlg) if dlg else None})
    comments = [{**c, "name": names.get(c["user_id"], "?")}
                for c in db.rows("SELECT * FROM co_comments WHERE co_id = ? ORDER BY id", (co_id,))]
    decisions = [{**x, "name": names.get(x["user_id"], "?"),
                  "for_name": names.get(x["on_behalf_of"]) if x["on_behalf_of"] else None}
                 for x in db.rows("SELECT * FROM co_decisions WHERE co_id = ? ORDER BY id", (co_id,))]
    ecr = db.one("SELECT id, number, title FROM change_requests WHERE id = ?", (co["ecr_id"],)) if co["ecr_id"] else None
    mine = None
    if user and co["state"] == "submitted":
        row = approval.acting_row(user, co_id)
        if row:
            mine = {"for_id": row["user_id"], "for_name": names.get(row["user_id"]) if row["user_id"] != user["id"]
                    else None}
    revised = any(i["action"] == "release" and i["from_revision"] for i in items)
    return {**co, "requested_by_name": names.get(co["requested_by"], "?"), "items": items,
            "approvers": approvers, "comments": comments, "decisions": decisions, "ecr": ecr,
            "interchangeability": {**interchangeability(co), "needed": revised},
            "effectivity_text": effectivity_text(co), "effectivity_options": EFFECTIVITY,
            "parents": _parents_to_update(co_id), "lead_name": names.get(co["lead_id"]) if co["lead_id"] else None,
            "my_decision": mine}
