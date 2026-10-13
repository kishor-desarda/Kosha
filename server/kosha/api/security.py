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


# --------------------------------------------------------- two-step sign-in --

@api("GET", "/api/me/two-step")
def my_two_step(req: Req):
    from .. import twostep
    return twostep.status(req.uid)


@api("POST", "/api/me/two-step/start", role="viewer")
def my_two_step_start(req: Req):
    """The key to add to an authenticator app (as text and as an otpauth:// link)."""
    from .. import twostep
    return twostep.start_setup(req.uid)


@api("POST", "/api/me/two-step/confirm", role="viewer")
def my_two_step_confirm(req: Req):
    """The first code from the app switches two-step sign-in on; ``recovery_codes`` are shown this once."""
    from .. import twostep
    return {"recovery_codes": twostep.confirm_setup(req.uid, req.b("code")), **twostep.status(req.uid)}


@api("POST", "/api/me/two-step/off", role="viewer")
def my_two_step_off(req: Req):
    from .. import twostep
    st = twostep.status(req.uid)
    if st["required"]:
        raise bad("Your role needs two-step sign-in, so it stays on. To move to a new phone, ask an admin to reset "
                  "it; you set it up again at your next sign-in.", "required")
    why = twostep.check_code(req.user, req.b("code"))
    if why:
        raise bad(why, "bad_code")
    twostep.turn_off(req.uid, req.uid, "turned off by the user")
    return twostep.status(req.uid)


@api("POST", "/api/users/{id:int}/two-step/reset", role="admin")
def reset_two_step(req: Req):
    """For a lost phone: turns two-step sign-in off for this person and signs them out. Where their role needs it,
    they set it up again at their next sign-in."""
    from .. import twostep
    u = _user(req)
    twostep.turn_off(req.uid, u["id"], "reset by an admin")
    sessions.end_all(u["id"], "two-step sign-in reset by an admin")
    return twostep.status(u["id"])


@api("POST", "/api/users/{id:int}/downloads/resume", role="admin")
def resume_downloads(req: Req):
    """Let a person whose downloads the bulk-download alarm paused download again."""
    from .. import twostep
    u = _user(req)
    twostep.resume_downloads(req.uid, u["id"])
    return {"ok": True}


# ------------------------------------------------------------ personal data --

@api("GET", "/api/users/{id:int}/personal-data", role="admin")
def personal_data(req: Req):
    """Everything Kosha holds about a person, for a data-protection request."""
    from .. import privacy
    u = _user(req)
    db.audit(req.uid, "personal_data_export", "user", u["id"], "")
    return privacy.export(u["id"])


@api("POST", "/api/users/{id:int}/erase", role="admin")
def erase_user(req: Req):
    """Erase what identifies a person (``confirm``: their user name); the company's records stay."""
    from .. import privacy
    return privacy.erase(req.uid, req.int_param("id"), req.b("confirm"))


# -------------------------------------------------------------- audit chain --

@api("GET", "/api/audit/verify", role="admin")
def verify_audit(req: Req):
    """Recompute the audit log's chain of fingerprints: shows whether any entry was changed or removed, and where."""
    from .. import auditchain
    return auditchain.verify()
