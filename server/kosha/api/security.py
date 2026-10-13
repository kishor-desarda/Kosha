# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sessions and sign-in settings: sign out, sign a user out everywhere, session and throttling limits."""
from __future__ import annotations

from .. import db, security, sessions
from ..web import Req, api, bad, not_found


@api("POST", "/api/auth/logout")
def logout(req: Req):
    """Sign out: ends the session of the token used for this request."""
    sid = getattr(req.request.state, "session_id", None)
    if sid:
        sessions.end(sid, "signed out")
    db.audit(req.uid, "logout", "user", req.uid, "")
    return {"ok": True}


@api("GET", "/api/auth/password-rules", role=None, auth=False)
def password_rules(req: Req):
    """The password policy, in words, for sign-up and change-password forms."""
    return {"min_length": security.PASSWORD_MIN_LENGTH, "max_length": security.PASSWORD_MAX_LENGTH,
            "hint": security.PASSWORD_HINT}


def _user(req: Req) -> dict:
    u = db.one("SELECT id, username, full_name FROM users WHERE id = ?", (req.int_param("id"),))
    if not u:
        raise not_found("User not found")
    return u


@api("GET", "/api/users/{id:int}/sessions", role="admin")
def user_sessions(req: Req):
    """Open sessions of a user (when they started, last activity, address, program)."""
    u = _user(req)
    return {"user": u, "sessions": sessions.open_sessions(u["id"])}


@api("POST", "/api/users/{id:int}/sign-out-everywhere", role="admin")
def sign_out_everywhere(req: Req):
    """End every session of a user, in browsers and CAD add-ins alike. They can sign in again at once."""
    u = _user(req)
    with db.tx():
        n = sessions.end_all(u["id"], "signed out everywhere by an admin")
        db.audit(req.uid, "sign_out_everywhere", "user", u["id"], f"{u['username']}: {n} session(s) ended")
    return {"ok": True, "sessions_ended": n}


@api("GET", "/api/admin/security", role="admin")
def get_security(req: Req):
    """Session lifetimes and sign-in throttling limits, with the allowed ranges."""
    return {"settings": sessions.settings(), "defaults": sessions.DEFAULTS,
            "limits": {k: {"min": lo, "max": hi} for k, (lo, hi) in sessions.LIMITS.items()},
            "password_hint": security.PASSWORD_HINT}


@api("PUT", "/api/admin/security", role="admin")
def put_security(req: Req):
    """Change session lifetimes and sign-in throttling limits (applies to open sessions at once)."""
    if not isinstance(req.body, dict):
        raise bad("Send the settings as a JSON object.")
    try:
        new = sessions.save_settings(req.body)
    except ValueError as e:
        raise bad(str(e))
    db.audit(req.uid, "security_settings", "settings", None, ", ".join(f"{k}={v}" for k, v in new.items()))
    return get_security(req)
