# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-app notifications (the bell in the top bar).

The model is deliberately generic so any feature can tell a user about something with one call:

    from kosha import notifications
    notifications.notify(user_id, "deviation_approval", "DEV-26-0003 is waiting for your approval", "#/deviation/3")

``kind`` is a short machine word (``mention``, ``discussion_reply``, ``deviation_approval`` ...), ``title`` the plain
sentence the user reads, ``link`` a route inside Kosha (``#/doc/12``). Notifications are only marked read, never deleted.
Called inside a ``db.tx()`` block, the notification is part of that transaction.
"""
from __future__ import annotations

from typing import Iterable

from . import db

MAX_TITLE = 300
MAX_LINK = 500


def _safe_link(link: str | None) -> str:
    """Only links inside Kosha: a route (#/...) or a path on this server (/...). Anything else is dropped."""
    link = (link or "").strip()
    if link.startswith("#/") or (link.startswith("/") and not link.startswith("//")):
        return link[:MAX_LINK]
    return ""


def notify(user_id: int | None, kind: str, title: str, link: str = "", actor_id: int | None = None,
           key: str = "") -> int | None:
    """Create one notification for one user. Returns its id, or None when the user is unknown or inactive, or
    when a notification with the same ``key`` was already given to this user (a notice queued twice shows once)."""
    if not user_id or not (kind or "").strip() or not (title or "").strip():
        return None
    u = db.one("SELECT id, active FROM users WHERE id = ?", (int(user_id),))
    if not u or not u["active"]:
        return None
    key = (key or "").strip()[:200]
    if key and db.one("SELECT id FROM notifications WHERE user_id = ? AND notice_key = ?", (u["id"], key)):
        return None
    return db.insert("notifications", {
        "user_id": u["id"], "kind": kind.strip()[:60], "title": " ".join(title.split())[:MAX_TITLE],
        "link": _safe_link(link), "actor_id": actor_id, "created_at": db.now(), "read_at": None,
        "notice_key": key or None,
    })


def notify_many(user_ids: Iterable[int | None], kind: str, title: str, link: str = "", actor_id: int | None = None,
                skip: Iterable[int | None] = ()) -> list[int]:
    """Notify several users once each, leaving out ``skip`` (usually the person who did the action)."""
    left_out = {s for s in skip if s}
    out, seen = [], set()
    for uid in user_ids:
        if not uid or uid in left_out or uid in seen:
            continue
        seen.add(uid)
        nid = notify(uid, kind, title, link, actor_id)
        if nid:
            out.append(nid)
    return out


def unread_count(user_id: int) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,)) or 0)


def for_user(user_id: int, unread_only: bool = False, limit: int = 30) -> list[dict]:
    rows = db.rows("SELECT n.*, u.full_name AS actor_name FROM notifications n LEFT JOIN users u ON u.id = n.actor_id "
                   "WHERE n.user_id = ?" + (" AND n.read_at IS NULL" if unread_only else "") +
                   " ORDER BY n.id DESC LIMIT ?", (user_id, max(1, min(int(limit), 200))))
    for r in rows:
        r["read"] = r["read_at"] is not None
    return rows


def mark_read(user_id: int, ids: Iterable[int] | None = None) -> int:
    """Mark the user's own notifications as read (all unread ones when ``ids`` is None). Returns how many changed."""
    now = db.now()
    with db.tx():
        if ids is None:
            n = db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,))
            db.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (now, user_id))
            return int(n or 0)
        n = 0
        for nid in {int(i) for i in ids}:
            if db.scalar("SELECT 1 FROM notifications WHERE id = ? AND user_id = ? AND read_at IS NULL", (nid, user_id)):
                db.execute("UPDATE notifications SET read_at = ? WHERE id = ?", (now, nid))
                n += 1
        return n
