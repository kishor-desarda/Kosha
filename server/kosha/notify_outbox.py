# SPDX-License-Identifier: AGPL-3.0-or-later
"""Notification outbox: who gets which notice on which channel, delivered in the background.

Any feature calls `enqueue(...)` after its own change is committed:

    from kosha import notify_outbox as outbox
    outbox.enqueue([user_id, ...], "ECO-0003 needs your approval", "Asha asks you to ...", "#/change/3", "approvals",
                   key="change.submitted:3:2026-10-13T09:00:00+00:00")
    outbox.enqueue("org.kosha.teams", "Nightly backup failed", "...")     # straight to one channel

`enqueue` only writes rows. It never talks to another system and never raises, so a broken mail
server or chat add-in can neither block nor undo the action behind a notice. The same `key` queued
twice is stored once (idempotent). The worker delivers due rows through their channel and, when a
delivery fails, tries again later with growing waits; every attempt is kept in the delivery log.

Every notice for people also appears under the bell in Kosha (kosha.notifications), whatever channels they
chose; the channels below are the extra ways to reach them. A feature that already wrote the bell itself
passes in_app=False.

Channels:
    email          built in (kosha.mailer): one message per person, to the address on their account
    <add-in id>    a notifier add-in (Teams, Slack, Telegram ...): one post per notice, naming the people
    others         added with register_channel()

Kinds are what people choose channels for under "My notifications" (KINDS; features may add their
own with register_kind).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from . import db, mailer, notifications
from .addins import registry

from kosha_sdk.notify import SEVERITIES, Notice, Recipient  # noqa: E402

log = logging.getLogger("kosha.notify")
SETTINGS_KEY = "notify"
MAX_ATTEMPTS = 6
SEND_TIMEOUT_SECONDS = 30
STUCK_MINUTES = 10
WORKER_SECONDS = float(os.environ.get("KOSHA_NOTIFY_WORKER_SECONDS", "15"))

# kind -> (label for people, who it is for, channels chosen until the person changes them)
KINDS: dict[str, tuple[str, str, list[str]]] = {
    "approvals": ("Change orders waiting for my approval", "approvers", ["email"]),
    "mentions": ("Someone mentions me", "everyone", ["email"]),
    "erp_failures": ("ERP transfers that failed", "approvers and admins", ["email"]),
    "releases": ("Newly released revisions", "everyone", []),
}


def register_kind(kind: str, label: str, audience: str = "everyone", default_channels: Iterable[str] = ()) -> None:
    """Let a feature add its own kind of notice to "My notifications"."""
    KINDS.setdefault(kind, (label, audience, list(default_channels)))


# ---------------------------------------------------------------- channels

@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    source: str            # email | addin | custom
    ready: bool
    why: str = ""          # why it cannot be used yet
    per_person: bool = True

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "source": self.source, "ready": self.ready, "why": self.why,
                "per_person": self.per_person}


@dataclass
class _Custom:
    name: str
    send: Callable[[Notice], None]
    per_person: bool
    ready: Callable[[], tuple[bool, str]] | None


_custom: dict[str, _Custom] = {}


def register_channel(channel_id: str, name: str, send: Callable[[Notice], None], *, per_person: bool = True,
                     ready: Callable[[], tuple[bool, str]] | None = None) -> None:
    """Hook for other delivery paths, for example in-app notifications:

        register_channel("in_app", "In Kosha", lambda notice: notifications.notify(...))

    `send` gets one Notice per person (per_person=True) with the person in `notice.recipients`, and the
    link as Kosha's own relative link ("#/change/3"). It runs in the outbox worker; raising retries later."""
    _custom[channel_id] = _Custom(name, send, per_person, ready)


def channels() -> list[Channel]:
    ok, why = mailer.configured()
    out = [Channel("email", "E-mail", "email", ok, why, True)]
    for a in registry.by_kind("notifier", enabled_only=False):
        m = a.manifest
        if not m:
            continue
        name = m.name.replace(" notifier", "")
        if a.status != "enabled":
            out.append(Channel(a.id, name, "addin", False, a.message or "Switched off under Admin › Add-ins.", False))
            continue
        values = registry.settings(a.id)
        missing = [s.label for s in m.settings if s.required and values.get(s.key) in (None, "")]
        why = f"Not set up yet: fill in {', '.join(missing)} under Admin › Add-ins › {m.name}." if missing else ""
        out.append(Channel(a.id, name, "addin", not missing, why, False))
    for cid, c in _custom.items():
        r, w = c.ready() if c.ready else (True, "")
        out.append(Channel(cid, c.name, "custom", r, w, c.per_person))
    return out


def base_url() -> str:
    return ((db.get_setting(SETTINGS_KEY) or {}).get("base_url") or "").strip().rstrip("/")


def save_settings(changes: dict) -> dict:
    cfg = dict(db.get_setting(SETTINGS_KEY) or {})
    if "base_url" in changes:
        url = " ".join(str(changes["base_url"] or "").split()).rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("Kosha's address must start with http:// or https://")
        cfg["base_url"] = url
    db.set_setting(SETTINGS_KEY, cfg)
    return cfg


def absolute_link(link: str) -> str:
    """Kosha's links are relative ("#/change/3"); other systems need the full address, or nothing."""
    if not link or link.startswith(("http://", "https://")):
        return link or ""
    base = base_url()
    if not base:
        return ""
    return f"{base}/{link.lstrip('/')}"


# ------------------------------------------------------------- preferences

def prefs(user_id: int) -> dict[str, list[str]]:
    out = {k: list(v[2]) for k, v in KINDS.items()}
    for r in db.rows("SELECT kind, channels_json FROM notify_prefs WHERE user_id = ?", (user_id,)):
        if r["kind"] in out:
            try:
                out[r["kind"]] = [c for c in json.loads(r["channels_json"]) if isinstance(c, str)]
            except ValueError:
                pass
    return out


def save_prefs(user_id: int, changes: dict) -> dict[str, list[str]]:
    known = {c.id for c in channels()}
    with db.tx():
        for kind, chans in (changes or {}).items():
            if kind not in KINDS:
                raise ValueError(f"Unknown kind of notice: {kind}")
            if not isinstance(chans, list):
                raise ValueError("Send a list of channels for each kind of notice.")
            clean = [c for c in dict.fromkeys(chans) if isinstance(c, str) and c in known]
            db.execute("INSERT INTO notify_prefs (user_id, kind, channels_json, updated_at) VALUES (?, ?, ?, ?) "
                       "ON CONFLICT(user_id, kind) DO UPDATE SET channels_json = excluded.channels_json, "
                       "updated_at = excluded.updated_at", (user_id, kind, json.dumps(clean), db.now()))
    return prefs(user_id)


def users_wanting(kind: str) -> list[int]:
    """Active users who chose at least one channel for this kind."""
    return [u["id"] for u in db.rows("SELECT id FROM users WHERE active = 1 ORDER BY id") if prefs(u["id"]).get(kind)]


# ------------------------------------------------------------------ enqueue

_wake = threading.Event()


def enqueue(target, title: str, body: str = "", link: str = "", kind: str = "general", *, severity: str = "info",
            key: str = "", source: str = "", in_app: bool = True) -> int:
    """Queue a notice for some users (a list of user ids) or for one channel (its id). Returns the number of
    deliveries queued (the bell in Kosha is not counted). Never raises: a problem is logged, and the action that
    called it carries on. Call it after the action's own transaction has committed."""
    try:
        n = _enqueue(target, title, body, link, kind, severity, key, source, in_app)
    except Exception:
        log.exception("Could not queue the notice %r; the action that caused it is not affected", title)
        return 0
    if n:
        _wake.set()
    return n


def _dedupe(notice_key: str, channel: str, user_id) -> str:
    return hashlib.sha256(f"{notice_key}|{channel}|{user_id or ''}".encode()).hexdigest()[:40]


def _enqueue(target, title, body, link, kind, severity, key, source, in_app=True) -> int:
    title = " ".join((title or "").split())[:300]
    if not title:
        raise ValueError("A notice needs a title")
    severity = severity if severity in SEVERITIES else "info"
    notice_key = key or f"auto:{uuid.uuid4().hex}"
    ready = {c.id: c for c in channels() if c.ready}
    planned: list[tuple[str, int | None, str, list[dict]]] = []      # channel, user, address, recipients
    users: list[dict] = []
    if isinstance(target, str):
        if target not in ready:
            log.info("Notice %r not queued: channel %s is not set up", title, target)
            return 0
        planned.append((target, None, "", []))
    else:
        ids = [int(u) for u in dict.fromkeys(target or []) if u is not None]
        users = db.rows(f"SELECT id, full_name, email FROM users WHERE active = 1 AND id IN ({','.join('?' * len(ids))})",
                        ids) if ids else []
        by_channel: dict[str, list[dict]] = {}
        for u in users:
            wanted = prefs(u["id"]).get(kind, ["email"]) if kind in KINDS else ["email"]
            for ch in wanted:
                if ch in ready:
                    by_channel.setdefault(ch, []).append(u)
        for ch, us in by_channel.items():
            people = [{"user_id": u["id"], "name": u["full_name"], "email": u["email"] or ""} for u in us]
            if ready[ch].per_person:
                for p in people:
                    if ch == "email" and not p["email"]:
                        log.info("Notice %r: %s has no e-mail address", title, p["name"])
                        continue
                    planned.append((ch, p["user_id"], p["email"] if ch == "email" else "", [p]))
            else:
                planned.append((ch, None, "", people))
    now = db.now()
    queued = 0
    with db.tx() as conn:
        for ch, uid, address, people in planned:
            cur = conn.execute(
                "INSERT OR IGNORE INTO notify_outbox (dedupe_key, notice_key, kind, channel, user_id, address, "
                "recipients_json, title, body, link, severity, source, status, attempts, next_attempt_at, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
                (_dedupe(notice_key, ch, uid), notice_key, kind, ch, uid, address, json.dumps(people), title,
                 (body or "")[:20000], (link or "")[:1000], severity, (source or "")[:200], now, now, now))
            queued += cur.rowcount
        if in_app and not isinstance(target, str):
            for u in users:      # the bell in Kosha, whatever extra channels people chose
                notifications.notify(u["id"], kind, title, link, key=notice_key)
    return queued


# ----------------------------------------------------------------- delivery

def _notice(row: dict, for_custom: bool = False) -> Notice:
    people = json.loads(row["recipients_json"] or "[]")
    recipients = tuple(Recipient(p.get("name") or "", p.get("email") or "", p.get("user_id")) for p in people)
    if row["channel"] == "email":
        recipients = tuple(replace(r, email=row["address"]) for r in recipients) or \
            (Recipient("", row["address"], row["user_id"]),)
    return Notice(title=row["title"], body=row["body"], link=row["link"] if for_custom else absolute_link(row["link"]),
                  severity=row["severity"], kind=row["kind"], recipients=recipients, key=row["notice_key"])


def _send(row: dict) -> None:
    ch = row["channel"]
    if ch == "email":
        mailer.send(_notice(row))
    elif ch in _custom:
        _custom[ch].send(_notice(row, for_custom=True))
    else:
        registry.call(ch, "send", _notice(row), timeout=SEND_TIMEOUT_SECONDS)


def backoff_seconds(attempts: int) -> float:
    """30 s, 2 min, 8 min, 32 min, about 2 h, then 6 h; a little random so retries do not bunch up."""
    return min(30.0 * 4 ** max(0, attempts - 1), 6 * 3600.0) * random.uniform(0.85, 1.15)


def _later(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def deliver(row: dict) -> dict:
    """Try one delivery now and record the outcome. Never raises."""
    attempts = row["attempts"] + 1
    db.update("notify_outbox", row["id"], {"status": "sending", "attempts": attempts, "updated_at": db.now()})
    t0 = time.monotonic()
    try:
        _send(row)
    except Exception as e:      # an add-in's NotifyError, AddinUnavailable, a mail error: all kept for a retry
        msg = (str(e) or e.__class__.__name__)[:1000]
        final = attempts >= MAX_ATTEMPTS
        db.update("notify_outbox", row["id"], {
            "status": "failed" if final else "pending", "last_error": msg, "updated_at": db.now(),
            "next_attempt_at": db.now() if final else _later(backoff_seconds(attempts))})
        _log_attempt(row["id"], False, msg, t0)
        log.info("Notice #%s to %s failed (attempt %s): %s", row["id"], row["channel"], attempts, msg)
    else:
        db.update("notify_outbox", row["id"], {"status": "sent", "last_error": "", "sent_at": db.now(),
                                               "updated_at": db.now()})
        _log_attempt(row["id"], True, "Delivered", t0)
    return db.one("SELECT * FROM notify_outbox WHERE id = ?", (row["id"],))


def _log_attempt(outbox_id: int, ok: bool, message: str, t0: float) -> None:
    db.insert("notify_attempts", {"outbox_id": outbox_id, "at": db.now(), "ok": 1 if ok else 0, "message": message,
                                  "duration_ms": int((time.monotonic() - t0) * 1000)})


_lock = threading.Lock()


def process_due(limit: int = 50) -> int:
    """Deliver every notice that is due. Returns how many were tried (0 when another run is busy)."""
    if not _lock.acquire(blocking=False):
        return 0
    try:
        stuck = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_MINUTES)).replace(microsecond=0).isoformat()
        db.execute("UPDATE notify_outbox SET status = 'pending' WHERE status = 'sending' AND updated_at < ?", (stuck,))
        due = db.rows("SELECT * FROM notify_outbox WHERE status = 'pending' AND next_attempt_at <= ? ORDER BY id LIMIT ?",
                      (db.now(), limit))
        for row in due:
            deliver(row)
        return len(due)
    finally:
        _lock.release()


def retry(outbox_id: int) -> dict | None:
    """Send a failed or waiting notice again now (admin's "Send again")."""
    row = db.one("SELECT * FROM notify_outbox WHERE id = ?", (outbox_id,))
    if not row:
        return None
    if row["status"] in ("pending", "failed"):
        db.update("notify_outbox", outbox_id, {"status": "pending", "attempts": 0, "next_attempt_at": db.now(),
                                               "updated_at": db.now()})
        process_due()            # delivers it now, unless the worker is busy delivering (then it does so next)
        _wake.set()
    return db.one("SELECT * FROM notify_outbox WHERE id = ?", (outbox_id,))


def start_worker() -> threading.Thread:
    def loop():
        while True:
            _wake.wait(WORKER_SECONDS)
            _wake.clear()
            try:
                while process_due() >= 50:
                    pass
            except Exception:  # pragma: no cover
                log.exception("Notification worker error")

    t = threading.Thread(target=loop, name="notify-worker", daemon=True)
    t.start()
    return t


# -------------------------------------------------------------- delivery log

def log_rows(status: str | None = None, limit: int = 200) -> dict:
    where, params = "", []
    if status:
        where, params = " WHERE status = ?", [status]
    rows = db.rows(f"SELECT * FROM notify_outbox{where} ORDER BY id DESC LIMIT ?", [*params, min(int(limit), 1000)])
    names = {c.id: c.name for c in channels()}
    for r in rows:
        r["channel_name"] = names.get(r["channel"], r["channel"])
        r["recipients"] = [p.get("name") for p in json.loads(r.pop("recipients_json") or "[]")]
        r["attempt_log"] = db.rows("SELECT at, ok, message, duration_ms FROM notify_attempts WHERE outbox_id = ? "
                                   "ORDER BY id DESC LIMIT 10", (r["id"],))
    counts = {s: db.scalar("SELECT COUNT(*) FROM notify_outbox WHERE status = ?", (s,))
              for s in ("pending", "sending", "sent", "failed")}
    return {"rows": rows, "counts": counts}
