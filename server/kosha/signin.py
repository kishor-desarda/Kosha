# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in with throttling of failed attempts, and password changes that end old sessions.

After too many failed sign-ins for one user name (default 5) or from one network address (default 20) within
the pause time (default 15 minutes), sign-in is paused for that user name or address until the pause has run
out. The answer is the same whether or not the user name exists, and a password check is always made, so
neither the message nor the answer time tells an attacker which accounts exist.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone

from . import db, security, sessions
from .web import ApiError, Req, bad

log = logging.getLogger("kosha.signin")

USER_FIELDS = ("id", "username", "full_name", "email", "role", "ui_mode", "ui_theme")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def client_ip(req: Req) -> str:
    """The caller's address. Behind an HTTPS proxy, uvicorn takes it from X-Forwarded-For (see FORWARDED_ALLOW_IPS)."""
    c = req.request.client
    return (c.host if c else "") or "unknown"


def _clean_name(username: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", (username or "").strip())[:64].casefold()


def _locked_until(username: str, ip: str) -> datetime | None:
    s = sessions.settings()
    since = (_now() - timedelta(minutes=s["sign_in_lock_minutes"])).isoformat()
    until = None
    for column, value, limit in (("username", username, s["sign_in_max_failures"]),
                                 ("ip", ip, s["sign_in_ip_max_failures"])):
        row = db.one(f"SELECT COUNT(*) AS n, MAX(at) AS last FROM sign_in_failures WHERE {column} = ? AND at > ?",
                     (value, since))
        if row and row["n"] >= limit and row["last"]:
            t = datetime.fromisoformat(row["last"]) + timedelta(minutes=s["sign_in_lock_minutes"])
            until = max(until, t) if until else t
    return until if until and until > _now() else None


def _paused(until: datetime) -> ApiError:
    seconds = max(1, int((until - _now()).total_seconds()))
    minutes = math.ceil(seconds / 60)
    return ApiError(429, f"Too many failed sign-ins. For your security, sign-in is paused: try again in {minutes} "
                         f"minute{'s' if minutes != 1 else ''}. Your work is safe. If you have forgotten your "
                         "password, ask your Kosha administrator to reset it.",
                    "sign_in_paused", {"retry_after": seconds, "retry_at": until.isoformat()})


def record_failure(username: str, ip: str, user_id: int | None, why: str) -> ApiError | None:
    """Note a failed attempt; returns the 'paused' error when this attempt reached a limit."""
    name = _clean_name(username)
    now = _now()
    with db.tx():
        db.insert("sign_in_failures", {"at": now.isoformat(), "username": name, "ip": ip})
        db.execute("DELETE FROM sign_in_failures WHERE at < ?", ((now - timedelta(days=1)).isoformat(),))
        db.audit(user_id, "login_failed", "user", user_id, f"user name '{name}' from {ip}: {why}")
    until = _locked_until(name, ip)
    if until:
        db.audit(user_id, "login_paused", "user", user_id, f"user name '{name}' from {ip} until {until.isoformat()}")
        log.warning("Sign-in paused for user name %r / address %s until %s", name, ip, until.isoformat())
        return _paused(until)
    return None


def sign_in(req: Req) -> dict:
    """Check the user name and password; returns {"token", "user"} or raises a plain-language ApiError."""
    username = (req.b("username") or "").strip() if isinstance(req.b("username"), str) else ""
    password = req.b("password") if isinstance(req.b("password"), str) else ""
    password = password[:1024]     # passwords have at most 128 characters; never spend time hashing megabytes
    ip = client_ip(req)
    name = _clean_name(username)
    until = _locked_until(name, ip)
    if until:
        raise _paused(until)
    u = db.one("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)) if username else None
    if u is None:
        security.waste_verify(password)
        ok, why = False, "unknown user name"
    else:
        ok = security.verify_password(password, u["password_hash"])
        why = "wrong password"
        if ok and not u["active"]:
            ok, why = False, "account deactivated"
    if not ok:
        paused = record_failure(username, ip, u["id"] if u else None, why)
        if paused:
            raise paused
        raise bad("Wrong user name or password.", "bad_login")
    db.execute("DELETE FROM sign_in_failures WHERE username = ?", (name,))
    token = sessions.create(u["id"], ip, req.request.headers.get("user-agent", ""))
    db.audit(u["id"], "login", "user", u["id"], f"from {ip}")
    return {"token": token, "user": {k: u[k] for k in USER_FIELDS}}


def check_new_password(password, username: str) -> str:
    """The new password when the policy accepts it; otherwise a 400 with the reason."""
    if not isinstance(password, str):
        raise bad(security.password_problem("", username) or "Enter a password.", "weak_password")
    problem = security.password_problem(password, username)
    if problem:
        raise bad(problem, "weak_password")
    return password


def change_own_password(req: Req) -> dict:
    """Change the signed-in user's password; ends all their sessions and returns a new token for this one."""
    u = db.one("SELECT * FROM users WHERE id = ?", (req.uid,))
    ip = client_ip(req)
    until = _locked_until(_clean_name(u["username"]), ip)
    if until:
        raise _paused(until)
    current = req.b("current") if isinstance(req.b("current"), str) else ""
    if not security.verify_password(current, u["password_hash"]):
        paused = record_failure(u["username"], ip, u["id"], "wrong current password when changing it")
        if paused:
            raise paused
        raise bad("Your current password is not correct.", "bad_password")
    new = check_new_password(req.b("new"), u["username"])
    if new == current:
        raise bad("Choose a password that is different from the current one.", "weak_password")
    with db.tx():
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (security.hash_password(new), u["id"]))
        ended = sessions.end_all(u["id"], "password changed")
        db.audit(u["id"], "password_change", "user", u["id"], f"{ended} session(s) ended")
    token = sessions.create(u["id"], ip, req.request.headers.get("user-agent", ""))
    return {"ok": True, "token": token, "sessions_ended": ended}
