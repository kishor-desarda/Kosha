# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server-side sessions behind the sign-in tokens.

Every sign-in opens a row in ``sessions``; the token carries the session id. The table keeps only a SHA-256 of
that id, so a copy of the database (a backup, an export) never contains a usable session. A session ends when
the user signs out, changes the password, is signed out everywhere or deactivated by an admin, has been idle
longer than the idle limit, or reaches its absolute lifetime. Both limits are admin settings.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, db, security

SETTING_KEY = "security"
# OWASP ASVS level 2: sign in again after 12 hours, or after 30 minutes without activity.
DEFAULTS = {
    "session_idle_minutes": 30,
    "session_absolute_hours": config.TOKEN_HOURS,
    "sign_in_max_failures": 5,
    "sign_in_lock_minutes": 15,
    "sign_in_ip_max_failures": 20,
}
LIMITS = {
    "session_idle_minutes": (5, 7 * 24 * 60),
    "session_absolute_hours": (1, 24 * 30),
    "sign_in_max_failures": (3, 100),
    "sign_in_lock_minutes": (1, 24 * 60),
    "sign_in_ip_max_failures": (5, 100000),
}
TOUCH_SECONDS = 60          # last activity is written at most once a minute per session

_cache: tuple[float, dict] | None = None
_cache_lock = threading.Lock()


def settings() -> dict:
    """Current sign-in and session settings (cached for a few seconds; every request reads them)."""
    global _cache
    with _cache_lock:
        if _cache and time.monotonic() - _cache[0] < 5:
            return dict(_cache[1])
    stored = db.get_setting(SETTING_KEY) or {}
    out = dict(DEFAULTS)
    for k, (lo, hi) in LIMITS.items():
        try:
            out[k] = min(max(int(stored.get(k, out[k])), lo), hi)
        except (TypeError, ValueError):
            pass
    with _cache_lock:
        _cache = (time.monotonic(), out)
    return dict(out)


def save_settings(values: dict) -> dict:
    """Validate and store new settings. Raises ValueError with a plain message."""
    global _cache
    current = settings()
    labels = {"session_idle_minutes": "Idle limit (minutes)", "session_absolute_hours": "Sign in again after (hours)",
              "sign_in_max_failures": "Failed sign-ins per account", "sign_in_lock_minutes": "Pause (minutes)",
              "sign_in_ip_max_failures": "Failed sign-ins per network address"}
    for k, v in (values or {}).items():
        if k not in LIMITS:
            continue
        lo, hi = LIMITS[k]
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{labels[k]} must be a whole number.")
        if not lo <= n <= hi:
            raise ValueError(f"{labels[k]} must be between {lo} and {hi}.")
        current[k] = n
    db.set_setting(SETTING_KEY, current)
    with _cache_lock:
        _cache = None
    return settings()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(t: datetime) -> str:
    return t.isoformat()


def _parse(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _key(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()


def create(user_id: int, ip: str = "", user_agent: str = "") -> str:
    """Open a session and return the signed token for it."""
    s = settings()
    sid = secrets.token_urlsafe(24)
    now = _utcnow()
    hours = s["session_absolute_hours"]
    db.insert("sessions", {"id": _key(sid), "user_id": user_id, "created_at": _iso(now), "last_seen_at": _iso(now),
                           "expires_at": _iso(now + timedelta(hours=hours)), "ip": (ip or "")[:64],
                           "user_agent": (user_agent or "")[:200]})
    return security.make_token(user_id, hours=hours, sid=sid)


def check(token: str, touch: bool = True) -> tuple[int, str] | None:
    """(user id, session key) for a token whose session is still open, else None. Ends sessions that ran out."""
    payload = security.decode_token(token)
    if not payload or not isinstance(payload.get("sid"), str):
        return None
    key = _key(payload["sid"])
    row = db.one("SELECT user_id, created_at, last_seen_at, expires_at, ended_at FROM sessions WHERE id = ?", (key,))
    if not row or row["ended_at"] or row["user_id"] != int(payload["sub"]):
        return None
    s = settings()
    now = _utcnow()
    last = _parse(row["last_seen_at"])
    expires = min(_parse(row["expires_at"]), _parse(row["created_at"]) + timedelta(hours=s["session_absolute_hours"]))
    if now >= expires:
        end(key, "expired")
        return None
    if now - last > timedelta(minutes=s["session_idle_minutes"]):
        end(key, "idle")
        return None
    if touch and (now - last).total_seconds() >= TOUCH_SECONDS:
        try:
            db.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ? AND ended_at IS NULL", (_iso(now), key))
        except Exception:   # a busy database must not turn a read into an error; the next request tries again
            pass
    return row["user_id"], key


def end(key: str, reason: str) -> None:
    db.execute("UPDATE sessions SET ended_at = ?, ended_reason = ? WHERE id = ? AND ended_at IS NULL",
               (_iso(_utcnow()), reason, key))


def end_all(user_id: int, reason: str) -> int:
    """End every open session of a user; returns how many were open."""
    n = db.scalar("SELECT COUNT(*) FROM sessions WHERE user_id = ? AND ended_at IS NULL", (user_id,)) or 0
    db.execute("UPDATE sessions SET ended_at = ?, ended_reason = ? WHERE user_id = ? AND ended_at IS NULL",
               (_iso(_utcnow()), reason, user_id))
    return int(n)


def open_sessions(user_id: int) -> list[dict]:
    """Sessions of a user that are still open (expired ones are left out)."""
    s = settings()
    now = _utcnow()
    out = []
    for r in db.rows("SELECT id, created_at, last_seen_at, expires_at, ip, user_agent FROM sessions "
                     "WHERE user_id = ? AND ended_at IS NULL ORDER BY last_seen_at DESC", (user_id,)):
        if now >= _parse(r["expires_at"]) or now - _parse(r["last_seen_at"]) > timedelta(minutes=s["session_idle_minutes"]):
            continue
        out.append({"id": r["id"][:12], "created_at": r["created_at"], "last_seen_at": r["last_seen_at"],
                    "expires_at": r["expires_at"], "ip": r["ip"], "user_agent": r["user_agent"]})
    return out


def purge(days: int = 90) -> None:
    """Forget sessions that ended (or ran out) long ago; the audit log keeps sign-ins and sign-outs."""
    cutoff = _iso(_utcnow() - timedelta(days=days))
    db.execute("DELETE FROM sessions WHERE (ended_at IS NOT NULL AND ended_at < ?) OR expires_at < ?", (cutoff, cutoff))
