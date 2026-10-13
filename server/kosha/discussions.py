# SPDX-License-Identifier: AGPL-3.0-or-later
"""Discussions: comment threads on items, document revisions, change orders, deviations and BOMs.

Rules
- Anyone signed in can start a thread or reply; ``@username`` mentions notify that user in the app.
- The thread starter, or anyone with the engineer role or higher, can resolve and reopen a thread.
- You can edit your own comment for 15 minutes; the earlier text is kept in ``discussion_comment_edits``.
- You can remove your own comment (it shows as "comment removed"; the text stays in the database).
  Nobody can edit or remove somebody else's comment.
- Every action is written to the audit log.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import db, notifications
from .web import ROLE_LEVEL, bad, conflict, forbidden, not_found

ENTITY_TYPES = ("item", "document", "change_order", "deviation", "bom")
EDIT_WINDOW = timedelta(minutes=15)
MAX_BODY = 4000
MAX_TITLE = 200
REMOVED = "comment removed"
MENTION = re.compile(r"(?<![\w@.\\])@([A-Za-z0-9_][\w.\-\\]*)")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _ok(ok: bool, why: str = "") -> dict:
    return {"ok": bool(ok), "why": "" if ok else why}


# ------------------------------------------------------------------ records --

def entity_info(entity_type: str, entity_id: int) -> dict:
    """Label, link and (for documents) the current revision of the record a thread belongs to."""
    if entity_type not in ENTITY_TYPES:
        raise bad(f"Discussions can be held on: {', '.join(ENTITY_TYPES)}.")
    eid = int(entity_id)
    if entity_type == "item":
        r = db.one("SELECT part_number, revision FROM items WHERE id = ?", (eid,))
        if not r:
            raise not_found("Item not found")
        return {"label": r["part_number"], "link": f"#/item/{eid}", "revision": ""}
    if entity_type == "document":
        r = db.one("SELECT filename, revision FROM documents WHERE id = ? AND deleted = 0", (eid,))
        if not r:
            raise not_found("File not found")
        return {"label": f"{r['filename']} rev {r['revision']}", "link": f"#/doc/{eid}", "revision": r["revision"],
                "filename": r["filename"]}
    if entity_type == "change_order":
        r = db.one("SELECT number, title FROM change_orders WHERE id = ?", (eid,))
        if not r:
            raise not_found("Change order not found")
        return {"label": r["number"], "link": f"#/change/{eid}", "revision": ""}
    if entity_type == "deviation":
        r = db.one("SELECT number FROM deviations WHERE id = ?", (eid,))
        if not r:
            raise not_found("Deviation not found")
        return {"label": r["number"], "link": f"#/deviation/{eid}", "revision": ""}
    r = db.one("SELECT b.revision, i.part_number FROM boms b JOIN items i ON i.id = b.item_id WHERE b.id = ?", (eid,))
    if not r:
        raise not_found("BOM not found")
    return {"label": f"BOM {r['part_number']} rev {r['revision']}", "link": f"#/bom/{eid}", "revision": ""}


def thread_or_404(thread_id: int) -> dict:
    t = db.one("SELECT * FROM discussion_threads WHERE id = ?", (int(thread_id),))
    if not t:
        raise not_found("Discussion not found")
    return t


def comment_or_404(comment_id: int) -> dict:
    c = db.one("SELECT * FROM discussion_comments WHERE id = ?", (int(comment_id),))
    if not c:
        raise not_found("Comment not found")
    return c


def _clean_body(body: str | None) -> str:
    text = (body or "").strip()
    if not text:
        raise bad("Write something first.")
    if len(text) > MAX_BODY:
        raise bad(f"Comments can be up to {MAX_BODY} characters; this one has {len(text)}. Split it into two.")
    return text


# ----------------------------------------------------------------- mentions --

def mentioned_users(text: str) -> list[dict]:
    """Active users named as @username in the text (case does not matter), in order, each once."""
    out, seen = [], set()
    for m in MENTION.finditer(text or ""):
        name = m.group(1).rstrip(".-")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        u = db.one("SELECT id, username, full_name FROM users WHERE username = ? COLLATE NOCASE AND active = 1", (name,))
        if u:
            out.append(u)
    return out


def _record_mentions(user: dict, comment_id: int, thread: dict, text: str, info: dict) -> set[int]:
    """Store new mentions of a comment and notify the people mentioned. Returns the ids notified."""
    already = {r["user_id"] for r in db.rows("SELECT user_id FROM discussion_mentions WHERE comment_id = ?", (comment_id,))}
    notified: set[int] = set()
    for u in mentioned_users(text):
        if u["id"] in already:
            continue
        db.execute("INSERT INTO discussion_mentions (comment_id, user_id) VALUES (?, ?)", (comment_id, u["id"]))
        if u["id"] == user["id"]:
            continue
        snippet = " ".join(text.split())
        snippet = snippet if len(snippet) <= 90 else snippet[:87] + "..."
        notifications.notify(u["id"], "mention", f"{user['full_name']} mentioned you on {info['label']}: \"{snippet}\"",
                             f"{info['link']}?thread={thread['id']}", user["id"])
        notified.add(u["id"])
    return notified


def _mentions_elsewhere(user: dict, ids: set[int], comment_id: int, thread: dict, text: str, info: dict) -> None:
    """After the comment is saved: the people mentioned also hear on the channels they chose (e-mail, chat).
    The bell was already written with the comment, so the outbox leaves it out."""
    if not ids:
        return
    from . import notify_outbox   # late import: the outbox loads the add-in registry
    notify_outbox.enqueue(sorted(ids), f"{user['full_name']} mentioned you on {info['label']}",
                          f"{user['full_name']} wrote:\n{text[:2000]}", f"{info['link']}?thread={thread['id']}",
                          "mentions", key=f"mention:{comment_id}", source=f"discussion:{thread['id']}", in_app=False)


# ------------------------------------------------------------------ actions --

def start(user: dict, entity_type: str, entity_id: int, body: str, title: str = "", revision: str | None = None) -> int:
    """Open a new thread with its first comment. Document threads belong to a revision (the current one by default)."""
    info = entity_info(entity_type, entity_id)
    text = _clean_body(body)
    title = " ".join((title or "").split())[:MAX_TITLE]
    rev = ""
    if entity_type == "document":
        rev = (revision or info["revision"]).strip().upper()
        known = {r["revision"] for r in db.rows("SELECT DISTINCT revision FROM versions WHERE document_id = ?",
                                                 (int(entity_id),))} | {info["revision"]}
        if rev not in known:
            raise bad(f"This file has no revision {rev}. Choose one of: {', '.join(sorted(known))}.")
    now = db.now()
    with db.tx():
        tid = db.insert("discussion_threads", {
            "entity_type": entity_type, "entity_id": int(entity_id), "revision": rev, "title": title, "status": "open",
            "created_by": user["id"], "created_at": now, "updated_at": now,
        })
        cid = db.insert("discussion_comments", {"thread_id": tid, "user_id": user["id"], "body": text, "created_at": now})
        thread = thread_or_404(tid)
        mentioned = _record_mentions(user, cid, thread, text, info)
        db.audit(user["id"], "discussion_start", "discussion", tid,
                 f"{entity_type} {entity_id}{' rev ' + rev if rev else ''}: {title or text[:60]}")
    _mentions_elsewhere(user, mentioned, cid, thread, text, info)
    return tid


def reply(user: dict, thread_id: int, body: str) -> int:
    t = thread_or_404(thread_id)
    if t["status"] == "resolved":
        raise conflict("This discussion is resolved. Reopen it to reply.", "resolved")
    info = entity_info(t["entity_type"], t["entity_id"])
    text = _clean_body(body)
    now = db.now()
    with db.tx():
        cid = db.insert("discussion_comments", {"thread_id": t["id"], "user_id": user["id"], "body": text,
                                                "created_at": now})
        db.update("discussion_threads", t["id"], {"updated_at": now})
        mentioned = _record_mentions(user, cid, t, text, info)
        # people already in the conversation hear about the reply (unless they were just mentioned anyway)
        others = [r["user_id"] for r in db.rows("SELECT DISTINCT user_id FROM discussion_comments WHERE thread_id = ? "
                                                 "AND deleted_at IS NULL", (t["id"],))]
        notifications.notify_many(others, "discussion_reply", f"{user['full_name']} replied in a discussion on "
                                  f"{info['label']}{': ' + t['title'] if t['title'] else ''}",
                                  f"{info['link']}?thread={t['id']}", user["id"], skip=[user["id"], *mentioned])
        db.audit(user["id"], "discussion_reply", "discussion", t["id"], f"comment {cid}")
    _mentions_elsewhere(user, mentioned, cid, t, text, info)
    return cid


def can_edit(user: dict, c: dict, now: datetime | None = None) -> dict:
    if c["user_id"] != user["id"]:
        return _ok(False, "You can only edit your own comments.")
    if c["deleted_at"]:
        return _ok(False, "This comment was removed.")
    created = _parse(c["created_at"])
    if created and (now or _utcnow()) - created > EDIT_WINDOW:
        return _ok(False, "Comments can be edited for 15 minutes after posting. Add a new comment instead.")
    return _ok(True)


def can_remove(user: dict, c: dict) -> dict:
    if c["user_id"] != user["id"]:
        return _ok(False, "You can only remove your own comments.")
    if c["deleted_at"]:
        return _ok(False, "This comment was already removed.")
    return _ok(True)


def edit(user: dict, comment_id: int, body: str) -> None:
    c = comment_or_404(comment_id)
    allowed = can_edit(user, c)
    if not allowed["ok"]:
        if c["user_id"] != user["id"]:
            raise forbidden(allowed["why"])
        raise conflict(allowed["why"], "removed" if c["deleted_at"] else "edit_window")
    text = _clean_body(body)
    if text == c["body"]:
        return
    t = thread_or_404(c["thread_id"])
    info = entity_info(t["entity_type"], t["entity_id"])
    now = db.now()
    with db.tx():
        db.insert("discussion_comment_edits", {"comment_id": c["id"], "body": c["body"], "replaced_at": now})
        db.update("discussion_comments", c["id"], {"body": text, "edited_at": now})
        _record_mentions(user, c["id"], t, text, info)
        db.audit(user["id"], "discussion_edit", "discussion", t["id"], f"comment {c['id']}")


def remove(user: dict, comment_id: int) -> None:
    """Soft delete: the comment shows as "comment removed"; its text stays in the database."""
    c = comment_or_404(comment_id)
    allowed = can_remove(user, c)
    if not allowed["ok"]:
        raise forbidden(allowed["why"]) if c["user_id"] != user["id"] else conflict(allowed["why"], "removed")
    with db.tx():
        db.update("discussion_comments", c["id"], {"deleted_at": db.now(), "deleted_by": user["id"]})
        db.audit(user["id"], "discussion_remove", "discussion", c["thread_id"], f"comment {c['id']}")


def can_resolve(user: dict, t: dict) -> dict:
    if t["created_by"] == user["id"] or ROLE_LEVEL[user["role"]] >= ROLE_LEVEL["engineer"]:
        return _ok(True)
    return _ok(False, "Only the person who started this discussion, or an engineer, can resolve or reopen it.")


def set_status(user: dict, thread_id: int, resolved: bool) -> None:
    t = thread_or_404(thread_id)
    allowed = can_resolve(user, t)
    if not allowed["ok"]:
        raise forbidden(allowed["why"])
    want = "resolved" if resolved else "open"
    if t["status"] == want:
        return
    now = db.now()
    with db.tx():
        db.update("discussion_threads", t["id"], {
            "status": want, "updated_at": now,
            "resolved_by": user["id"] if resolved else None, "resolved_at": now if resolved else None,
        })
        db.audit(user["id"], "discussion_resolve" if resolved else "discussion_reopen", "discussion", t["id"],
                 f"{t['entity_type']} {t['entity_id']}")


# ----------------------------------------------------------------- reading --

def threads(user: dict, entity_type: str, entity_id: int) -> dict:
    info = entity_info(entity_type, entity_id)
    names = {r["id"]: r for r in db.rows("SELECT id, username, full_name FROM users")}
    now = _utcnow()
    out = []
    for t in db.rows("SELECT * FROM discussion_threads WHERE entity_type = ? AND entity_id = ? "
                     "ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, updated_at DESC, id DESC",
                     (entity_type, int(entity_id))):
        comments = []
        for c in db.rows("SELECT * FROM discussion_comments WHERE thread_id = ? ORDER BY id", (t["id"],)):
            removed = bool(c["deleted_at"])
            created = _parse(c["created_at"])
            comments.append({
                "id": c["id"], "user_id": c["user_id"], "author": names.get(c["user_id"], {}).get("full_name", "?"),
                "username": names.get(c["user_id"], {}).get("username", ""),
                "body": REMOVED if removed else c["body"], "removed": removed,
                "created_at": c["created_at"], "edited_at": c["edited_at"], "deleted_at": c["deleted_at"],
                "editable_until": (created + EDIT_WINDOW).replace(microsecond=0).isoformat() if created else None,
                "can_edit": can_edit(user, c, now), "can_remove": can_remove(user, c),
                "mentions": [] if removed else [names[r["user_id"]]["username"] for r in db.rows(
                    "SELECT user_id FROM discussion_mentions WHERE comment_id = ?", (c["id"],)) if r["user_id"] in names],
            })
        resolved = t["status"] == "resolved"
        out.append({
            **t, "author": names.get(t["created_by"], {}).get("full_name", "?"),
            "resolved_by_name": names.get(t["resolved_by"], {}).get("full_name") if t["resolved_by"] else None,
            "comments": comments, "can_resolve": can_resolve(user, t),
            "can_reply": _ok(not resolved, "This discussion is resolved. Reopen it to reply."),
        })
    return {"entity_type": entity_type, "entity_id": int(entity_id), "label": info["label"], "link": info["link"],
            "current_revision": info["revision"], "threads": out,
            "open": sum(1 for t in out if t["status"] == "open"), "can_start": _ok(True)}
