# SPDX-License-Identifier: AGPL-3.0-or-later
"""Auth, users, settings, audit, numbering, search, dashboard."""
from __future__ import annotations

import re

from .. import access, apisafety, approval, db, security, sessions, signin
from ..lifecycle import STATE_LABEL, doc_summary, user_names
from ..web import ROLE_LEVEL, Req, api, as_bool, bad, conflict, not_found

USER_FIELDS = ("id, username, full_name, email, role, ui_mode, ui_theme, active, created_at, kind, view_only, expires_on, "
               "company, CASE WHEN totp_secret IS NULL THEN 0 ELSE 1 END AS two_step_on, downloads_paused_at")
# Themes of the web client (docs/themes.md). A user's ui_theme may also be "": the installation's default_theme.
THEMES = ("system", "light", "dark", "contrast")


@api("POST", "/api/auth/login", role=None, auth=False)
def login(req: Req):
    """Sign in with user name and password; returns a token to send as `Authorization: Bearer <token>`.

    Failed sign-ins are throttled per user name and per address (see Admin › Sign-in & sessions)."""
    r = signin.sign_in(req)
    if "two_step" in r:     # the password was right; send the code next (POST /api/auth/two-step)
        return r
    return _signed_in_answer(r)


def _signed_in_answer(r: dict) -> dict:
    out = {"token": r["token"], "user": r["user"], "erp_name": erp_name(), "default_theme": default_theme()}
    if "recovery_codes" in r:
        out["recovery_codes"] = r["recovery_codes"]
    return out


@api("POST", "/api/auth/two-step", role=None, auth=False)
def login_two_step(req: Req):
    """Second step of signing in: ``challenge`` from the first answer and ``code`` from the authenticator app (or a
    recovery code)."""
    return _signed_in_answer(signin.second_step(req))


@api("POST", "/api/auth/two-step/setup", role=None, auth=False)
def login_two_step_setup(req: Req):
    """For a person who must use two-step sign-in but has not set it up: the key to add to their app."""
    return signin.setup_during_sign_in(req)


@api("POST", "/api/auth/two-step/setup/confirm", role=None, auth=False)
def login_two_step_confirm(req: Req):
    """The first code from the app switches two-step sign-in on and signs in; ``recovery_codes`` are shown once."""
    return _signed_in_answer(signin.confirm_during_sign_in(req))


def erp_name() -> str:
    """Short name of the ERP behind the active connector add-in, for screen text."""
    from .. import erp
    from ..addins import registry
    try:
        a = registry.get(erp.connector_id())
    except Exception:  # no connector installed: generic wording
        return "ERP"
    return a.manifest.name.replace(" connector", "") if a and a.manifest else "ERP"


def default_theme() -> str:
    """The installation's theme for people who have not chosen one (Admin › Users)."""
    t = db.get_setting("default_theme")
    return t if t in THEMES else "system"


def _check_theme(theme, allow_default: bool) -> str:
    if theme == "" and allow_default:
        return theme
    if theme not in THEMES:
        raise bad("Theme must be system (same as the computer), light, dark or contrast"
                  + (", or empty for the installation's default." if allow_default else "."), "bad_theme")
    return theme


@api("GET", "/api/auth/me")
def me(req: Req):
    return {"user": req.user, "erp_name": erp_name(), "default_theme": default_theme()}


@api("POST", "/api/auth/mode")
def set_mode(req: Req):
    mode = req.b("mode")
    if mode not in ("guided", "expert"):
        raise bad("Mode must be guided or expert")
    db.execute("UPDATE users SET ui_mode = ? WHERE id = ?", (mode, req.uid))
    return {"mode": mode}


@api("POST", "/api/auth/theme")
def set_theme(req: Req):
    """Choose your own theme (`theme`): system (same as your computer), light, dark or contrast; "" follows the
    installation's default. Returns the stored `theme` and the installation's `default_theme`."""
    theme = _check_theme(req.b("theme"), allow_default=True)
    db.execute("UPDATE users SET ui_theme = ? WHERE id = ?", (theme, req.uid))
    return {"theme": theme, "default_theme": default_theme()}


@api("POST", "/api/auth/password")
def change_password(req: Req):
    """Change your own password (`current`, `new`). Ends all your sessions and returns a new `token` for this one."""
    return signin.change_own_password(req)


# ------------------------------------------------------------------ users --

@api("GET", "/api/users")
def list_users(req: Req):
    users = db.rows(f"SELECT {USER_FIELDS} FROM users ORDER BY full_name")
    _with_access(users)
    return {"users": users}


def _with_access(users: list[dict]) -> list[dict]:
    roles: dict[int, list[str]] = {}
    for r in db.rows("SELECT user_id, role FROM user_roles ORDER BY role"):
        roles.setdefault(r["user_id"], []).append(r["role"])
    projs: dict[int, list[int]] = {}
    for r in db.rows("SELECT user_id, project_id FROM user_projects ORDER BY project_id"):
        projs.setdefault(r["user_id"], []).append(r["project_id"])
    for u in users:
        u["roles"] = roles.get(u["id"], [])
        u["projects"] = projs.get(u["id"], [])
    return users


def _one_user(uid: int) -> dict:
    return _with_access([db.one(f"SELECT {USER_FIELDS} FROM users WHERE id = ?", (uid,))])[0]


def _set_access(uid: int, body: dict) -> list[str]:
    """Extra roles and vendor projects from the admin's form; returns what changed, for the audit log."""
    changed = []
    if isinstance(body.get("roles"), list) or "roles" in body:
        access.set_roles(uid, body.get("roles"))
        changed.append("roles")
    if isinstance(body.get("projects"), list) or "projects" in body:
        access.set_projects(uid, body.get("projects"))
        changed.append("projects")
    return changed


@api("POST", "/api/users", role="admin")
def create_user(req: Req):
    username = (req.b("username") or "").strip()
    if not re.match(r"^[\w.\-\\@]{2,64}$", username):
        raise bad("User name may use letters, digits, dot, dash and underscore.")
    role = req.b("role") or "engineer"
    if role not in ROLE_LEVEL:
        raise bad("Unknown role")
    pw = signin.check_new_password(req.b("password"), username)
    if db.one("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)):
        raise conflict("That user name is taken.")
    body = req.body if isinstance(req.body, dict) else {}
    extra = access.check_account_fields(body)
    with db.tx():
        uid = db.insert("users", {"username": username, "full_name": (req.b("full_name") or username).strip(),
                                  "email": req.b("email") or "", "role": role,
                                  "password_hash": security.hash_password(pw), "ui_mode": req.b("ui_mode") or "guided",
                                  "ui_theme": _check_theme(req.b("ui_theme") or "", allow_default=True),
                                  "created_at": db.now(), **extra})
        _set_access(uid, body)
        db.audit(req.uid, "user_create", "user", uid, f"{username} ({role}{', ' + extra['kind'] if 'kind' in extra else ''})")
    return {"user": _one_user(uid)}


@api("PATCH", "/api/users/{id:int}", role="admin")
def update_user(req: Req):
    uid = req.int_param("id")
    if not db.one("SELECT id FROM users WHERE id = ?", (uid,)):
        raise not_found("User not found")
    upd = {}
    for k in ("full_name", "email", "role", "ui_mode", "ui_theme"):
        v = req.b(k)
        if v is not None:
            if k == "role" and v not in ROLE_LEVEL:
                raise bad("Unknown role")
            if k == "ui_theme":
                _check_theme(v, allow_default=True)
            upd[k] = v
    body = req.body if isinstance(req.body, dict) else {}
    upd.update(access.check_account_fields(body))
    if uid == req.uid and (upd.get("role", "admin") != "admin" or upd.get("kind", "person") != "person"
                           or upd.get("view_only") or upd.get("expires_on")):
        raise bad("You can't take away your own admin access; ask another admin.")
    if req.b("active") is not None:
        if uid == req.uid and not as_bool(req.b("active")):
            raise bad("You can't deactivate yourself.")
        upd["active"] = 1 if as_bool(req.b("active")) else 0
    if req.b("password"):
        name = db.scalar("SELECT username FROM users WHERE id = ?", (uid,))
        upd["password_hash"] = security.hash_password(signin.check_new_password(req.b("password"), name))
    # a new password or a deactivation ends every session of that user at once
    reason = ("password reset by an admin" if "password_hash" in upd else
              "deactivated" if upd.get("active") == 0 else "")
    with db.tx():
        if upd:
            db.update("users", uid, upd)
        changed = [k if k != "password_hash" else "password" for k in upd] + _set_access(uid, body)
        ended = sessions.end_all(uid, reason) if reason else 0
        db.audit(req.uid, "user_update", "user", uid, ",".join(changed) + (f"; {ended} session(s) ended" if ended else ""))
    return {"user": _one_user(uid)}


# --------------------------------------------------------------- settings --

PUBLIC_SETTINGS = ("part_number_regex", "revision_skip_letters", "first_revision", "require_eco_for_release",
                   "company_name", "default_theme", "revision_scheme", "four_eyes_categories",
                   "allow_self_approval_with_reason", "obsolete_needs_change_order", "api_rate_limit_per_minute", "recycle_bin_days")
BOOL_SETTINGS = ("require_eco_for_release", "allow_self_approval_with_reason", "obsolete_needs_change_order")


@api("GET", "/api/settings")
def get_settings(req: Req):
    from ..cad import dwg
    out = {k: db.get_setting(k) for k in PUBLIC_SETTINGS}
    out["dwg_converter"] = dwg.converter_status()
    return out


@api("PUT", "/api/settings", role="admin")
def put_settings(req: Req):
    for k in sorted(PUBLIC_SETTINGS, key=lambda k: k != "revision_scheme"):    # the scheme sets the first revision
        if isinstance(req.body, dict) and k in req.body:
            v = req.body[k]
            if k == "part_number_regex":
                try:
                    re.compile(v)
                except re.error as e:
                    raise bad(f"Part number pattern is not valid: {e}")
            if k in BOOL_SETTINGS:
                v = as_bool(v)
            if k == "revision_scheme":
                if v not in ("letters", "numeric"):
                    raise bad("The revision scheme is letters (A, B, C) or numeric (R0, R1, R2).")
                if v != db.get_setting(k):
                    if db.scalar("SELECT 1 FROM lifecycle_events WHERE to_state = 'released' LIMIT 1"):
                        raise bad("Kosha already has released revisions. The revision scheme is chosen once, before "
                                  "go-live, so the shop floor's prints stay in order.")
                    db.set_setting("first_revision", "R0" if v == "numeric" else "A")
                    req.body.pop("first_revision", None)     # the scheme decides where revisions start
            if k == "four_eyes_categories":
                if isinstance(v, str):
                    v = [c for c in re.split(r"[\s,]+", v) if c]
                if not isinstance(v, list) or not all(isinstance(c, str) and re.match(r"^[A-Za-z0-9]{1,6}$", c) for c in v):
                    raise bad("List category codes such as ROT, PRV; leave it empty for every category.")
                v = sorted({c.upper() for c in v})
            if k == "recycle_bin_days":
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    v = 0
                if not 1 <= v <= 3650:
                    raise bad("Keep deleted files in the recycle bin for 1 to 3650 days.")
            if k == "default_theme":
                _check_theme(v, allow_default=False)
            if k == "api_rate_limit_per_minute":
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    v = -1
                if not 0 <= v <= 1_000_000:
                    raise bad("The API limit is a number of calls per minute; 0 switches it off.")
                if v and v < 60:
                    raise bad("Allow at least 60 calls a minute, or the browser itself would be refused.")
            db.set_setting(k, v)
            if k == "api_rate_limit_per_minute":
                from ..apisafety import forget_rate_setting
                forget_rate_setting()
    db.audit(req.uid, "settings", "settings", None, "")
    return get_settings(req)


@api("GET", "/api/audit", role="approver")
def audit(req: Req):
    paged, after, limit = apisafety.cursor_args(req, 200)
    params: list = []
    where = []
    if after is not None:
        where.append("id < ?")
        params.append(after)
    if req.q("entity"):
        where.append("entity = ?")
        params.append(req.q("entity"))
    if req.q("entity_id"):
        where.append("entity_id = ?")
        params.append(int(req.q("entity_id")))
    sql = "SELECT * FROM audit" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    names = user_names()
    rows = [{**r, "user": names.get(r["user_id"], "system")} for r in
            db.rows(sql, [*params, limit + 1 if paged else limit])]
    if paged:
        rows, nxt = apisafety.next_cursor(rows, limit)
        return {"entries": rows, "next_cursor": nxt}
    return {"entries": rows}


# -------------------------------------------------------------- numbering --

@api("POST", "/api/numbering/next", role="engineer")
def next_number(req: Req):
    """The next number of a series such as MX-100- (the CAD add-ins). Recorded in the numbering ledger, so a number
    is never handed out twice, even one reserved without a part."""
    from ..numbering import NumberingService
    prefix = (req.b("prefix") or "").strip().upper()
    if not re.match(r"^[A-Z0-9][A-Z0-9\-_/]{0,20}$", prefix):
        raise bad("Prefix may use letters, digits and dashes, e.g. MX-100-")
    try:
        width = int(req.b("width") or 3)
    except (TypeError, ValueError):
        raise bad("Width is the number of digits, from 1 to 9.") from None
    if not 1 <= width <= 9:
        raise bad("Width is the number of digits, from 1 to 9.")
    return {"part_number": NumberingService().next_in_series(prefix, width, req.uid)}


# ----------------------------------------------------------------- search --

def _with_preview(rows: list[dict], names: dict) -> list[dict]:
    out = []
    for d in rows:
        s = doc_summary(d, names)
        s["preview_kind"] = d.get("preview_kind")
        s["folder_name"] = d.get("folder_name")
        out.append(s)
    return out


DOC_SELECT = ("SELECT d.*, v.preview_kind, f.name AS folder_name FROM documents d "
              "LEFT JOIN versions v ON v.document_id = d.id AND v.version = d.latest_version "
              "LEFT JOIN folders f ON f.id = d.folder_id ")


@api("GET", "/api/search")
def search(req: Req):
    q = (req.q("q") or "").strip()
    if not q:
        return {"documents": [], "items": [], "changes": []}
    limit = min(int(req.q("limit", 50)), 200)
    terms = [t for t in q.split() if t]
    where = []
    params: list = []
    for t in terms:
        where.append("(d.filename LIKE ? OR d.part_number LIKE ? OR d.description LIKE ?)")
        params += [f"%{t}%"] * 3
    docs = db.rows(DOC_SELECT + "WHERE d.deleted = 0" + access.scope().documents() + " AND " + " AND ".join(where) +
                   " ORDER BY d.updated_at DESC LIMIT ?", [*params, limit])
    iw, ip = [], []
    for t in terms:
        # an OEM part number, OEM machine model or the customer's own part number finds the part too
        iw.append("(part_number LIKE ? OR description LIKE ? OR material LIKE ? OR customer_part_number LIKE ? "
                  "OR id IN (SELECT item_id FROM item_oem_refs WHERE deleted = 0 AND (oem_part_number LIKE ? "
                  "OR oem_model LIKE ?)))")
        ip += [f"%{t}%"] * 6
    items = db.rows("SELECT * FROM items i WHERE " + " AND ".join(iw) + access.scope().items() +
                    " ORDER BY part_number LIMIT ?", [*ip, limit])
    changes = [] if access.scope().vendor else db.rows("SELECT id, number, title, state FROM change_orders WHERE number LIKE ? OR title LIKE ? "
                      "ORDER BY id DESC LIMIT 20", (f"%{q}%", f"%{q}%"))
    return {"documents": _with_preview(docs, user_names()), "items": items, "changes": changes}


# -------------------------------------------------------------- dashboard --

@api("GET", "/api/dashboard")
def dashboard(req: Req):
    names = user_names()
    uid = req.uid
    mine = db.rows(DOC_SELECT + "WHERE d.deleted = 0" + access.scope().documents() + " AND d.checked_out_by = ? ORDER BY d.checked_out_at DESC", (uid,))
    covering = approval.standing_in_for(uid)
    who = [uid, *covering]
    approvals = db.rows(
        "SELECT DISTINCT c.id, c.number, c.title, c.priority, c.submitted_at, c.requested_by, a.user_id AS for_id "
        "FROM change_orders c JOIN co_approvers a ON a.co_id = c.id WHERE a.user_id IN (" + ",".join("?" * len(who)) +
        ") AND a.decision = 'pending' AND c.state = 'submitted' ORDER BY c.submitted_at", who)
    seen: set[int] = set()
    approvals = [a for a in sorted(approvals, key=lambda a: a["for_id"] != uid)
                 if not (a["id"] in seen or seen.add(a["id"]))]
    approvals.sort(key=lambda a: a["submitted_at"] or "")
    for a in approvals:
        a["requested_by_name"] = names.get(a["requested_by"], "?")
        a["for_name"] = names.get(a["for_id"]) if a["for_id"] != uid else None
    in_review = []
    if ROLE_LEVEL[req.user["role"]] >= ROLE_LEVEL["approver"]:
        in_review = _with_preview(db.rows(DOC_SELECT + "WHERE d.deleted = 0" + access.scope().documents() +
                                          " AND d.state = 'in_review' "
                                          "AND d.id NOT IN (SELECT document_id FROM co_items ci JOIN change_orders c "
                                          "ON c.id = ci.co_id WHERE c.state = 'submitted') "
                                          "ORDER BY d.updated_at LIMIT 20"), names)
    released = db.rows(
        "SELECT e.at, e.revision, e.user_id, d.id, d.filename, d.part_number, d.description FROM lifecycle_events e "
        "JOIN documents d ON d.id = e.document_id WHERE e.to_state = 'released' AND d.deleted = 0 "
        "ORDER BY e.id DESC LIMIT 8")
    for r in released:
        r["by"] = names.get(r["user_id"], "?")
    my_changes = db.rows("SELECT id, number, title, state FROM change_orders WHERE requested_by = ? AND state IN "
                         "('draft','submitted','approved','rejected') ORDER BY id DESC LIMIT 6", (uid,))
    erp = {
        "pending": db.scalar("SELECT COUNT(*) FROM erp_jobs WHERE status IN ('pending','running')"),
        "failed": db.scalar("SELECT COUNT(*) FROM erp_jobs WHERE status = 'failed'"),
        "enabled": bool((db.get_setting("erp") or {}).get("enabled")),
        "dry_run": bool((db.get_setting("erp") or {}).get("dry_run")),
    }
    cp_open = db.rows("SELECT g.id, g.label, g.score_min, g.score_max, COUNT(m.document_id) AS n FROM cp_groups g "
                      "JOIN cp_members m ON m.group_id = g.id WHERE g.status = 'open' GROUP BY g.id "
                      "ORDER BY n DESC LIMIT 5")
    counts = {
        "documents": db.scalar("SELECT COUNT(*) FROM documents WHERE deleted = 0"),
        "items": db.scalar("SELECT COUNT(*) FROM items"),
        "boms": db.scalar("SELECT COUNT(*) FROM boms"),
        "released": db.scalar("SELECT COUNT(*) FROM documents WHERE deleted = 0 AND state = 'released'"),
    }
    return {"checked_out": _with_preview(mine, names), "approvals": approvals, "in_review": in_review,
            "recent_releases": released, "my_changes": my_changes, "erp": erp, "common_parts": cp_open,
            "counts": counts, "state_labels": STATE_LABEL}
