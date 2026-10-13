# SPDX-License-Identifier: AGPL-3.0-or-later
"""Directory sign-in: the buttons on the sign-in page, redirect round trips, groups and roles, links to users."""
from __future__ import annotations

from urllib.parse import quote

from starlette.responses import JSONResponse, RedirectResponse

from .. import config, db, directory, notify_outbox
from ..signin import USER_FIELDS, client_ip
from ..web import ROLE_LEVEL, ApiError, Req, api, bad, not_found

from kosha_sdk import AddinError  # noqa: E402


def _base(req: Req) -> str:
    """Kosha's own address: the one set under Admin › Notifications, else the one this request came to."""
    base = notify_outbox.base_url()
    if not base:
        base = str(req.request.base_url).rstrip("/")
        if config.HTTPS and base.startswith("http://"):
            base = "https://" + base[len("http://"):]
    return base


def redirect_uri(req: Req, addin_id: str) -> str:
    return f"{_base(req)}/api/auth/sso/{quote(addin_id, safe='')}/callback"


def _to_page(fragment: str, keep_cookie: bool = False) -> RedirectResponse:
    """Back to the web client; the sign-in page reads the fragment (a ticket, or why it did not work)."""
    r = RedirectResponse(f"/#/{fragment}", status_code=303)
    if not keep_cookie:
        r.delete_cookie(directory.COOKIE, path=directory.COOKIE_PATH)
    return r


def _secure(req: Req) -> bool:
    return config.HTTPS or req.request.url.scheme == "https"


@api("GET", "/api/auth/providers", role=None, auth=False)
def sign_in_providers(req: Req):
    """Ways to sign in besides a Kosha password: buttons for redirect providers (OpenID Connect ...), and whether
    the user name and password are also checked against a directory (LDAP, Active Directory)."""
    ready = [p for p in directory.providers() if p.ready]
    return {"redirect": [{"id": p.id, "label": p.label} for p in ready if p.style == "redirect"],
            "directory_passwords": [p.label for p in ready if p.style == "password"]}


@api("GET", "/api/auth/sso/{addin_id}/start", role=None, auth=False)
def sso_start(req: Req):
    """Start signing in with a redirect provider: sends the browser to the provider's sign-in page."""
    aid = req.params["addin_id"]
    try:
        url, browser = directory.begin(aid, redirect_uri(req, aid), client_ip(req))
    except AddinError as e:
        db.audit(None, "login_failed", "user", None, f"{aid} from {client_ip(req)}: could not start: {e}")
        return _to_page("sso-error/unavailable")
    except ApiError as e:
        return _to_page("sso-error/" + ("busy" if e.status == 429 else "unavailable"))
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(directory.COOKIE, browser, max_age=directory.REQUEST_MINUTES * 60, path=directory.COOKIE_PATH,
                 httponly=True, samesite="lax", secure=_secure(req))
    return r


@api("GET", "/api/auth/sso/{addin_id}/callback", role=None, auth=False)
def sso_callback(req: Req):
    """Where the provider sends the browser back. Answers with a one-time ticket for the sign-in page."""
    try:
        ticket = directory.returned(req.params["addin_id"], dict(req.query),
                                    req.request.cookies.get(directory.COOKIE, ""), client_ip(req))
    except ApiError:
        return _to_page("sso-error/expired")
    return _to_page(f"sso/{ticket}", keep_cookie=True)


@api("POST", "/api/auth/sso/finish", role=None, auth=False)
def sso_finish(req: Req):
    """Redeem the one-time `ticket` from a redirect sign-in (same browser, within two minutes) for a session token."""
    ticket = req.b("ticket") if isinstance(req.b("ticket"), str) else ""
    try:
        u, token = directory.redeem(ticket, req.request.cookies.get(directory.COOKIE, ""), client_ip(req),
                                    req.request.headers.get("user-agent", ""))
    except ApiError as e:
        r = JSONResponse({"error": e.message, "code": e.code}, status_code=e.status)
        r.delete_cookie(directory.COOKIE, path=directory.COOKIE_PATH)
        return r
    from .core import erp_name
    r = JSONResponse({"token": token, "user": {k: u[k] for k in USER_FIELDS}, "erp_name": erp_name()})
    r.delete_cookie(directory.COOKIE, path=directory.COOKIE_PATH)
    return r


# ------------------------------------------------------------------ admin --

def _admin_view(req: Req) -> dict:
    return {"settings": directory.settings(), "roles": list(ROLE_LEVEL),
            "providers": [{**p.to_dict(), "redirect_uri": redirect_uri(req, p.id) if p.style != "password" else ""}
                          for p in directory.providers()],
            "links": directory.links(), "local_admin": directory.LOCAL_ADMIN}


@api("GET", "/api/admin/directory", role="admin")
def get_directory(req: Req):
    """Directory sign-in: the sign-in add-ins and whether they are ready, the group → role list, and linked users."""
    return _admin_view(req)


@api("PUT", "/api/admin/directory", role="admin")
def put_directory(req: Req):
    """Change the group → role list (`group_roles`), the role for everyone else (`default_role`, "" = no access),
    `create_users` and `roles_from_directory`."""
    if not isinstance(req.body, dict):
        raise bad("Send the settings as a JSON object.")
    try:
        new = directory.save_settings(req.body)
    except ValueError as e:
        raise bad(str(e))
    db.audit(req.uid, "directory_settings", "settings", None,
             f"{len(new['group_roles'])} group(s); everyone else: {new['default_role'] or 'no access'}; "
             f"create users: {new['create_users']}; roles from directory: {new['roles_from_directory']}")
    return _admin_view(req)


@api("POST", "/api/admin/directory/test", role="admin")
def test_directory(req: Req):
    """Check a directory account (`provider`, `username`, `password`) without signing in: who the directory says it
    is, its groups, and the Kosha role and account it would get. Nothing is changed."""
    p = directory.provider(str(req.b("provider") or ""), "password")
    if not p or not p.ready:
        raise bad("Choose a sign-in add-in that checks passwords and is set up.")
    username = str(req.b("username") or "").strip()
    password = req.b("password") if isinstance(req.b("password"), str) else ""
    if not username or not password:
        raise bad("Enter the user name and password of a directory account to check.")
    try:
        found = directory.registry.call(p.id, "authenticate", username, password, timeout=directory.CALL_TIMEOUT)
    except AddinError as e:
        db.audit(req.uid, "directory_test", "settings", None, f"{p.name} '{username}': {e}")
        return {"ok": False, "message": f"{p.name} could not check the account: {e}"}
    db.audit(req.uid, "directory_test", "settings", None, f"{p.name} '{username}': {'found' if found else 'refused'}")
    if found is None:
        return {"ok": False, "message": f"{p.name} says the user name or password is wrong."}
    ident = directory._as_identity(found, p.name)
    linked = db.one("SELECT u.username FROM user_identities i JOIN users u ON u.id = i.user_id WHERE i.provider = ? "
                    "AND (i.external_id = ? OR (i.external_id IS NULL AND i.directory_username = ? COLLATE NOCASE)) "
                    "AND i.unlinked_at IS NULL", (p.id, ident.external_id, ident.username))
    role = directory.role_for(ident.groups)
    if linked:
        account = f"Signs in as the Kosha user {linked['username']}."
    elif db.one("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (ident.username,)):
        account = (f"A Kosha account named {ident.username} exists but is not linked: link it under Users › "
                   "Directory, or this person cannot sign in.")
    elif directory.settings()["create_users"]:
        account = f"A new Kosha user {ident.username} is created at the first sign-in."
    else:
        account = "No Kosha account yet, and new users are not created automatically: add and link one first."
    return {"ok": True, "message": "The directory knows this account.",
            "identity": {"external_id": ident.external_id, "username": ident.username, "full_name": ident.full_name,
                         "email": ident.email, "groups": list(ident.groups)},
            "role": role, "account": account}


def _user(req: Req) -> dict:
    u = db.one("SELECT id, username, full_name FROM users WHERE id = ?", (req.int_param("id"),))
    if not u:
        raise not_found("User not found")
    return u


@api("GET", "/api/users/{id:int}/directory", role="admin")
def user_directory(req: Req):
    """Directory accounts linked to a user, and the sign-in add-ins a link can be made to."""
    u = _user(req)
    return {"user": u, "links": directory.links(u["id"]), "local_admin": u["username"].casefold() == directory.LOCAL_ADMIN,
            "providers": [p.to_dict() for p in directory.providers()]}


@api("POST", "/api/users/{id:int}/directory", role="admin")
def link_user(req: Req):
    """Link a user to a directory account (`provider`, `directory_username`). From that person's next directory
    sign-in on, they sign in through the directory; their Kosha password is no longer used."""
    u = _user(req)
    directory.link(u["id"], str(req.b("provider") or ""), str(req.b("directory_username") or ""), req.uid)
    return user_directory(req)


@api("DELETE", "/api/users/{id:int}/directory/{link_id:int}", role="admin")
def unlink_user(req: Req):
    """Unlink a directory account from a user and end their sessions. They can then sign in only with a Kosha
    password (set one under Users › Reset password)."""
    u = _user(req)
    ended = directory.unlink(u["id"], req.int_param("link_id"), req.uid)
    return {**user_directory(req), "sessions_ended": ended}
