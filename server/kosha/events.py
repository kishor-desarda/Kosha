# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kosha's events, for other programs and for open screens (specification 13.4).

Everything that happens (an item created, a file checked in, a revision released, a change order approved ...) is
published once, after it is committed, through ``addins.registry.emit``. From there:

* **Webhooks.** An admin registers an address and the events it wants. Each event becomes a delivery in
  ``webhook_deliveries``, sent in the background as a JSON POST signed with HMAC-SHA256
  (``X-Kosha-Signature: t=<unix time>,v1=<hex of HMAC(secret, "<t>.<body>")>``), retried with growing waits
  (1 minute up to 12 hours, six tries), and logged. An admin can send any delivery again (replay).
* **Live screens.** Browsers keep one connection open (``GET /api/events/stream``, server-sent events) and refresh
  what they show when someone else checks in, releases or approves. Only the event name and record ids travel on it;
  the screen then asks for the record with its own permissions.

Outside calls never block the action behind the event: they happen in the background with a time-out.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import db
from .web import bad, not_found

log = logging.getLogger("kosha.events")

EVENTS = {
    "item.created": "A part (item) was created",
    "item.updated": "A part's properties changed",
    "file.checked_in": "A file was checked in (new version)",
    "file.checked_out": "A file was taken for editing",
    "revision.released": "A revision was released",
    "revision.obsoleted": "A revision was made obsolete or spares only",
    "bom.released": "A BOM was released",
    "ecr.raised": "A change request was raised",
    "eco.submitted": "A change order was sent for approval",
    "eco.approved": "A change order was approved by everyone",
    "eco.implemented": "A change order was implemented",
    "deviation.approved": "A deviation was approved",
    "comment.added": "A comment was added to a discussion",
}
ALIASES = {"change.submitted": "eco.submitted", "change.implemented": "eco.implemented"}
RETRY_MINUTES = (1, 5, 30, 120, 720)
MAX_ATTEMPTS = len(RETRY_MINUTES) + 1
TIMEOUT_SECONDS = 10
_process_lock = threading.Lock()


# --------------------------------------------------------------- publishing --

def on_event(event: dict) -> None:
    """Registry listener for every event: webhooks and live screens."""
    name = ALIASES.get(event.get("event", ""), event.get("event", ""))
    if name not in EVENTS:
        return
    data = {k: v for k, v in event.items() if k != "event"}
    live.publish(name, data)
    queue_deliveries(name, data)


def register() -> None:
    from .addins import registry
    registry.listen("*", on_event)


def queue_deliveries(name: str, data: dict) -> int:
    hooks = [h for h in db.rows("SELECT * FROM webhooks WHERE active = 1 AND removed_at IS NULL")
             if name in json.loads(h["events_json"] or "[]")]
    if not hooks:
        return 0
    event_id = "evt_" + secrets.token_hex(10)
    body = {"id": event_id, "event": name, "at": db.now(), "data": data}
    with db.tx():
        for h in hooks:
            db.insert("webhook_deliveries", {"webhook_id": h["id"], "event": name, "event_id": event_id,
                                             "payload_json": json.dumps(body, default=str), "status": "pending",
                                             "next_at": db.now(), "created_at": db.now()})
    _wake()
    return len(hooks)


def _wake() -> None:
    try:
        from .notify_outbox import _wake as w
        w.set()
    except Exception:
        pass


# ----------------------------------------------------------------- webhooks --

def sign(secret: str, body: bytes, ts: int | None = None) -> str:
    ts = int(ts if ts is not None else time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _check_url(url: str) -> str:
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise bad("The address must start with https:// (or http:// inside the company network).")
    if parts.username or parts.password:
        raise bad("Put no user name or password in the address; Kosha signs each call instead.")
    return url[:500]


def _check_events(events) -> list[str]:
    if not isinstance(events, list) or not events or any(e not in EVENTS for e in events):
        raise bad("Choose at least one event: " + ", ".join(EVENTS) + ".")
    return sorted(set(events))


def hooks() -> list[dict]:
    out = []
    for h in db.rows("SELECT * FROM webhooks WHERE removed_at IS NULL ORDER BY id"):
        counts = {s: db.scalar("SELECT COUNT(*) FROM webhook_deliveries WHERE webhook_id = ? AND status = ?",
                               (h["id"], s)) for s in ("pending", "sent", "failed")}
        h.pop("secret", None)
        out.append({**h, "events": json.loads(h.pop("events_json") or "[]"), "counts": counts})
    return out


def add_hook(user_id: int, body: dict) -> dict:
    name = " ".join(str(body.get("name") or "").split())[:80]
    if not name:
        raise bad("Name the receiving program, for example \"Shop-floor screens\".")
    secret = "whsec_" + secrets.token_urlsafe(24)
    with db.tx():
        hid = db.insert("webhooks", {"name": name, "url": _check_url(body.get("url")),
                                     "events_json": json.dumps(_check_events(body.get("events"))), "secret": secret,
                                     "active": 1, "created_by": user_id, "created_at": db.now()})
        db.audit(user_id, "webhook_add", "webhook", hid, name)
    return {**next(h for h in hooks() if h["id"] == hid), "secret": secret}


def _hook_or_404(hid: int) -> dict:
    h = db.one("SELECT * FROM webhooks WHERE id = ? AND removed_at IS NULL", (hid,))
    if not h:
        raise not_found("Webhook not found")
    return h


def update_hook(user_id: int, hid: int, body: dict) -> dict:
    _hook_or_404(hid)
    upd: dict = {}
    if "name" in body:
        upd["name"] = " ".join(str(body["name"] or "").split())[:80] or "Webhook"
    if "url" in body:
        upd["url"] = _check_url(body["url"])
    if "events" in body:
        upd["events_json"] = json.dumps(_check_events(body["events"]))
    if "active" in body:
        upd["active"] = 1 if body["active"] else 0
    if upd:
        with db.tx():
            db.update("webhooks", hid, upd)
            db.audit(user_id, "webhook_change", "webhook", hid, ",".join(upd))
    return next(h for h in hooks() if h["id"] == hid)


def remove_hook(user_id: int, hid: int) -> None:
    h = _hook_or_404(hid)
    with db.tx():       # kept with its log; waiting deliveries are dropped
        db.update("webhooks", hid, {"active": 0, "removed_at": db.now()})
        db.execute("UPDATE webhook_deliveries SET status = 'cancelled' WHERE webhook_id = ? AND status = 'pending'",
                   (hid,))
        db.audit(user_id, "webhook_remove", "webhook", hid, h["name"])


def deliveries(hid: int | None = None, limit: int = 100) -> list[dict]:
    rows = db.rows("SELECT d.id, d.webhook_id, d.event, d.event_id, d.status, d.attempts, d.next_at, d.last_status, "
                   "d.last_error, d.created_at, d.sent_at, d.replay_of, w.name AS webhook FROM webhook_deliveries d "
                   "JOIN webhooks w ON w.id = d.webhook_id" + (" WHERE d.webhook_id = ?" if hid else "") +
                   " ORDER BY d.id DESC LIMIT ?", (hid, limit) if hid else (limit,))
    return rows


def replay(user_id: int, delivery_id: int) -> dict:
    d = db.one("SELECT * FROM webhook_deliveries WHERE id = ?", (delivery_id,))
    if not d:
        raise not_found("Delivery not found")
    _hook_or_404(d["webhook_id"])
    with db.tx():
        nid = db.insert("webhook_deliveries", {"webhook_id": d["webhook_id"], "event": d["event"],
                                               "event_id": d["event_id"], "payload_json": d["payload_json"],
                                               "status": "pending", "next_at": db.now(), "created_at": db.now(),
                                               "replay_of": d["id"]})
        db.audit(user_id, "webhook_replay", "webhook", d["webhook_id"], f"{d['event']} {d['event_id']}")
    _wake()
    return db.one("SELECT * FROM webhook_deliveries WHERE id = ?", (nid,))


def _send(d: dict) -> None:
    h = db.one("SELECT * FROM webhooks WHERE id = ?", (d["webhook_id"],))
    attempts = d["attempts"] + 1
    body = d["payload_json"].encode()
    status, error = None, ""
    try:
        req = urllib.request.Request(h["url"], data=body, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": "Kosha-Webhooks/1", "X-Kosha-Event": d["event"],
            "X-Kosha-Delivery": str(d["id"]), "X-Kosha-Event-Id": d["event_id"],
            "X-Kosha-Signature": sign(h["secret"], body)})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status, error = e.code, f"The receiver answered {e.code}."
    except Exception as e:          # unreachable, refused, timed out: tried again later
        error = f"Could not reach the receiver: {e.__class__.__name__}: {str(e)[:200]}"
    ok = status is not None and 200 <= status < 300
    if ok:
        upd = {"status": "sent", "attempts": attempts, "last_status": status, "last_error": "", "sent_at": db.now()}
    elif attempts >= MAX_ATTEMPTS:
        upd = {"status": "failed", "attempts": attempts, "last_status": status, "last_error": error}
    else:
        wait = RETRY_MINUTES[attempts - 1]
        nxt = (datetime.now(timezone.utc) + timedelta(minutes=wait)).replace(microsecond=0).isoformat()
        upd = {"status": "pending", "attempts": attempts, "last_status": status, "last_error": error, "next_at": nxt}
    db.update("webhook_deliveries", d["id"], upd)


def process_due(limit: int = 50) -> int:
    """Send every delivery that is due. Returns how many were tried (0 when another run is busy)."""
    if not _process_lock.acquire(blocking=False):
        return 0
    try:
        rows = db.rows("SELECT * FROM webhook_deliveries WHERE status = 'pending' AND next_at <= ? ORDER BY id "
                       "LIMIT ?", (db.now(), limit))
        for d in rows:
            try:
                _send(d)
            except Exception:       # pragma: no cover - one bad row never stops the others
                log.exception("Webhook delivery %s failed unexpectedly", d["id"])
        return len(rows)
    finally:
        _process_lock.release()


# ------------------------------------------------------------- live screens --

class Live:
    """Open browser connections. Events reach each one as a small message: the event name and record ids."""

    MAX_PER_USER = 8

    def __init__(self):
        self._lock = threading.Lock()
        self._subs: dict[int, tuple] = {}
        self._next = 0

    def subscribe(self, user_id: int) -> tuple[int, asyncio.Queue]:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            mine = [k for k, (_, _, uid) in self._subs.items() if uid == user_id]
            for k in mine[:max(0, len(mine) - self.MAX_PER_USER + 1)]:    # oldest connections give way
                self._subs.pop(k, None)
            self._next += 1
            self._subs[self._next] = (loop, q, user_id)
            return self._next, q

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)

    def count(self) -> int:
        return len(self._subs)

    def publish(self, name: str, data: dict) -> None:
        msg = {"event": name, **{k: v for k, v in data.items() if k == "user_id" or k.endswith("_id") or k == "id"}}
        with self._lock:
            subs = list(self._subs.values())
        for loop, q, _ in subs:
            try:
                loop.call_soon_threadsafe(_offer, q, msg)
            except RuntimeError:        # that connection's loop has closed
                pass


def _offer(q: asyncio.Queue, msg: dict) -> None:
    try:
        q.put_nowait(msg)
    except asyncio.QueueFull:           # a stalled screen misses events; it refreshes on its own later
        pass


live = Live()


async def stream(user_id: int):
    """The server-sent events of one browser connection."""
    sub_id, q = live.subscribe(user_id)
    try:
        yield "retry: 5000\n\n"
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25)
            except asyncio.TimeoutError:
                yield ": still here\n\n"
                continue
            yield f"event: {msg['event']}\ndata: {json.dumps(msg)}\n\n"
    finally:
        live.unsubscribe(sub_id)
