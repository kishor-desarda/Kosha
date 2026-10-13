# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who approves a change order, and making sure they do (specification 7.6).

* **Routes** are set per category (the code that starts the part number), per project, or as the default for every
  change: one approver for normal parts; design lead plus quality for pressure and rotating parts. A route asks its
  approvers all at once (*parallel*) or one after another (*sequential*). The routes of every category and project
  in the change are combined; the default route is used when none of them has one.
* **Delegates:** an approver names a stand-in until a date (leave). The stand-in decides in the approver's name and
  both names are kept.
* **Reminders:** after two working days a waiting approver (and their stand-in) is reminded; after four the route's
  design lead is told (or the administrators, when the route names nobody).
* **Decisions are kept for good** in ``co_decisions``: the person, the time, the revisions and the comment. Nothing
  updates or deletes them.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from . import access, db
from .web import bad, not_found

MODES = {"parallel": "All at once", "sequential": "One after another"}
SCOPES = {"default": "Every change", "category": "Category", "project": "Project"}
REMIND_AFTER_DAYS = 2           # working days
ESCALATE_AFTER_DAYS = 4


def can_approve(u: dict | None) -> bool:
    """Approvers, and people with the extra role *quality* (for the design-plus-quality routes)."""
    return bool(u) and bool(u.get("active", 1)) and (access.has_role(u, "approver") or access.has_role(u, "quality"))


def _user(uid) -> dict | None:
    try:
        return db.one("SELECT * FROM users WHERE id = ?", (int(uid),))
    except (TypeError, ValueError):
        return None


# -------------------------------------------------------------------- routes --

def routes(include_inactive: bool = False) -> list[dict]:
    from .lifecycle import user_names
    names = user_names()
    out = []
    for r in db.rows("SELECT * FROM approval_routes" + ("" if include_inactive else " WHERE active = 1") +
                     " ORDER BY CASE scope WHEN 'default' THEN 0 WHEN 'category' THEN 1 ELSE 2 END, value, id"):
        ids = json.loads(r.pop("approvers_json") or "[]")
        out.append({**r, "approvers": [{"id": i, "name": names.get(i, "?")} for i in ids],
                    "lead_name": names.get(r["lead_id"]) if r["lead_id"] else None,
                    "scope_label": SCOPES.get(r["scope"], r["scope"]), "mode_label": MODES.get(r["mode"], r["mode"])})
    return out


def _clean(body: dict, current: dict | None = None) -> dict:
    cur = current or {}
    name = str(body.get("name", cur.get("name", "")) or "").strip()
    scope = str(body.get("scope", cur.get("scope", "default")) or "default")
    value = str(body.get("value", cur.get("value", "")) or "").strip().upper()
    mode = str(body.get("mode", cur.get("mode", "parallel")) or "parallel")
    approvers = body.get("approver_ids", json.loads(cur.get("approvers_json") or "[]"))
    lead = body.get("lead_id", cur.get("lead_id"))
    if not name:
        raise bad("Give the route a name, for example \"Rotating parts\".")
    if scope not in SCOPES:
        raise bad("A route is for every change, a category or a project.")
    if mode not in MODES:
        raise bad("Approvers are asked all at once or one after another.")
    if scope == "default":
        value = ""
    elif scope == "category" and not access.CATEGORY.match(value):
        raise bad("Category codes are 2 to 4 letters or digits, for example ROT.")
    elif scope == "project" and not db.scalar("SELECT id FROM projects WHERE code_key = ?", (value,)):
        raise bad(f"There is no project {value or '(empty)'}.")
    if not isinstance(approvers, list) or not approvers:
        raise bad("Choose at least one approver.")
    ids: list[int] = []
    for a in approvers:
        u = _user(a)
        if not u or not u["active"]:
            raise bad("One of the approvers is not an active user.")
        if not can_approve(u):
            raise bad(f"{u['full_name']} cannot approve: give them the approver role or the extra role "
                      "Quality and purchase first.")
        if u["id"] not in ids:
            ids.append(u["id"])
    if lead in ("", 0):
        lead = None
    if lead is not None:
        lu = _user(lead)
        if not lu or not lu["active"]:
            raise bad("The design lead must be an active user.")
        lead = lu["id"]
    clash = db.scalar("SELECT id FROM approval_routes WHERE active = 1 AND scope = ? AND value = ? AND id != ?",
                      (scope, value, cur.get("id") or 0))
    if clash:
        raise bad("There is already a route for " + ("every change." if scope == "default" else f"{value}."))
    return {"name": name[:80], "scope": scope, "value": value, "mode": mode, "approvers_json": json.dumps(ids),
            "lead_id": lead}


def add_route(user_id: int, body: dict) -> dict:
    vals = _clean(body)
    with db.tx():
        rid = db.insert("approval_routes", {**vals, "active": 1, "created_by": user_id, "created_at": db.now()})
        db.audit(user_id, "route_add", "approval_route", rid, f"{vals['name']}: {vals['scope']} {vals['value']}".strip())
    return next(r for r in routes() if r["id"] == rid)


def _route_or_404(rid: int) -> dict:
    r = db.one("SELECT * FROM approval_routes WHERE id = ? AND active = 1", (rid,))
    if not r:
        raise not_found("Approval route not found")
    return r


def update_route(user_id: int, rid: int, body: dict) -> dict:
    cur = _route_or_404(rid)
    vals = _clean(body, cur)
    with db.tx():
        db.update("approval_routes", rid, vals)
        db.audit(user_id, "route_change", "approval_route", rid, vals["name"])
    return next(r for r in routes() if r["id"] == rid)


def remove_route(user_id: int, rid: int) -> None:
    cur = _route_or_404(rid)
    with db.tx():       # kept, switched off: older change orders still show who was asked
        db.update("approval_routes", rid, {"active": 0})
        db.audit(user_id, "route_remove", "approval_route", rid, cur["name"])


def route_for(co_id: int) -> dict:
    """The combined route for a change order: [(user id, step)], mode and design lead."""
    item_ids = [r["item_id"] for r in db.rows("SELECT DISTINCT d.item_id FROM co_items ci JOIN documents d ON "
                                              "d.id = ci.document_id WHERE ci.co_id = ? AND d.item_id IS NOT NULL",
                                              (co_id,))]
    cats, projects = set(), set()
    for iid in item_ids:
        pn = db.scalar("SELECT part_number FROM items WHERE id = ?", (iid,)) or ""
        if "-" in pn:
            cats.add(pn.split("-", 1)[0].upper())
        for p in db.rows("SELECT p.code_key FROM project_items pi JOIN projects p ON p.id = pi.project_id "
                         "WHERE pi.item_id = ?", (iid,)):
            projects.add(p["code_key"])
    active = db.rows("SELECT * FROM approval_routes WHERE active = 1 ORDER BY id")
    matched = [r for r in active if (r["scope"] == "category" and r["value"] in cats) or
               (r["scope"] == "project" and r["value"] in projects)]
    if not matched:
        matched = [r for r in active if r["scope"] == "default"]
    steps: dict[int, int] = {}
    sequential = any(r["mode"] == "sequential" for r in matched)
    for r in matched:
        for i, uid in enumerate(json.loads(r["approvers_json"] or "[]")):
            step = i + 1 if r["mode"] == "sequential" else 1
            steps[uid] = min(steps.get(uid, step), step)
    lead = next((r["lead_id"] for r in matched if r["lead_id"]), None)
    return {"approvers": sorted(steps.items(), key=lambda x: (x[1], x[0])),
            "mode": "sequential" if sequential else "parallel", "lead_id": lead,
            "routes": [r["name"] for r in matched]}


def apply_route(co: dict) -> list[str]:
    """Replace the approvers of a draft change order with its route. The requester is never their own approver.
    Returns the names of the routes used (empty when no route applies). Call inside a transaction."""
    r = route_for(co["id"])
    people = [(uid, step) for uid, step in r["approvers"] if uid != co["requested_by"] and can_approve(_user(uid))]
    if not people:
        return []
    # renumber the steps 1, 2, 3 ... after leaving people out
    order = {s: i + 1 for i, s in enumerate(sorted({s for _, s in people}))}
    db.execute("DELETE FROM co_approvers WHERE co_id = ?", (co["id"],))
    for uid, step in people:
        db.execute("INSERT INTO co_approvers (co_id, user_id, step) VALUES (?, ?, ?)", (co["id"], uid, order[step]))
    db.update("change_orders", co["id"], {"route_mode": r["mode"], "lead_id": r["lead_id"]})
    return r["routes"]


# ----------------------------------------------------------------- delegates --

def today() -> str:
    return date.today().isoformat()


def active_delegate(user_id: int, on: str | None = None) -> int | None:
    u = db.one("SELECT delegate_id, delegate_until FROM users WHERE id = ?", (user_id,))
    if not u or not u["delegate_id"] or not u["delegate_until"] or u["delegate_until"] < (on or today()):
        return None
    return u["delegate_id"]


def set_delegate(user: dict, delegate_id, until: str | None) -> dict:
    if delegate_id in (None, "", 0):
        with db.tx():
            db.update("users", user["id"], {"delegate_id": None, "delegate_until": None})
            db.audit(user["id"], "delegate_clear", "user", user["id"], "")
        return {"delegate": None}
    d = _user(delegate_id)
    if not d or not d["active"]:
        raise bad("Choose an active colleague as your stand-in.")
    if d["id"] == user["id"]:
        raise bad("Choose someone else as your stand-in.")
    if not can_approve(d):
        raise bad(f"{d['full_name']} cannot approve changes, so they cannot stand in for you.")
    try:
        day = date.fromisoformat(str(until or ""))
    except ValueError:
        raise bad("Say until when, as a date.") from None
    if day.isoformat() < today():
        raise bad("That date has passed. Choose the last day of your leave.")
    with db.tx():
        db.update("users", user["id"], {"delegate_id": d["id"], "delegate_until": day.isoformat()})
        db.audit(user["id"], "delegate_set", "user", user["id"], f"{d['full_name']} until {day.isoformat()}")
    return delegate_of(user["id"])


def delegate_of(user_id: int) -> dict:
    u = db.one("SELECT delegate_id, delegate_until FROM users WHERE id = ?", (user_id,))
    if not u or not u["delegate_id"]:
        return {"delegate": None}
    name = db.scalar("SELECT full_name FROM users WHERE id = ?", (u["delegate_id"],))
    return {"delegate": {"id": u["delegate_id"], "name": name, "until": u["delegate_until"],
                         "active": active_delegate(user_id) is not None}}


def standing_in_for(user_id: int) -> list[int]:
    """The approvers this person currently stands in for."""
    return [r["id"] for r in db.rows("SELECT id FROM users WHERE delegate_id = ? AND delegate_until >= ?",
                                     (user_id, today()))]


def acting_row(user: dict, co_id: int) -> dict | None:
    """The waiting approval this person may decide: their own, or one of someone they stand in for."""
    own = db.one("SELECT * FROM co_approvers WHERE co_id = ? AND user_id = ? AND decision = 'pending'",
                 (co_id, user["id"]))
    if own:
        return own
    for uid in standing_in_for(user["id"]):
        row = db.one("SELECT * FROM co_approvers WHERE co_id = ? AND user_id = ? AND decision = 'pending'", (co_id, uid))
        if row:
            return row
    return None


def with_delegates(user_ids) -> list[int]:
    out: list[int] = []
    for uid in user_ids:
        for x in (uid, active_delegate(uid)):
            if x and x not in out:
                out.append(x)
    return out


# ------------------------------------------------------------------- steps --

def start_round(co_id: int, mode: str) -> list[int]:
    """Ask the first step (or everybody, in parallel). Returns who was asked. Call inside a transaction."""
    rows = db.rows("SELECT * FROM co_approvers WHERE co_id = ? ORDER BY step, id", (co_id,))
    first = min((r["step"] for r in rows), default=1)
    asked = []
    now = db.now()
    for r in rows:
        now_asked = mode != "sequential" or r["step"] == first
        db.execute("UPDATE co_approvers SET decision = ?, comment = '', decided_at = NULL, decided_by = NULL, "
                   "asked_at = ?, reminded_at = NULL, escalated_at = NULL WHERE id = ?",
                   ("pending" if now_asked else "waiting", now if now_asked else None, r["id"]))
        if now_asked:
            asked.append(r["user_id"])
    return asked


def next_step(co_id: int) -> list[int]:
    """After an approval: ask the next step when the current one is done. Returns who was asked."""
    if db.scalar("SELECT COUNT(*) FROM co_approvers WHERE co_id = ? AND decision = 'pending'", (co_id,)):
        return []
    step = db.scalar("SELECT MIN(step) FROM co_approvers WHERE co_id = ? AND decision = 'waiting'", (co_id,))
    if step is None:
        return []
    rows = db.rows("SELECT * FROM co_approvers WHERE co_id = ? AND decision = 'waiting' AND step = ?", (co_id, step))
    for r in rows:
        db.execute("UPDATE co_approvers SET decision = 'pending', asked_at = ? WHERE id = ?", (db.now(), r["id"]))
    return [r["user_id"] for r in rows]


def notify_asked(co_id: int, user_ids: list[int], requester_id: int | None = None) -> None:
    """Tell the people now asked (and their stand-ins). Call after the transaction has committed."""
    if not user_ids:
        return
    from . import notify_outbox as outbox
    from .lifecycle import user_names
    co = db.one("SELECT * FROM change_orders WHERE id = ?", (co_id,))
    names = user_names()
    body = f"{names.get(co['requested_by'], 'Someone')} asks you to approve {co['number']}: {co['title']}."
    outbox.enqueue(with_delegates(user_ids), f"{co['number']} needs your approval: {co['title']}", body,
                   f"#/change/{co_id}", "approvals", key=f"change.step:{co_id}:{co['round']}:{min(user_ids)}",
                   source=f"change:{co_id}")


# --------------------------------------------------------------- reminders --

def working_days(start: datetime, end: datetime) -> int:
    """Whole working days (Monday to Friday) from ``start`` to ``end``."""
    d, n = start.date(), 0
    while d < end.date():
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _parse(ts: str) -> datetime:
    t = datetime.fromisoformat(ts)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def send_reminders(now: datetime | None = None) -> dict:
    """Remind approvers who have waited two working days, and tell the design lead after four. Each is sent once
    per approval round. Safe to run as often as wanted."""
    from . import notify_outbox as outbox
    from .lifecycle import user_names
    now = now or datetime.now(timezone.utc)
    names = user_names()
    reminded, escalated = [], []
    rows = db.rows("SELECT a.*, c.number, c.title, c.lead_id, c.round FROM co_approvers a JOIN change_orders c ON "
                   "c.id = a.co_id WHERE c.state = 'submitted' AND a.decision = 'pending' AND a.asked_at IS NOT NULL")
    for r in rows:
        days = working_days(_parse(r["asked_at"]), now)
        if days >= REMIND_AFTER_DAYS and not r["reminded_at"]:
            with db.tx():
                db.execute("UPDATE co_approvers SET reminded_at = ? WHERE id = ? AND reminded_at IS NULL",
                           (db.now(), r["id"]))
            reminded.append(r)
        if days >= ESCALATE_AFTER_DAYS and not r["escalated_at"]:
            with db.tx():
                db.execute("UPDATE co_approvers SET escalated_at = ? WHERE id = ? AND escalated_at IS NULL",
                           (db.now(), r["id"]))
            escalated.append(r)
    for r in reminded:
        outbox.enqueue(with_delegates([r["user_id"]]), f"Reminder: {r['number']} is waiting for your approval",
                       f"{r['number']}: {r['title']} has waited {REMIND_AFTER_DAYS} working days for your decision.",
                       f"#/change/{r['co_id']}", "approvals", key=f"change.remind:{r['id']}:{r['round']}",
                       source=f"change:{r['co_id']}")
    for r in escalated:
        lead = [r["lead_id"]] if r["lead_id"] else [u["id"] for u in db.rows(
            "SELECT id FROM users WHERE role = 'admin' AND active = 1")]
        who = names.get(r["user_id"], "an approver")
        outbox.enqueue(lead, f"{r['number']} has waited {ESCALATE_AFTER_DAYS} working days for {who}",
                       f"{r['number']}: {r['title']} is still waiting for {who}. Ask them, or have a stand-in "
                       "named for them.", f"#/change/{r['co_id']}", "approvals",
                       key=f"change.escalate:{r['id']}:{r['round']}", source=f"change:{r['co_id']}")
    return {"reminded": len(reminded), "escalated": len(escalated)}
