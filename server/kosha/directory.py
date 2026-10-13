# SPDX-License-Identifier: AGPL-3.0-or-later
"""Directory sign-in: people sign in with an account they already have (LDAP, Active Directory, OpenID Connect).

The directory itself is reached only through sign-in add-ins (kind ``sign-in``), each call through the add-in
guard with a time-out. This module keeps what must not depend on any one directory:

* **Links.** A directory account is linked to a Kosha user by the directory's stable id (``external_id``),
  never by name. A first sign-in creates a Kosha user (when the admin allows it); it never takes over an
  existing Kosha account that happens to have the same user name. Only an admin links those, by naming
  the directory account; the link is completed at that person's next directory sign-in.
* **Roles.** Directory groups are turned into Kosha roles by the admin's group list (the highest role wins).
  People in none of the listed groups get the default role, or cannot sign in when there is none.
* **The local admin.** The built-in ``admin`` account is never linked, so it signs in with its Kosha password
  even when the directory is down. Other local accounts (not linked) keep working the same way.
* **Redirect round trips** (OpenID Connect): the ``state`` is random, single use and valid for ten minutes;
  the round trip is bound to the browser that started it (a cookie), and the browser receives a one-time
  ticket, never a token in an address.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db, sessions
from .addins import registry
from .web import ROLE_LEVEL, ApiError

from kosha_sdk import AddinError  # noqa: E402
from kosha_sdk.signin import Identity  # noqa: E402

log = logging.getLogger("kosha.directory")

KIND = "sign-in"
SETTING_KEY = "directory_sign_in"
DEFAULTS: dict[str, Any] = {
    "group_roles": [],               # [{"group": "kosha-engineers", "role": "engineer"}, ...]
    "default_role": "",              # role for people in none of the groups; "" = they cannot sign in
    "create_users": True,            # a first directory sign-in creates the Kosha user
    "roles_from_directory": True,    # the role follows the groups at every sign-in
}
EXTERNAL_PASSWORD = "!directory"     # stored as the password of users made by a directory sign-in: never matches
LOCAL_ADMIN = "admin"                # the built-in account that always signs in locally
CALL_TIMEOUT = 20.0                  # seconds for one add-in call during sign-in
DESCRIBE_TIMEOUT = 5.0
REQUEST_MINUTES = 10                 # a redirect round trip must come back within this time
TICKET_SECONDS = 120                 # ... and the browser must redeem its ticket within this time
STARTS_PER_ADDRESS = 30              # redirect sign-ins one network address may start per REQUEST_MINUTES
COOKIE = "kosha_sso"
COOKIE_PATH = "/api/auth/sso"
USERNAME_RE = re.compile(r"^[\w.\-\\@]{2,64}$")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _key(secret: str) -> str:
    return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ settings --

def settings() -> dict:
    stored = db.get_setting(SETTING_KEY) or {}
    out = dict(DEFAULTS)
    out.update({k: stored[k] for k in DEFAULTS if k in stored})
    return out


def save_settings(values: dict) -> dict:
    """Check and store the group list and options. Raises ValueError with a plain message."""
    cfg = settings()
    if "group_roles" in values:
        rows = values["group_roles"]
        if not isinstance(rows, list):
            raise ValueError("Send the groups as a list of {group, role}.")
        clean, seen = [], set()
        for n, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise ValueError(f"Group line {n}: send a group and a role.")
            group = " ".join(str(row.get("group") or "").split())
            role = str(row.get("role") or "")
            if not group:
                continue                      # an empty line in the form
            if len(group) > 500:
                raise ValueError(f"Group line {n}: the group name is too long (at most 500 characters).")
            if role not in ROLE_LEVEL:
                raise ValueError(f"Group line {n} ({group}): choose a role (viewer, engineer, approver or admin).")
            if group.casefold() in seen:
                raise ValueError(f"The group {group} is listed twice. Keep one line for it.")
            seen.add(group.casefold())
            clean.append({"group": group, "role": role})
        cfg["group_roles"] = clean
    if "default_role" in values:
        role = str(values["default_role"] or "")
        if role and role not in ROLE_LEVEL:
            raise ValueError("The role for everyone else must be viewer, engineer, approver, admin or none.")
        cfg["default_role"] = role
    for k in ("create_users", "roles_from_directory"):
        if k in values:
            cfg[k] = bool(values[k])
    db.set_setting(SETTING_KEY, cfg)
    return settings()


def role_for(groups) -> str | None:
    """The Kosha role directory groups give: the highest listed one, else the default role (None: no access)."""
    cfg = settings()
    have = {str(g).casefold() for g in groups or ()}
    best = None
    for row in cfg["group_roles"]:
        if row["group"].casefold() in have and (best is None or ROLE_LEVEL[row["role"]] > ROLE_LEVEL[best]):
            best = row["role"]
    return best or (cfg["default_role"] or None)


# ----------------------------------------------------------------- providers --

@dataclass
class Provider:
    id: str
    name: str
    style: str = ""                 # password | redirect ("" when it could not be asked)
    label: str = ""
    ready: bool = False
    why: str = ""                   # why it is not ready, in plain words

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "style": self.style, "label": self.label, "ready": self.ready,
                "why": self.why}


def providers() -> list[Provider]:
    """Every installed sign-in add-in, and whether it can be used now."""
    out = []
    for a in registry.by_kind(KIND, enabled_only=False):
        m = a.manifest
        if not m:
            continue
        p = Provider(a.id, m.name)
        if a.status != "enabled":
            p.why = a.message or "Switched off under Admin › Add-ins."
            out.append(p)
            continue
        values = registry.settings(a.id)
        missing = [s.label for s in m.settings if s.required and values.get(s.key) in (None, "")]
        if missing:
            p.why = f"Not set up yet: fill in {', '.join(missing)} under Admin › Add-ins › {m.name}."
            out.append(p)
            continue
        try:
            d = registry.call(a.id, "describe", timeout=DESCRIBE_TIMEOUT) or {}
            p.style = str(d.get("style") or "")
            p.label = str(d.get("label") or m.name)
        except AddinError as e:
            p.why = str(e)
            out.append(p)
            continue
        if p.style not in ("password", "redirect"):
            p.why = f"{m.name} does not say whether it checks passwords or sends people to another site."
        else:
            p.ready = True
        out.append(p)
    return out


def provider(addin_id: str, style: str | None = None) -> Provider | None:
    return next((p for p in providers() if p.id == addin_id and (style is None or p.style == style)), None)


def _as_identity(value: Any, name: str) -> Identity:
    """The add-in's answer as an Identity (an add-in built on another copy of the SDK returns a look-alike)."""
    if isinstance(value, Identity):
        return value
    try:
        return Identity(external_id=str(getattr(value, "external_id")), username=str(getattr(value, "username")),
                        full_name=str(getattr(value, "full_name", "") or ""), email=str(getattr(value, "email", "") or ""),
                        groups=tuple(getattr(value, "groups", ()) or ()))
    except Exception:
        raise AddinError(f"{name} gave an answer Kosha cannot read (expected an Identity).") from None


# ------------------------------------------------------------------ links --

def is_linked(user_id: int) -> bool:
    """True when the user signs in through a directory (a completed link). Their Kosha password is then not used."""
    return bool(db.scalar("SELECT COUNT(*) FROM user_identities WHERE user_id = ? AND unlinked_at IS NULL "
                          "AND external_id IS NOT NULL", (user_id,)))


def links(user_id: int | None = None) -> list[dict]:
    """Current links (all, or of one user), with the provider's name."""
    names = {a.id: a.manifest.name for a in registry.by_kind(KIND, enabled_only=False) if a.manifest}
    where, params = ("AND i.user_id = ?", (user_id,)) if user_id is not None else ("", ())
    out = []
    for r in db.rows("SELECT i.id, i.user_id, i.provider, i.external_id, i.directory_username, i.email, i.groups_json, "
                     "i.created_at, i.last_sign_in_at, u.username, u.full_name, u.role, u.active FROM user_identities i "
                     f"JOIN users u ON u.id = i.user_id WHERE i.unlinked_at IS NULL {where} "
                     "ORDER BY u.full_name, i.id", params):
        try:
            groups = json.loads(r.pop("groups_json") or "[]")
        except ValueError:
            groups = []
        out.append({**r, "groups": groups, "pending": r["external_id"] is None,
                    "provider_name": names.get(r["provider"], r["provider"])})
    return out


def link(user_id: int, provider_id: str, directory_username: str, by: int | None) -> dict:
    """Prepare a link of a Kosha user to a directory account (completed at that person's next directory sign-in)."""
    u = db.one("SELECT id, username, full_name FROM users WHERE id = ?", (user_id,))
    if not u:
        raise ApiError(404, "User not found", "not_found")
    if u["username"].casefold() == LOCAL_ADMIN:
        raise ApiError(400, "The built-in admin account stays a local account, so someone can always sign in when "
                            "the directory cannot be reached. Link another admin account instead.", "local_admin")
    a = registry.get(provider_id)
    if not a or not a.manifest or KIND not in a.manifest.kinds:
        raise ApiError(400, "Choose one of the sign-in add-ins listed under Admin › Add-ins.", "bad_request")
    name = " ".join(str(directory_username or "").split())
    if not name or len(name) > 256:
        raise ApiError(400, "Enter the person's user name in the directory (at most 256 characters).", "bad_request")
    with db.tx():
        if db.one("SELECT id FROM user_identities WHERE user_id = ? AND provider = ? AND unlinked_at IS NULL",
                  (user_id, provider_id)):
            raise ApiError(409, f"{u['full_name']} is already linked to {a.manifest.name}. Unlink first to link "
                                "another account.", "conflict")
        taken = db.one("SELECT u.full_name FROM user_identities i JOIN users u ON u.id = i.user_id WHERE i.provider = ? "
                       "AND i.directory_username = ? COLLATE NOCASE AND i.unlinked_at IS NULL", (provider_id, name))
        if taken:
            raise ApiError(409, f"The directory account {name} is already linked to {taken['full_name']}.", "conflict")
        lid = db.insert("user_identities", {"user_id": user_id, "provider": provider_id, "external_id": None,
                                            "directory_username": name, "created_at": db.now(), "created_by": by})
        db.audit(by, "directory_link", "user", user_id,
                 f"{u['username']} to {a.manifest.name} account '{name}' (completed at the next directory sign-in)")
    return next(x for x in links(user_id) if x["id"] == lid)


def unlink(user_id: int, link_id: int, by: int | None) -> int:
    """Remove a link (the row is kept, marked unlinked) and end the user's sessions; returns sessions ended."""
    row = db.one("SELECT i.*, u.username FROM user_identities i JOIN users u ON u.id = i.user_id "
                 "WHERE i.id = ? AND i.user_id = ? AND i.unlinked_at IS NULL", (link_id, user_id))
    if not row:
        raise ApiError(404, "This link does not exist (any more).", "not_found")
    with db.tx():
        db.execute("UPDATE user_identities SET unlinked_at = ?, unlinked_by = ? WHERE id = ?", (db.now(), by, link_id))
        ended = sessions.end_all(user_id, "unlinked from the directory")
        db.audit(by, "directory_unlink", "user", user_id,
                 f"{row['username']} from {row['provider']} account '{row['directory_username']}'; {ended} session(s) ended")
    return ended


# ---------------------------------------------------------- signing people in --

class Refused(ApiError):
    """The directory vouched for the person, but Kosha does not let them in. The message says what to do."""

    def __init__(self, message: str, code: str, user_id: int | None, detail: str):
        super().__init__(403, message, code)
        self.user_id, self.detail = user_id, detail


def _clean_username(name: str) -> str:
    name = name.strip()
    if USERNAME_RE.match(name):
        return name
    name = re.sub(r"[^\w.\-@]+", "-", name).strip("-")[:64]
    return name if USERNAME_RE.match(name) else ""


def complete_sign_in(provider_id: str, ident: Identity, ip: str) -> dict:
    """Find or create the Kosha user for a directory identity and bring name, e-mail and role up to date.

    Returns the user row. Raises Refused (audited) when Kosha does not let the person in."""
    a = registry.get(provider_id)
    pname = a.manifest.name if a and a.manifest else provider_id
    try:
        return _complete(provider_id, pname, ident)
    except Refused as r:
        db.audit(r.user_id, "login_refused", "user", r.user_id,
                 f"{pname} account '{ident.username}' from {ip}: {r.detail}")
        log.info("Directory sign-in refused for %r via %s: %s", ident.username, provider_id, r.detail)
        raise


def _complete(provider_id: str, pname: str, ident: Identity) -> dict:
    cfg = settings()
    groups = list(ident.groups)
    role = role_for(groups)
    no_group = Refused("You signed in to the directory, but you are not in a group that gives access to Kosha. "
                       "Ask your Kosha administrator to add your group under Admin › Sign-in & sessions.",
                       "no_kosha_group", None, f"in none of the listed groups ({', '.join(groups[:10]) or 'no groups'})")
    now = db.now()
    with db.tx():
        row = db.one("SELECT * FROM user_identities WHERE provider = ? AND external_id = ? AND unlinked_at IS NULL",
                     (provider_id, ident.external_id))
        if row is None:     # a link an admin prepared by name, waiting for this first sign-in
            row = db.one("SELECT * FROM user_identities WHERE provider = ? AND external_id IS NULL "
                         "AND directory_username = ? COLLATE NOCASE AND unlinked_at IS NULL",
                         (provider_id, ident.username))
            if row is not None:
                db.execute("UPDATE user_identities SET external_id = ? WHERE id = ?", (ident.external_id, row["id"]))
        if row is not None:
            u = db.one("SELECT * FROM users WHERE id = ?", (row["user_id"],))
            if not u["active"]:
                raise Refused("Your Kosha account is switched off. Ask your Kosha administrator to switch it on.",
                              "account_disabled", u["id"], "Kosha account deactivated")
            if role is None:
                no_group.user_id = u["id"]
                raise no_group
            how = "linked account"
        else:
            if role is None:
                raise no_group
            if not cfg["create_users"]:
                raise Refused("You signed in to the directory, but you have no Kosha account yet. Ask your Kosha "
                              "administrator to add you.", "no_kosha_account", None, "no linked Kosha user")
            username = _clean_username(ident.username)
            if not username:
                raise Refused("Your directory user name cannot be used as a Kosha user name. Ask your Kosha "
                              "administrator to add you and link your account.", "bad_username", None,
                              "user name not usable in Kosha")
            clash = db.one("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,))
            if clash:
                raise Refused(f"A Kosha account with the user name {username} already exists and is not linked to "
                              "your directory account. For your security Kosha does not take it over by itself: ask "
                              "your Kosha administrator to link it (Admin › Users › Directory).", "link_needed",
                              clash["id"], "a local account with this user name exists; an admin must link it")
            uid = db.insert("users", {"username": username, "full_name": (ident.full_name or username).strip()[:200],
                                      "email": ident.email or "", "role": role, "password_hash": EXTERNAL_PASSWORD,
                                      "ui_mode": "guided", "created_at": now})
            db.insert("user_identities", {"user_id": uid, "provider": provider_id, "external_id": ident.external_id,
                                          "directory_username": ident.username[:256], "created_at": now})
            row = db.one("SELECT * FROM user_identities WHERE user_id = ? AND provider = ? AND unlinked_at IS NULL",
                         (uid, provider_id))
            u = db.one("SELECT * FROM users WHERE id = ?", (uid,))
            db.audit(None, "user_create", "user", uid, f"{username} at the first sign-in through {pname} (role {role})")
            how = "new account"
        upd = {}
        if ident.full_name and ident.full_name.strip()[:200] != u["full_name"]:
            upd["full_name"] = ident.full_name.strip()[:200]
        if ident.email and ident.email != (u["email"] or ""):
            upd["email"] = ident.email
        if cfg["roles_from_directory"] and role != u["role"] and u["username"].casefold() != LOCAL_ADMIN:
            upd["role"] = role
            db.audit(None, "user_update", "user", u["id"], f"role {u['role']} → {role} from directory groups ({pname})")
        db.update("users", u["id"], upd)
        db.execute("UPDATE user_identities SET directory_username = ?, email = ?, groups_json = ?, last_sign_in_at = ? "
                   "WHERE id = ?", (ident.username[:256], ident.email or "", json.dumps(groups[:200]), now, row["id"]))
        u = db.one("SELECT * FROM users WHERE id = ?", (u["id"],))
    u["_how"] = how
    return u


@dataclass
class PasswordOutcome:
    provider: Provider | None = None
    identity: Identity | None = None
    asked: int = 0                                  # providers that gave an answer (yes or no)
    unavailable: list[str] = field(default_factory=list)


def check_password(username: str, password: str) -> PasswordOutcome:
    """Ask each ready password provider in turn; stops at the first that knows the person."""
    out = PasswordOutcome()
    if not username or not password:
        return out
    for p in providers():
        if not p.ready or p.style != "password":
            continue
        try:
            found = registry.call(p.id, "authenticate", username, password, timeout=CALL_TIMEOUT)
        except AddinError as e:
            out.unavailable.append(f"{p.name}: {e}")
            log.warning("Sign-in through %s could not be checked: %s", p.id, e)
            continue
        out.asked += 1
        if found is not None:
            try:
                out.provider, out.identity = p, _as_identity(found, p.name)
            except AddinError as e:
                out.unavailable.append(f"{p.name}: {e}")
                continue
            return out
    return out


# ----------------------------------------------------------- redirect round trips --

def begin(provider_id: str, redirect_uri: str, ip: str) -> tuple[str, str]:
    """Start a redirect sign-in. Returns (address of the provider's page, secret for the browser's cookie)."""
    p = provider(provider_id, "redirect")
    if not p or not p.ready:
        raise ApiError(404, "This way of signing in is not available.", "sso_unavailable")
    since = (_now() - timedelta(minutes=REQUEST_MINUTES)).isoformat()
    if (db.scalar("SELECT COUNT(*) FROM sign_in_requests WHERE ip = ? AND created_at > ?", (ip, since)) or 0) \
            >= STARTS_PER_ADDRESS:
        raise ApiError(429, "Too many sign-ins were started from your network address. Wait a few minutes and try "
                            "again.", "sign_in_paused", {"retry_after": REQUEST_MINUTES * 60})
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    start = registry.call(provider_id, "start", redirect_uri, state, timeout=CALL_TIMEOUT)
    url = str(getattr(start, "url", "") or "")
    saved = getattr(start, "saved", None) or {}
    if not url.startswith(("https://", "http://")) or not isinstance(saved, dict):
        raise AddinError(f"{p.name} did not give an address to send you to.")
    with db.tx():
        db.execute("DELETE FROM sign_in_requests WHERE created_at < ?", ((_now() - timedelta(days=1)).isoformat(),))
        db.insert("sign_in_requests", {"state_key": _key(state), "browser_key": _key(browser), "provider": provider_id,
                                       "redirect_uri": redirect_uri, "saved_json": json.dumps(saved), "ip": ip[:64],
                                       "created_at": _now().isoformat()})
    return url, browser


def returned(provider_id: str, params: dict, browser: str, ip: str) -> str:
    """The browser came back from the provider. Returns a one-time ticket the sign-in page redeems.

    Raises ApiError with code ``sso_expired`` when the answer cannot be tied to a sign-in started here; every
    other problem is kept with the ticket, so the sign-in page can show the message."""
    state = str(params.get("state") or "")
    with db.tx():
        row = db.one("SELECT * FROM sign_in_requests WHERE state_key = ? AND provider = ?", (_key(state), provider_id)) \
            if state else None
        too_old = row is not None and \
            datetime.fromisoformat(row["created_at"]) < _now() - timedelta(minutes=REQUEST_MINUTES)
        if row is None or row["returned_at"] or too_old or not secrets.compare_digest(row["browser_key"], _key(browser)):
            raise ApiError(400, "This sign-in has expired or was started in another browser.", "sso_expired")
        db.execute("UPDATE sign_in_requests SET returned_at = ?, saved_json = '{}' WHERE id = ?",
                   (_now().isoformat(), row["id"]))
    saved = json.loads(row["saved_json"] or "{}")
    user_id, error = None, ""
    p = provider(provider_id, "redirect")
    try:
        if not p or not p.ready:
            raise AddinError("This way of signing in has been switched off.")
        found = registry.call(provider_id, "finish", row["redirect_uri"], dict(params), saved, timeout=CALL_TIMEOUT)
        ident = _as_identity(found, p.name)
        user_id = complete_sign_in(provider_id, ident, ip)["id"]
    except Refused as r:
        error = r.message
    except AddinError as e:
        error = f"{p.name if p else 'The sign-in'} could not sign you in: {e} Your work is safe. Try again, or sign " \
                "in with your Kosha user name and password if you have one."
        db.audit(None, "login_failed", "user", None, f"{provider_id} from {ip}: {e}")
    ticket = secrets.token_urlsafe(32)
    db.execute("UPDATE sign_in_requests SET ticket_key = ?, user_id = ?, error = ? WHERE id = ?",
               (_key(ticket), user_id, error[:1000], row["id"]))
    return ticket


def redeem(ticket: str, browser: str, ip: str, user_agent: str) -> tuple[dict, str]:
    """Exchange a ticket for a session. Returns (user row, token)."""
    with db.tx():
        row = db.one("SELECT * FROM sign_in_requests WHERE ticket_key = ?", (_key(ticket),)) if ticket else None
        fresh = row is not None and row["returned_at"] and \
            datetime.fromisoformat(row["returned_at"]) >= _now() - timedelta(seconds=TICKET_SECONDS)
        if row is None or row["used_at"] or not fresh or not secrets.compare_digest(row["browser_key"], _key(browser)):
            raise ApiError(400, "This sign-in has expired or was started in another browser. Please sign in again.",
                           "sso_expired")
        db.execute("UPDATE sign_in_requests SET used_at = ? WHERE id = ?", (_now().isoformat(), row["id"]))
    if row["error"]:
        raise ApiError(403, row["error"], "sso_failed")
    u = db.one("SELECT * FROM users WHERE id = ?", (row["user_id"],))
    if not u or not u["active"]:
        raise ApiError(403, "Your Kosha account is switched off. Ask your Kosha administrator to switch it on.",
                       "account_disabled")
    from .access import sign_in_problem
    problem = sign_in_problem(u)
    if problem:
        raise ApiError(403, problem, "account_not_usable")
    a = registry.get(row["provider"])
    token = sessions.create(u["id"], ip, user_agent)
    db.audit(u["id"], "login", "user", u["id"], f"from {ip} through {a.manifest.name if a and a.manifest else row['provider']}")
    from .twostep import note_place      # the provider asks for its own second step
    note_place(u, ip, user_agent)
    return u, token
