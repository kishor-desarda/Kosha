# SPDX-License-Identifier: AGPL-3.0-or-later
"""Projects: a light record that inputs and items belong to.

A project has a code (unique, case-insensitive), a name, a customer (free text), a status (open or
closed) and dates. It is not a project-management tool: it only groups reference material and parts.
Projects are never deleted; closing one keeps everything that belongs to it.

A sales order booked in the ERP creates its project (specification 6.2): code PRJ-26-041, the customer, the
delivery date, and the standard folder set under Projects/ (*GA and layout*, *Design*, *Customer documents*,
*Purchase*, *Manuals*; the admin can change the set). The same sales order never makes a second project.
"""
from __future__ import annotations

import re
from datetime import date

from . import access, db
from .web import bad, conflict, not_found

STATUSES = ("open", "closed")
STATUS_LABEL = {"open": "Open", "closed": "Closed"}
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/\-]{0,39}$")
FIELDS = ("code", "name", "customer", "status", "start_date", "due_date", "description")
FOLDER_SET = ["GA and layout", "Design", "Customer documents", "Purchase", "Manuals"]


def clean_date(value, label: str) -> str | None:
    """Accept YYYY-MM-DD (or empty). Returns the ISO date or None."""
    v = str(value or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        raise bad(f"{label} must be a date like 2026-10-13.")


def clean_code(code) -> str:
    c = re.sub(r"\s+", " ", str(code or "").strip())
    if not CODE_RE.match(c):
        raise bad("A project code needs 1 to 40 letters or digits; dots, dashes, slashes and spaces are allowed "
                  "inside it (e.g. PRJ-2026-014).")
    return c


def project_or_404(pid: int) -> dict:
    p = db.one("SELECT * FROM projects WHERE id = ?", (pid,))
    if not p or not access.scope().can_see_project(pid):
        raise not_found("Project not found")
    return p


def _values(data: dict, current: dict | None = None) -> dict:
    out: dict = {}
    if "code" in data or current is None:
        out["code"] = clean_code(data.get("code"))
        out["code_key"] = out["code"].upper()
    for k in ("name", "customer", "description"):
        if k in data:
            out[k] = str(data.get(k) or "").strip()[:2000 if k == "description" else 200]
    if "status" in data:
        s = str(data.get("status") or "").strip().lower()
        if s not in STATUSES:
            raise bad("Status must be open or closed.")
        out["status"] = s
        if current is None or current["status"] != s:
            out["closed_at"] = db.now() if s == "closed" else None
    if "start_date" in data:
        out["start_date"] = clean_date(data.get("start_date"), "Start date")
    if "due_date" in data:
        out["due_date"] = clean_date(data.get("due_date"), "Due date")
    start = out.get("start_date", current["start_date"] if current else None)
    due = out.get("due_date", current["due_date"] if current else None)
    if start and due and due < start:
        raise bad("The due date is before the start date.")
    return out


def create(user: dict, data: dict) -> dict:
    vals = _values(data)
    if not vals.get("name"):
        vals["name"] = ""
    now = db.now()
    with db.tx():
        if db.scalar("SELECT id FROM projects WHERE code_key = ?", (vals["code_key"],)):
            raise conflict(f"There is already a project {vals['code']}.", "duplicate_code")
        pid = db.insert("projects", {**vals, "status": vals.get("status", "open"), "created_by": user["id"],
                                     "created_at": now, "updated_at": now})
        db.audit(user["id"], "project_create", "project", pid, vals["code"])
    return project_or_404(pid)


def update(user: dict, pid: int, data: dict) -> dict:
    with db.tx():
        p = project_or_404(pid)
        vals = _values({k: v for k, v in data.items() if k in FIELDS}, p)
        if "code_key" in vals and vals["code_key"] != p["code_key"] and \
                db.scalar("SELECT id FROM projects WHERE code_key = ? AND id != ?", (vals["code_key"], pid)):
            raise conflict(f"There is already a project {vals['code']}.", "duplicate_code")
        if not vals:
            return p
        vals["updated_at"] = db.now()
        db.update("projects", pid, vals)
        db.audit(user["id"], "project_update", "project", pid, ",".join(k for k in vals if k != "updated_at"))
    if "code" in vals or "name" in vals or "customer" in vals:
        from . import inputs          # inputs search their project codes and names
        inputs.library.reindex_where("SELECT input_id FROM input_projects WHERE project_id = ?", (pid,))
    return project_or_404(pid)


def link_item(user: dict, pid: int, item_id: int) -> bool:
    project_or_404(pid)
    if not db.scalar("SELECT id FROM items WHERE id = ?", (item_id,)):
        raise not_found("Part not found")
    with db.tx():
        if db.scalar("SELECT 1 FROM project_items WHERE project_id = ? AND item_id = ?", (pid, item_id)):
            return False
        db.execute("INSERT INTO project_items (project_id, item_id, created_by, created_at) VALUES (?, ?, ?, ?)",
                   (pid, item_id, user["id"], db.now()))
        db.audit(user["id"], "project_link_item", "project", pid, str(item_id))
    return True


def unlink_item(user: dict, pid: int, item_id: int) -> bool:
    project_or_404(pid)
    with db.tx():
        if not db.scalar("SELECT 1 FROM project_items WHERE project_id = ? AND item_id = ?", (pid, item_id)):
            return False
        db.execute("DELETE FROM project_items WHERE project_id = ? AND item_id = ?", (pid, item_id))
        db.audit(user["id"], "project_unlink_item", "project", pid, str(item_id))
    return True


def items_of(pid: int) -> list[dict]:
    return db.rows("SELECT i.id, i.part_number, i.description, i.item_type, i.state, i.revision FROM project_items pi "
                   "JOIN items i ON i.id = pi.item_id WHERE pi.project_id = ?" + access.scope().items() +
                   " ORDER BY i.part_number", (pid,))


def projects_of_item(item_id: int) -> list[dict]:
    return db.rows("SELECT p.id, p.code, p.name, p.status FROM project_items pi JOIN projects p ON p.id = pi.project_id "
                   "WHERE pi.item_id = ?" + access.scope().projects() + " ORDER BY p.code", (item_id,))


def search(q: str = "", status: str = "", limit: int = 200) -> list[dict]:
    where, params = [], []
    if status in STATUSES:
        where.append("p.status = ?")
        params.append(status)
    for t in (q or "").split():
        where.append("(p.code LIKE ? OR p.name LIKE ? OR p.customer LIKE ?)")
        params += [f"%{t}%"] * 3
    sql = ("SELECT p.*, (SELECT COUNT(*) FROM project_items pi WHERE pi.project_id = p.id) AS item_count "
           "FROM projects p WHERE 1 = 1" + "".join(" AND " + w for w in where) + access.scope().projects() +
           " ORDER BY CASE p.status WHEN 'open' THEN 0 ELSE 1 END, p.code_key LIMIT ?")
    return db.rows(sql, [*params, max(1, min(int(limit), 1000))])


def folder_set() -> list[str]:
    return db.get_setting("project_folder_set") or FOLDER_SET


def from_sales_order(user_id: int | None, number: str, customer: str = "", due_date: str | None = None,
                     name: str = "") -> tuple[dict, bool]:
    """The project of an ERP sales order, made with its folders when it does not exist yet. (project, created)"""
    from .numbering import NumberingService
    number = " ".join(str(number or "").split())[:60]
    if not number:
        raise bad("Give the sales order number.")
    with db.tx():        # one at a time: the same sales order never makes two projects
        existing = db.one("SELECT * FROM projects WHERE sales_order = ? COLLATE NOCASE", (number,))
        if existing:
            return existing, False
        due = clean_date(due_date, "Delivery date") if due_date else None
        customer = " ".join(str(customer or "").split())[:200]
        code = NumberingService().next_in_series(f"PRJ-{date.today():%y}-", 3, user_id)
        from .lifecycle import ensure_folder_path
        safe = re.sub(r'[\\/:*?"<>|]', " ", customer)[:60].strip()
        folder_name = f"{code} {safe}" if safe else code
        top = ensure_folder_path(f"Projects/{folder_name}", user_id)
        for sub in folder_set():
            ensure_folder_path(f"Projects/{folder_name}/{sub}", user_id)
        now = db.now()
        pid = db.insert("projects", {"code": code, "code_key": code.upper(), "name": name or f"Sales order {number}",
                                     "customer": customer, "status": "open", "start_date": date.today().isoformat(),
                                     "due_date": due, "sales_order": number, "folder_id": top,
                                     "created_by": user_id, "created_at": now, "updated_at": now})
        db.audit(user_id, "project_from_sales_order", "project", pid, f"{code} for sales order {number}")
    return db.one("SELECT * FROM projects WHERE id = ?", (pid,)), True


def summary(p: dict) -> dict:
    out = {k: p[k] for k in ("id", "code", "name", "customer", "status", "start_date", "due_date", "closed_at",
                             "description", "created_by", "created_at", "updated_at")}
    out["sales_order"] = p.get("sales_order") or ""
    out["folder_id"] = p.get("folder_id")
    out["status_label"] = STATUS_LABEL.get(p["status"], p["status"])
    if "item_count" in p:
        out["item_count"] = p["item_count"]
    return out
