# SPDX-License-Identifier: AGPL-3.0-or-later
"""Personal data: export and erase on request (specification 15.7; India's DPDP Act 2023, the EU GDPR).

Kosha holds little personal data: a person's name, user name and e-mail address, where and when they signed in,
their settings, and what they wrote in discussions. **Export** gives all of it as one JSON file. **Erase** removes
what identifies the person and keeps the company's records: the account becomes *Former user 17*, cannot sign in,
and their sign-in history, places, directory links and notification settings are deleted. The audit log keeps its
entries (records of what was done to drawings are kept for ten years, and its chain must not break); they then name
the former user only by number.
"""
from __future__ import annotations

import secrets

from . import db, security, sessions
from .web import bad, conflict, not_found

PROFILE = ("id", "username", "full_name", "email", "role", "kind", "company", "ui_mode", "ui_theme", "active",
           "created_at", "expires_on", "view_only", "totp_enabled_at", "delegate_until")


def _user(user_id: int) -> dict:
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not u:
        raise not_found("User not found")
    return u


def export(user_id: int) -> dict:
    u = _user(user_id)
    from .access import extra_roles
    return {
        "about": "Personal data Kosha holds about this person (specification 15.7).",
        "exported_at": db.now(),
        "profile": {k: u.get(k) for k in PROFILE if k in u},
        "extra_roles": extra_roles(user_id),
        "sessions": db.rows("SELECT created_at, last_seen_at, ended_at, ended_reason, ip, user_agent FROM sessions "
                            "WHERE user_id = ? ORDER BY created_at", (user_id,)),
        "sign_in_places": db.rows("SELECT place, first_at, last_at, last_ip, user_agent FROM sign_in_places "
                                  "WHERE user_id = ? ORDER BY first_at", (user_id,)),
        "directory_links": db.rows("SELECT provider, directory_username, email, created_at, last_sign_in_at, "
                                   "unlinked_at FROM user_identities WHERE user_id = ? ORDER BY id", (user_id,)),
        "notification_settings": db.rows("SELECT kind, channels_json, updated_at FROM notify_prefs WHERE user_id = ?",
                                         (user_id,)),
        "comments": db.rows("SELECT c.created_at, c.edited_at, c.deleted_at, t.entity_type, t.entity_id, c.body "
                            "FROM discussion_comments c JOIN discussion_threads t ON t.id = c.thread_id "
                            "WHERE c.user_id = ? ORDER BY c.id", (user_id,)),
        "audit": db.rows("SELECT at, action, entity, entity_id, details FROM audit WHERE user_id = ? ORDER BY id",
                         (user_id,)),
        "favourites": db.rows("SELECT kind, ref_id, created_at FROM user_favourites WHERE user_id = ? ORDER BY id",
                              (user_id,)),
        "saved_views": db.rows("SELECT name, hash, created_at FROM saved_views WHERE user_id = ? ORDER BY id", (user_id,)),
        "screen_preferences": db.scalar("SELECT prefs_json FROM user_prefs WHERE user_id = ?", (user_id,)) or "{}",
    }


def erase(admin_id: int, user_id: int, confirm: str) -> dict:
    u = _user(user_id)
    if user_id == admin_id:
        raise conflict("You cannot erase your own account. Ask another admin.", "own_account")
    if str(confirm or "").strip().casefold() != u["username"].casefold():
        raise bad(f"Type the user name ({u['username']}) to confirm. Erasing cannot be undone.", "confirm")
    if u["role"] == "admin" and not db.scalar("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != ?",
                                              (user_id,)):
        raise conflict("This is the only active admin. Make someone else an admin first.", "last_admin")
    if u["username"].startswith("erased-"):
        raise conflict("This account was erased already.", "erased")
    with db.tx():
        sessions.end_all(user_id, "personal data erased")
        upd = {"username": f"erased-{user_id}", "full_name": f"Former user {user_id}", "email": "",
               "password_hash": security.hash_password(secrets.token_urlsafe(32)), "active": 0,
               "totp_secret": None, "totp_pending": None, "totp_recovery_json": None, "totp_enabled_at": None}
        for col, empty in (("company", ""), ("ui_theme", ""), ("delegate_id", None), ("delegate_until", None)):
            if col in u:
                upd[col] = empty
        db.update("users", user_id, upd)
        db.execute("DELETE FROM sign_in_places WHERE user_id = ?", (user_id,))
        db.execute("UPDATE sessions SET ip = '', user_agent = '' WHERE user_id = ?", (user_id,))
        db.execute("UPDATE user_identities SET directory_username = '', email = '', external_id = NULL, "
                   "groups_json = '[]', unlinked_at = COALESCE(unlinked_at, ?), unlinked_by = COALESCE(unlinked_by, ?) "
                   "WHERE user_id = ?", (db.now(), admin_id, user_id))
        db.execute("DELETE FROM notify_prefs WHERE user_id = ?", (user_id,))
        from . import workbench
        workbench.forget(user_id)
        db.execute("UPDATE users SET delegate_id = NULL, delegate_until = NULL WHERE delegate_id = ?", (user_id,))
        db.audit(admin_id, "personal_data_erased", "user", user_id, "")
    return {"id": user_id, "full_name": f"Former user {user_id}"}
