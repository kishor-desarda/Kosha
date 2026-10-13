# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two-step sign-in with an authenticator app (specification 15.1), and the other sign-in safeguards around it.

* **Codes:** time-based one-time codes (RFC 6238: SHA-1, six digits, 30 seconds), as made by Microsoft
  Authenticator, Google Authenticator, FreeOTP and similar apps. A code is accepted once, 30 seconds either side of
  now. Ten one-time **recovery codes** are given when two-step sign-in is switched on (only their fingerprints are kept).
* **Who must use it:** the roles an admin lists (for example admin and approver), and everyone signing in from
  outside the office networks when the admin asks for that. Someone who has switched it on is always asked.
  A person who must use it but has not set it up yet is led through the set-up during that sign-in.
* **Between the two steps** the browser holds a *challenge*: signed, valid five minutes, for one address. Wrong codes
  count towards the same pause as wrong passwords.
* **New places:** when someone signs in from a network they never used before (the same /24, or /48 for IPv6),
  admins are told.
* **Bulk downloads:** more than ``bulk_download_per_hour`` files in an hour pauses the person's downloads and tells
  the admins, who let the downloads continue when all is well.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt

from . import config, db
from .web import ApiError, bad, conflict

STEP = 30
DIGITS = 6
WINDOW = 1
CHALLENGE_MINUTES = 5
RECOVERY_CODES = 10


# ------------------------------------------------------------------- codes --

def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    n = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(n).zfill(DIGITS)


def _matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    t = int((now or time.time()) // STEP)
    for step in range(t - WINDOW, t + WINDOW + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def setup_uri(secret: str, username: str) -> str:
    issuer = (db.get_setting("company_name") or "Kosha").strip() or "Kosha"
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def _recovery_hash(code: str) -> str:
    return hashlib.sha256(("kosha-recovery|" + code.replace("-", "").upper()).encode()).hexdigest()


def _new_recovery() -> tuple[list[str], str]:
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = base64.b32encode(secrets.token_bytes(5)).decode()[:8]
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes, json.dumps([_recovery_hash(c) for c in codes])


def check_code(user: dict, code: str) -> str:
    """'' when the code (or an unused recovery code) is right; otherwise why not. Marks it used."""
    u = db.one("SELECT id, totp_secret, totp_last_step, totp_recovery_json FROM users WHERE id = ?", (user["id"],))
    if not u or not u["totp_secret"]:
        return "Two-step sign-in is not set up for this account."
    step = _matching_step(u["totp_secret"], code)
    if step is not None:
        if u["totp_last_step"] is not None and step <= u["totp_last_step"]:
            return "That code was used already. Wait for the next one in your app."
        db.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, u["id"]))
        return ""
    wanted = _recovery_hash(str(code or "").strip())
    left = json.loads(u["totp_recovery_json"] or "[]")
    if wanted in left:
        left.remove(wanted)
        db.execute("UPDATE users SET totp_recovery_json = ? WHERE id = ?", (json.dumps(left), u["id"]))
        db.audit(u["id"], "two_step_recovery_used", "user", u["id"], f"{len(left)} recovery codes left")
        return ""
    return "That code is not right. Type the six digits your authenticator app shows now."


# ------------------------------------------------------------------ policy --

def policy() -> dict:
    from .sessions import settings
    s = settings()
    return {"roles": s["two_step_roles"], "outside_office": s["two_step_outside_office"],
            "office_networks": s["office_networks"]}


def in_office(ip: str, networks: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for part in (networks or "").replace(",", " ").split():
        try:
            if addr in ipaddress.ip_network(part, strict=False):
                return True
        except ValueError:
            continue
    return False


def needed(u: dict, ip: str) -> str:
    """'verify' (ask for a code), 'setup' (must set it up now) or '' (no second step)."""
    if u.get("totp_secret"):
        return "verify"
    p = policy()
    from .access import extra_roles
    roles = {u["role"], *extra_roles(u["id"])}
    must = bool(roles & set(p["roles"])) or (p["outside_office"] and not in_office(ip, p["office_networks"]))
    return "setup" if must else ""


# --------------------------------------------------------------- challenge --

def make_challenge(user_id: int, ip: str, how: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=CHALLENGE_MINUTES)
    return jwt.encode({"purpose": "two_step", "uid": user_id, "ip": ip, "how": how, "exp": exp,
                       "n": secrets.token_hex(6)}, config.secret_key(), algorithm="HS256")


def read_challenge(token: str, ip: str) -> dict:
    try:
        c = jwt.decode(str(token or ""), config.secret_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ApiError(401, "The sign-in took too long or was interrupted. Sign in again with your password.",
                       "challenge_expired") from None
    if c.get("purpose") != "two_step" or c.get("ip") != ip:
        raise ApiError(401, "Sign in again with your password.", "challenge_expired")
    return c


# ----------------------------------------------------------------- set-up --

def start_setup(user_id: int) -> dict:
    u = db.one("SELECT id, username, totp_secret FROM users WHERE id = ?", (user_id,))
    if u["totp_secret"]:
        raise conflict("Two-step sign-in is already on. Turn it off first to set up a new app.", "already_on")
    secret = new_secret()
    db.execute("UPDATE users SET totp_pending = ? WHERE id = ?", (secret, user_id))
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
            "uri": setup_uri(secret, u["username"])}


def confirm_setup(user_id: int, code: str) -> list[str]:
    """Switch two-step sign-in on with the first code from the app; returns the recovery codes (shown once)."""
    u = db.one("SELECT id, totp_pending FROM users WHERE id = ?", (user_id,))
    if not u or not u["totp_pending"]:
        raise bad("Start the set-up first.", "no_setup")
    step = _matching_step(u["totp_pending"], code)
    if step is None:
        raise bad("That code is not right. Check that the app shows Kosha and type the six digits it shows now.",
                  "bad_code")
    codes, hashed = _new_recovery()
    with db.tx():
        db.execute("UPDATE users SET totp_secret = totp_pending, totp_pending = NULL, totp_enabled_at = ?, "
                   "totp_last_step = ?, totp_recovery_json = ? WHERE id = ?", (db.now(), step, hashed, user_id))
        db.audit(user_id, "two_step_on", "user", user_id, "")
    return codes


def turn_off(actor_id: int, user_id: int, why: str) -> None:
    with db.tx():
        db.execute("UPDATE users SET totp_secret = NULL, totp_pending = NULL, totp_enabled_at = NULL, "
                   "totp_last_step = NULL, totp_recovery_json = NULL WHERE id = ?", (user_id,))
        db.audit(actor_id, "two_step_off", "user", user_id, why)


def status(user_id: int, ip: str = "") -> dict:
    u = db.one("SELECT id, role, totp_secret, totp_enabled_at, totp_recovery_json FROM users WHERE id = ?",
               (user_id,))
    from .access import extra_roles
    p = policy()
    required = bool({u["role"], *extra_roles(user_id)} & set(p["roles"]))
    return {"on": bool(u["totp_secret"]), "since": u["totp_enabled_at"], "required": required,
            "recovery_left": len(json.loads(u["totp_recovery_json"] or "[]")) if u["totp_secret"] else 0}


# ------------------------------------------------------------- new places --

def place_of(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip or "unknown"
    prefix = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def note_place(u: dict, ip: str, agent: str) -> bool:
    """Remember where this person signed in; True (and admins told) when it is a new place for them."""
    place = place_of(ip)
    now = db.now()
    known = db.one("SELECT id FROM sign_in_places WHERE user_id = ? AND place = ?", (u["id"], place))
    if known:
        db.execute("UPDATE sign_in_places SET last_at = ?, last_ip = ?, user_agent = ? WHERE id = ?",
                   (now, ip, agent[:200], known["id"]))
        return False
    first_ever = not db.scalar("SELECT 1 FROM sign_in_places WHERE user_id = ? LIMIT 1", (u["id"],))
    with db.tx():
        db.insert("sign_in_places", {"user_id": u["id"], "place": place, "first_at": now, "last_at": now,
                                     "last_ip": ip, "user_agent": agent[:200]})
        if not first_ever:
            db.audit(u["id"], "login_new_place", "user", u["id"], f"from {ip} ({place})")
    from .sessions import settings
    if first_ever or not settings()["new_place_alerts"]:
        return not first_ever
    from . import notify_outbox
    admins = [r["id"] for r in db.rows("SELECT id FROM users WHERE active = 1 AND role = 'admin' AND id != ?",
                                       (u["id"],))]
    notify_outbox.enqueue(admins, f"{u['full_name']} signed in from a new place",
                          f"{u['full_name']} ({u['username']}) signed in from {ip}, a network they have not used "
                          f"before. If that is unexpected, sign them out everywhere under Admin › Users and ask them.",
                          "#/admin?tab=users", severity="warning", key=f"new_place:{u['id']}:{place}",
                          source=f"user:{u['id']}")
    return True


# ---------------------------------------------------------- bulk downloads --

def count_download(user: dict | None) -> None:
    """Called before each download. Refuses while the person's downloads are paused, and pauses them (telling the
    admins) when they pass the hourly limit."""
    if not user or not user.get("id"):
        return
    u = db.one("SELECT id, username, full_name, downloads_paused_at FROM users WHERE id = ?", (user["id"],))
    if not u:
        return
    if u["downloads_paused_at"]:
        raise ApiError(423, "Your downloads are paused because many files were downloaded in a short time. This "
                            "protects the company's drawings. Viewing still works. Ask your Kosha administrator to "
                            "let your downloads continue.", "downloads_paused")
    from .sessions import settings
    limit = settings()["bulk_download_per_hour"]
    if not limit:
        return
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat()
    n = db.scalar("SELECT COUNT(*) FROM audit WHERE user_id = ? AND action = 'download' AND at > ?", (u["id"], since))
    if n < limit:
        return
    with db.tx():
        db.execute("UPDATE users SET downloads_paused_at = ? WHERE id = ?", (db.now(), u["id"]))
        db.audit(u["id"], "downloads_paused", "user", u["id"], f"{n} downloads in an hour (limit {limit})")
    from . import notify_outbox
    admins = [r["id"] for r in db.rows("SELECT id FROM users WHERE active = 1 AND role = 'admin'")]
    notify_outbox.enqueue(admins, f"Downloads paused: {u['full_name']}",
                          f"{u['full_name']} ({u['username']}) downloaded {n} files in the last hour, more than the "
                          f"limit of {limit}. Their downloads are paused; viewing still works. Check with them, then "
                          "let the downloads continue under Admin › Users.", "#/admin?tab=users", severity="error",
                          key=f"downloads_paused:{u['id']}:{db.now()[:13]}", source=f"user:{u['id']}")
    raise ApiError(423, "Your downloads are paused because many files were downloaded in a short time. Your "
                        "administrator has been told and can let them continue.", "downloads_paused")


def resume_downloads(admin_id: int, user_id: int) -> None:
    with db.tx():
        db.execute("UPDATE users SET downloads_paused_at = NULL WHERE id = ?", (user_id,))
        db.audit(admin_id, "downloads_resumed", "user", user_id, "")
