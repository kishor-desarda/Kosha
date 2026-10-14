# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who can do what (roles) and who can see what (access rules), specification sections 2 and 15.2.

**Roles stack.** Everyone has one main role on the ladder viewer < engineer < checker < approver < admin and may
hold extra roles beside it (data steward, spares and service, quality and purchase). An admin holds every role.

**Kinds of account.** A *person* signs in normally. A *vendor* account (an outside designer or supplier) sees
only the parts and files of the projects named on the account, can only read, and stops working after its end
date. A *service account* is for programs and never signs in on a screen.

**Access rules.** A rule limits a folder, project, customer or category to the people and roles listed on it:

* folder: the folder, its sub-folders and their files are hidden from everyone else;
* project: the project and its parts (and their files) are hidden from everyone else;
* customer: lists who may see that customer's *restricted* parts. A restricted part whose customer has no rule is
  seen by admins only, so naming the people is part of restricting a part;
* category: parts whose number starts with that category code (and their files) are hidden from everyone else.

Hidden things answer "not found", the same as things that do not exist. Admins see everything; background work
(no signed-in user) is not filtered. **View-only** people see previews but cannot download files.

The filters are plain SQL fragments with the values written in (numbers, and text with quotes doubled), so they
can be added to any query on SQLite or PostgreSQL without juggling parameters.
"""
from __future__ import annotations

import contextvars
import re
from datetime import date

from . import db
from .web import ROLE_LEVEL as LADDER
from .web import bad, conflict, forbidden, not_found

# extra roles held beside the main one (the main roles are the ladder in web.ROLE_LEVEL)
EXTRA_ROLES = {"data_steward": "Data steward", "spares": "Spares and service", "quality": "Quality and purchase"}
ROLE_NAMES = {"viewer": "Viewer", "engineer": "Engineer", "checker": "Checker", "approver": "Approver",
              "admin": "Administrator", **EXTRA_ROLES}
ROLE_HELP = {
    "viewer": "Search, view and print released drawings.",
    "engineer": "Create parts and drawings, take files for editing, build BOMs, raise changes.",
    "checker": "Everything an engineer does, and review drawings before the approver.",
    "approver": "Everything a checker does, and approve releases, change orders and deviations.",
    "admin": "Everything: users, access, backups, add-ins and settings.",
    "data_steward": "Numbering rules, categories, duplicates and allowing a locked customer part to be reused.",
    "spares": "Find parts by OEM number or customer and build spare lists.",
    "quality": "Report nonconformances and decide what happens to the parts; check corrective actions; FMEA; attach "
               "inspection reports and certificates; send drawing packages to vendors.",
}

KINDS = {"person": "Person", "vendor": "Vendor or outside designer", "service": "Service account (programs)"}
SCOPES = {"folder": "Folder", "project": "Project", "customer": "Customer", "category": "Category"}
CATEGORY = re.compile(r"^[A-Z0-9]{2,4}$")

current: contextvars.ContextVar[dict | None] = contextvars.ContextVar("kosha_user", default=None)
_cache: contextvars.ContextVar[tuple | None] = contextvars.ContextVar("kosha_scope", default=None)


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _ids(ids) -> str:
    return ", ".join(str(int(i)) for i in ids)


# ------------------------------------------------------------------- roles --

def extra_roles(user_id: int) -> list[str]:
    return [r["role"] for r in db.rows("SELECT role FROM user_roles WHERE user_id = ? ORDER BY role", (user_id,))]


def has_role(user: dict | None, role: str) -> bool:
    """Whether the user may act in ``role``: the main role is at least that high on the ladder, or the user holds
    it as an extra role. Admins hold every role."""
    if not user:
        return False
    main = LADDER.get(user.get("role"), -1)
    if user.get("kind") == "service":           # programs propose; releasing and approving need a person
        main = min(main, LADDER["engineer"])
        return role in LADDER and main >= LADDER[role]
    if role in LADDER:
        return main >= LADDER[role]
    if main >= LADDER["admin"]:
        return True
    roles = user.get("roles")
    if roles is None:
        roles = extra_roles(user["id"])
    return role in roles


def set_roles(user_id: int, roles: list) -> list[str]:
    if not isinstance(roles, list) or any(r not in EXTRA_ROLES for r in roles):
        raise bad(f"Extra roles are: {', '.join(EXTRA_ROLES)}.")
    with db.tx():
        db.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        for r in sorted(set(roles)):
            db.insert("user_roles", {"user_id": user_id, "role": r})
    return sorted(set(roles))


def set_projects(user_id: int, project_ids: list) -> list[int]:
    if not isinstance(project_ids, list) or not all(isinstance(p, int) for p in project_ids):
        raise bad("Projects must be a list of project ids.")
    found = {r["id"] for r in db.rows(f"SELECT id FROM projects WHERE id IN ({_ids(project_ids) or 0})")}
    if set(project_ids) - found:
        raise bad("One of the projects does not exist.")
    with db.tx():
        db.execute("DELETE FROM user_projects WHERE user_id = ?", (user_id,))
        for p in sorted(found):
            db.insert("user_projects", {"user_id": user_id, "project_id": p})
    return sorted(found)


def check_account_fields(body: dict) -> dict:
    """kind, view_only, expires_on and company from an admin's user form, checked."""
    out: dict = {}
    if "kind" in body:
        if body["kind"] not in KINDS:
            raise bad("The kind of account is a person, a vendor or a service account.")
        out["kind"] = body["kind"]
    if "view_only" in body:
        out["view_only"] = 1 if body["view_only"] in (True, 1, "1", "true") else 0
    if "expires_on" in body:
        v = (body["expires_on"] or "").strip() or None
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise bad("Write the end date as YYYY-MM-DD, for example 2027-03-31.")
        out["expires_on"] = v
    if "company" in body:
        out["company"] = " ".join(str(body["company"] or "").split())[:120]
    return out


def sign_in_problem(u: dict) -> str:
    """Why this account may not be used now ("" when it may)."""
    if u.get("kind") == "service":
        return "This is a service account for programs; it cannot sign in on a screen."
    if u.get("expires_on") and u["expires_on"] < date.today().isoformat():
        return (f"This account ended on {u['expires_on']}. Ask your contact at the company to extend it; "
                "nothing was changed.")
    if u.get("kind") == "vendor" and not db.scalar("SELECT 1 FROM user_projects WHERE user_id = ?", (u["id"],)):
        return "This vendor account has no project yet. Ask your contact at the company to add one."
    return ""


# ------------------------------------------------------------------- rules --

def rules() -> list[dict]:
    out = db.rows("SELECT * FROM access_rules WHERE deleted = 0 ORDER BY scope, value")
    members = db.rows("SELECT m.rule_id, m.member FROM access_rule_members m JOIN access_rules r ON r.id = m.rule_id "
                      "WHERE r.deleted = 0 ORDER BY m.member")
    names = {r["id"]: r["full_name"] for r in db.rows("SELECT id, full_name FROM users")}
    for r in out:
        r["members"] = [m["member"] for m in members if m["rule_id"] == r["id"]]
        r["member_names"] = [_member_name(m, names) for m in r["members"]]
        r["label"] = _scope_label(r["scope"], r["value"])
    return out


def _member_name(m: str, names: dict) -> str:
    kind, _, v = m.partition(":")
    if kind == "user":
        return names.get(int(v), f"user {v}")
    return ROLE_NAMES.get(v, v) + " (role)"


def _scope_label(scope: str, value: str) -> str:
    if scope == "folder":
        from .lifecycle import folder_path
        return "/".join(f["name"] for f in folder_path(int(value))) or f"folder {value}"
    if scope == "project":
        p = db.one("SELECT code, name FROM projects WHERE id = ?", (int(value),))
        return f"{p['code']} {p['name']}".strip() if p else f"project {value}"
    return value


def _check_members(members) -> list[str]:
    if not isinstance(members, list) or not members:
        raise bad("Name at least one person or role who may see it.")
    out = []
    for m in members:
        kind, _, v = str(m).partition(":")
        if kind == "role" and (v in LADDER or v in EXTRA_ROLES):
            out.append(f"role:{v}")
        elif kind == "user" and v.isdigit() and db.scalar("SELECT 1 FROM users WHERE id = ?", (int(v),)):
            out.append(f"user:{int(v)}")
        else:
            raise bad(f"'{m}' is not a person or a role.")
    return sorted(set(out))


def _check_value(scope: str, value) -> str:
    value = " ".join(str(value or "").split())
    if scope not in SCOPES:
        raise bad("A rule limits a folder, a project, a customer or a category.")
    if not value:
        raise bad(f"Choose the {scope}.")
    if scope == "folder" and not (value.isdigit() and db.scalar("SELECT 1 FROM folders WHERE id = ?", (int(value),))):
        raise bad("That folder does not exist.")
    if scope == "project" and not (value.isdigit() and db.scalar("SELECT 1 FROM projects WHERE id = ?", (int(value),))):
        raise bad("That project does not exist.")
    if scope == "category":
        value = value.upper()
        if not CATEGORY.match(value):
            raise bad("A category code is 2 to 4 capital letters or digits, for example SHF.")
    return value


def add_rule(user_id: int, body: dict) -> dict:
    scope = body.get("scope")
    value = _check_value(scope, body.get("value"))
    members = _check_members(body.get("members"))
    if any(r["scope"] == scope and r["value"].lower() == value.lower() for r in
           db.rows("SELECT scope, value FROM access_rules WHERE deleted = 0 AND scope = ?", (scope,))):
        raise conflict(f"There is already a rule for this {scope}; change that one.")
    with db.tx():
        rid = db.insert("access_rules", {"scope": scope, "value": value, "note": str(body.get("note") or "")[:300],
                                         "created_by": user_id, "created_at": db.now(), "updated_at": db.now()})
        for m in members:
            db.insert("access_rule_members", {"rule_id": rid, "member": m})
        db.audit(user_id, "access_rule_add", "access_rule", rid, f"{scope} {value}: {', '.join(members)}")
    return next(r for r in rules() if r["id"] == rid)


def update_rule(user_id: int, rule_id: int, body: dict) -> dict:
    r = db.one("SELECT * FROM access_rules WHERE id = ? AND deleted = 0", (rule_id,))
    if not r:
        raise not_found("This rule was removed.")
    members = _check_members(body.get("members"))
    with db.tx():
        db.execute("DELETE FROM access_rule_members WHERE rule_id = ?", (rule_id,))
        for m in members:
            db.insert("access_rule_members", {"rule_id": rule_id, "member": m})
        upd = {"updated_at": db.now()}
        if "note" in body:
            upd["note"] = str(body.get("note") or "")[:300]
        db.update("access_rules", rule_id, upd)
        db.audit(user_id, "access_rule_change", "access_rule", rule_id, f"{r['scope']} {r['value']}: {', '.join(members)}")
    return next(x for x in rules() if x["id"] == rule_id)


def remove_rule(user_id: int, rule_id: int) -> None:
    r = db.one("SELECT * FROM access_rules WHERE id = ? AND deleted = 0", (rule_id,))
    if not r:
        raise not_found("This rule was already removed.")
    with db.tx():
        db.execute("UPDATE access_rules SET deleted = 1, updated_at = ? WHERE id = ?", (db.now(), rule_id))
        db.audit(user_id, "access_rule_remove", "access_rule", rule_id, f"{r['scope']} {r['value']}")


# ------------------------------------------------------------------- scope --

class Scope:
    """What one user may see, as SQL fragments. Built once per request."""

    def __init__(self, user: dict | None):
        self.user = user
        self.all = user is None or LADDER.get(user.get("role"), -1) >= LADDER["admin"]
        self.vendor = bool(user) and user.get("kind") == "vendor"
        self.view_only = bool(user) and bool(user.get("view_only"))
        self.hidden_folders: set[int] = set()
        self._item_sql = ""
        self.my_projects: list[int] = []
        self.hidden_projects: list[int] = []
        if self.all:
            return
        uid = user["id"]
        mine = {f"user:{uid}", f"role:{user['role']}"} | {f"role:{r}" for r in (user.get("roles") or extra_roles(uid))}
        mine |= {f"role:{r}" for r, lvl in LADDER.items() if lvl <= LADDER.get(user["role"], -1)}
        allowed: dict[str, set[str]] = {}
        denied: dict[str, set[str]] = {}
        members: dict[int, set[str]] = {}
        for m in db.rows("SELECT m.rule_id, m.member FROM access_rule_members m JOIN access_rules r ON r.id = m.rule_id "
                         "WHERE r.deleted = 0"):
            members.setdefault(m["rule_id"], set()).add(m["member"])
        for r in db.rows("SELECT id, scope, value FROM access_rules WHERE deleted = 0"):
            ok = bool(members.get(r["id"], set()) & mine)
            (allowed if ok else denied).setdefault(r["scope"], set()).add(r["value"])
        # folders: a limited folder hides everything below it, unless a rule further down lets this person in
        if denied.get("folder"):
            kids: dict[int | None, list[int]] = {}
            for f in db.rows("SELECT id, parent_id FROM folders"):
                kids.setdefault(f["parent_id"], []).append(f["id"])
            let_in = {int(v) for v in allowed.get("folder", set())}
            stack = [int(v) for v in denied["folder"]]
            while stack:
                fid = stack.pop()
                if fid in self.hidden_folders or fid in let_in:
                    continue
                self.hidden_folders.add(fid)
                stack.extend(kids.get(fid, []))
        self.hidden_projects = sorted(int(v) for v in denied.get("project", set()))
        my_customers = sorted(v.lower() for v in allowed.get("customer", set()))
        conds = ["(i.restricted = 1 AND LOWER(i.customer) NOT IN (" + (", ".join(_q(c) for c in my_customers) or "''")
                 + "))"]
        for code in sorted(denied.get("category", set())):
            conds.append(f"i.part_number LIKE {_q(code + '-%')}")
        if self.hidden_projects:
            conds.append(f"i.id IN (SELECT item_id FROM project_items WHERE project_id IN ({_ids(self.hidden_projects)}))")
        self._item_sql = " OR ".join(conds)
        if self.vendor:
            self.my_projects = [r["project_id"] for r in db.rows(
                "SELECT project_id FROM user_projects WHERE user_id = ? ORDER BY project_id", (uid,))]

    # -- SQL fragments, each starting with " AND " (or empty when nothing is limited)
    def items(self, alias: str = "i") -> str:
        """Limits a query on items (aliased ``alias``) to what this user may see."""
        if self.all:
            return ""
        out = f" AND {alias}.id NOT IN (SELECT i.id FROM items i WHERE {self._item_sql})"
        if self.vendor:
            out += (f" AND {alias}.id IN (SELECT item_id FROM project_items WHERE project_id IN "
                    f"({_ids(self.my_projects) or 0}))")
        return out

    def documents(self, alias: str = "d") -> str:
        """Limits a query on documents (aliased ``alias``) to what this user may see."""
        if self.all:
            return ""
        out = ""
        if self.hidden_folders:
            out += f" AND {alias}.folder_id NOT IN ({_ids(sorted(self.hidden_folders))})"
        out += (f" AND ({alias}.item_id IS NULL OR {alias}.item_id NOT IN "
                f"(SELECT i.id FROM items i WHERE {self._item_sql}))")
        if self.vendor:
            out += (f" AND {alias}.item_id IN (SELECT item_id FROM project_items WHERE project_id IN "
                    f"({_ids(self.my_projects) or 0}))")
        return out

    def folders(self, alias: str = "f") -> str:
        if self.all or not self.hidden_folders:
            return ""
        return f" AND {alias}.id NOT IN ({_ids(sorted(self.hidden_folders))})"

    def projects(self, alias: str = "p") -> str:
        if self.all:
            return ""
        if self.vendor:
            return f" AND {alias}.id IN ({_ids(self.my_projects) or 0})"
        return f" AND {alias}.id NOT IN ({_ids(self.hidden_projects)})" if self.hidden_projects else ""

    # -- single records
    def can_see_item(self, item_id: int | None) -> bool:
        if self.all or item_id is None:
            return True
        return bool(db.scalar("SELECT 1 FROM items i0 WHERE i0.id = ?" + self.items("i0"), (item_id,)))

    def can_see_document(self, doc: dict) -> bool:
        if self.all:
            return True
        if doc["folder_id"] in self.hidden_folders:
            return False
        if self.vendor and not doc.get("item_id"):
            return False
        return self.can_see_item(doc.get("item_id"))

    def can_see_folder(self, folder_id: int) -> bool:
        return self.all or folder_id not in self.hidden_folders

    def can_see_project(self, project_id: int) -> bool:
        if self.all:
            return True
        if self.vendor:
            return project_id in self.my_projects
        return project_id not in self.hidden_projects


def scope(user: dict | None = None) -> Scope:
    """The scope of ``user``, or of the signed-in user of this request (unlimited outside a request)."""
    u = user if user is not None else current.get()
    if u is None:
        return Scope(None)
    cached = _cache.get()
    if cached is not None and cached[0] is u:
        return cached[1]
    s = Scope(u)
    _cache.set((u, s))
    return s


def check_item(item_id: int | None) -> None:
    if not scope().can_see_item(item_id):
        raise not_found("Item not found")


def check_document(doc: dict) -> None:
    if not scope().can_see_document(doc):
        raise not_found("File not found")


def check_download() -> None:
    if scope().view_only:
        raise forbidden("You have view-only access: you can look at the preview but not download the file. "
                        "Ask your admin if you need the file itself.")
    from .twostep import count_download
    count_download(current.get())       # the bulk-download alarm (specification 15.3)


# ------------------------------------------------------------ access review --

def review() -> dict:
    """Who can see what, for the design head to confirm every quarter (specification 15.2)."""
    users = db.rows("SELECT id, username, full_name, role, kind, view_only, expires_on, company, active, created_at "
                    "FROM users ORDER BY active DESC, full_name")
    extra: dict[int, list[str]] = {}
    for r in db.rows("SELECT user_id, role FROM user_roles ORDER BY role"):
        extra.setdefault(r["user_id"], []).append(r["role"])
    projs: dict[int, list[str]] = {}
    for r in db.rows("SELECT up.user_id, p.code FROM user_projects up JOIN projects p ON p.id = up.project_id "
                     "ORDER BY p.code"):
        projs.setdefault(r["user_id"], []).append(r["code"])
    all_rules = rules()
    today = date.today().isoformat()
    for u in users:
        u["roles"] = extra.get(u["id"], [])
        u["projects"] = projs.get(u["id"], [])
        u["sees_limited"] = [f"{SCOPES[r['scope']]} {r['label']}" for r in all_rules
                             if _member_of(u, r["members"])]
        last = db.one("SELECT at FROM audit WHERE user_id = ? AND action = 'login' ORDER BY id DESC LIMIT 1", (u["id"],))
        u["last_sign_in"] = last["at"] if last else None
        notes = []
        if u["active"] and u["expires_on"] and u["expires_on"] < today:
            notes.append("end date passed")
        if u["active"] and u["role"] == "admin":
            notes.append("sees everything")
        if u["active"] and not u["last_sign_in"] and u["kind"] != "service":
            notes.append("never signed in")
        u["check"] = ", ".join(notes)
    return {"generated_at": db.now(), "users": users, "rules": all_rules}


def _member_of(u: dict, members: list[str]) -> bool:
    mine = {f"user:{u['id']}"} | {f"role:{r}" for r in u["roles"]}
    mine |= {f"role:{r}" for r, lvl in LADDER.items() if lvl <= LADDER.get(u["role"], -1)}
    return u["role"] == "admin" or bool(mine & set(members))


def review_csv() -> bytes:
    import csv
    import io
    r = review()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "User name", "Kind", "Main role", "Extra roles", "View only", "Company", "Projects",
                "Limited areas they can see", "End date", "Active", "Last sign-in", "Please check"])
    for u in r["users"]:
        w.writerow([u["full_name"], u["username"], KINDS.get(u["kind"], u["kind"]), ROLE_NAMES.get(u["role"], u["role"]),
                    "; ".join(ROLE_NAMES.get(x, x) for x in u["roles"]), "yes" if u["view_only"] else "",
                    u["company"], "; ".join(u["projects"]), "; ".join(u["sees_limited"]), u["expires_on"] or "",
                    "yes" if u["active"] else "no", u["last_sign_in"] or "never", u["check"]])
    return ("﻿" + buf.getvalue()).encode("utf-8")
