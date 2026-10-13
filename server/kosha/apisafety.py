# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rules that make the API safe for programs (specification 13.1).

* **Versioned path:** ``/api/v1/...`` reaches the same operations as ``/api/...`` (the browser's path).
* **Request IDs:** every answer carries ``X-Request-ID`` (the caller's own, when it sends a sensible one), and every
  error names it, so a person can quote it and an admin can find it in the server log.
* **One error format:** ``{"error": plain message, "code": machine code, "request_id": ..., details...}``.
* **No lost updates:** records people edit carry ``row_version``. An update that sends the version it read in
  ``If-Match`` is refused (412) when someone changed the record in between, naming who and when.
* **Safe retries:** a POST with an ``Idempotency-Key`` header does its work once; sending it again returns the
  first answer. The same key with a different request is refused. Keys are kept for a day.
* **Limits:** each account (or address, before sign-in) may make ``api_rate_limit_per_minute`` calls a minute
  (setting; 0 switches the limit off). Past it, the answer is 429 with ``Retry-After``.
* **Cursor paging** for the long lists: ``?cursor=`` (empty for the first page) and ``limit``; the answer's
  ``next_cursor`` asks for the next page and is ``null`` on the last one.
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
IDEM_KEY = re.compile(r"^[\x21-\x7e]{1,200}$")
KEEP_KEYS = timedelta(hours=24)
DEFAULT_RATE = 1200
request_id: contextvars.ContextVar[str] = contextvars.ContextVar("kosha_request_id", default="")


# ------------------------------------------------------------- middleware --

class ApiMiddleware:
    """Gives each request its ID (also in the answer's headers) and serves /api/v1/... from /api/..."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = ""
        for k, v in scope.get("headers") or []:
            if k == b"x-request-id":
                rid = v.decode("latin-1")
                break
        if not REQUEST_ID.match(rid):
            rid = secrets.token_hex(8)
        path = scope.get("path", "")
        if path == "/api/v1" or path.startswith("/api/v1/"):
            new = "/api" + path[len("/api/v1"):]
            scope = {**scope, "path": new, "raw_path": new.encode("utf-8")}
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id.set(rid)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", rid.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id.reset(token)


def current_request_id(request=None) -> str:
    if request is not None:
        rid = getattr(getattr(request, "state", None), "request_id", None)
        if rid:
            return rid
    return request_id.get()


# ------------------------------------------------------------- rate limit --

_lock = threading.Lock()
_buckets: dict[str, tuple[int, int]] = {}
_rate_cache: list[float] = [0.0, float(DEFAULT_RATE)]


def rate_limit() -> int:
    now = time.monotonic()
    if now - _rate_cache[0] > 15:
        try:
            v = db.get_setting("api_rate_limit_per_minute")
            _rate_cache[1] = float(DEFAULT_RATE if v is None else v)
        except Exception:       # never refuse work because the setting could not be read
            pass
        _rate_cache[0] = now
    return int(_rate_cache[1])


def forget_rate_setting() -> None:
    _rate_cache[0] = 0.0


def check_rate(key: str) -> None:
    from .web import ApiError
    limit = rate_limit()
    if limit <= 0:
        return
    now = time.time()
    window = int(now // 60)
    with _lock:
        w, n = _buckets.get(key, (window, 0))
        n = n + 1 if w == window else 1
        _buckets[key] = (window, n)
        if len(_buckets) > 50000:           # forget old windows
            for k in [k for k, (ww, _) in _buckets.items() if ww != window]:
                _buckets.pop(k, None)
    if n > limit:
        wait = max(1, 60 - int(now % 60))
        raise ApiError(429, f"Too many requests from this account in one minute (the limit is {limit}). Wait "
                            f"{wait} seconds and try again; nothing was changed.", "rate_limited",
                       {"retry_after": wait})


# ------------------------------------------------------------ idempotency --

def fingerprint(method: str, path: str, body: Any, files: dict) -> str:
    h = hashlib.sha256()
    if isinstance(body, (bytes, bytearray)):
        body = {"_bytes": hashlib.sha256(body).hexdigest()}
    h.update(json.dumps([method, path, body], sort_keys=True, default=str).encode())
    for name in sorted(files or {}):
        for f in files[name]:
            fh = f.file
            pos = fh.tell()
            fh.seek(0)
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
            fh.seek(pos)
            h.update(f"{name}:{f.filename}".encode())
    return h.hexdigest()


def begin(user_id: int, key: str, method: str, path: str, fp: str) -> dict | None:
    """Before running a POST with an Idempotency-Key: the earlier answer to replay, or None to go ahead (the key
    is then held until ``finish``)."""
    from .web import ApiError, bad, conflict
    if not IDEM_KEY.match(key):
        raise bad("The Idempotency-Key must be 1 to 200 visible characters.", "bad_idempotency_key")
    cutoff = (datetime.now(timezone.utc) - KEEP_KEYS).replace(microsecond=0).isoformat()
    with db.tx():
        db.execute("DELETE FROM api_idempotency WHERE created_at < ?", (cutoff,))
        row = db.one("SELECT * FROM api_idempotency WHERE user_id = ? AND idem_key = ?", (user_id, key))
        if not row:
            db.insert("api_idempotency", {"user_id": user_id, "idem_key": key, "method": method, "path": path,
                                          "fingerprint": fp, "status": 0, "created_at": db.now()})
            return None
    if row["fingerprint"] != fp:
        raise ApiError(422, "This Idempotency-Key was already used for a different request. Use a new key for a "
                            "new request; nothing was changed.", "idempotency_key_reused")
    if not row["status"]:
        raise conflict("The same request is still being worked on. Wait a moment and send it again.",
                       "idempotency_in_progress", retry_after=2)
    return {"status": row["status"], "body": json.loads(row["response_json"] or "null")}


def finish(user_id: int, key: str, status: int, body: Any) -> None:
    """Keep a successful answer for replays; let go of the key otherwise, so the caller can try again."""
    try:
        with db.tx():
            if 200 <= status < 300 and body is not None:
                db.execute("UPDATE api_idempotency SET status = ?, response_json = ? WHERE user_id = ? AND "
                           "idem_key = ?", (status, json.dumps(body, default=str), user_id, key))
            else:
                db.execute("DELETE FROM api_idempotency WHERE user_id = ? AND idem_key = ? AND status = 0",
                           (user_id, key))
    except Exception:       # the work itself is done (or failed) either way; only the replay record is missing
        import logging
        logging.getLogger("kosha").exception("Could not keep the answer for Idempotency-Key %r", key)


# --------------------------------------------------------------- versions --

def if_match(req) -> int | None:
    raw = (req.request.headers.get("if-match") or "").strip()
    if not raw or raw == "*":
        return None
    m = re.fullmatch(r'(?:W/)?"?(\d{1,12})"?', raw)
    if not m:
        from .web import bad
        raise bad('If-Match must be the row_version you read, for example If-Match: "3".', "bad_if_match")
    return int(m.group(1))


def check_version(req, table: str, row: dict, what: str) -> None:
    """Refuse an update sent for an older version of the record (412), naming who changed it and when."""
    want = if_match(req)
    if want is None or int(row.get("row_version") or 1) == want:
        return
    from .lifecycle import user_names
    from .web import ApiError
    who = user_names().get(row.get("updated_by"), "Someone") if row.get("updated_by") else "Someone"
    when = ""
    if row.get("updated_at"):
        try:
            when = " at " + datetime.fromisoformat(row["updated_at"]).strftime("%H:%M UTC on %d %b")
        except ValueError:
            pass
    raise ApiError(412, f"{who} changed {what}{when}, after you opened it. Reload and try again; your changes were "
                        "not saved.", "changed_meanwhile", {"row_version": int(row.get("row_version") or 1)})


def guard(req, table: str, row_id: int, what: str) -> None:
    """Before an update: refuse it when the caller sent If-Match for an older version."""
    if req.request.headers.get("if-match"):
        row = db.one(f"SELECT id, row_version, updated_by, updated_at FROM {table} WHERE id = ?", (row_id,))
        if row:
            check_version(req, table, row, what)


def bump(table: str, row_id: int, user_id: int | None) -> None:
    """A person or program changed the record: next version, and who and when."""
    db.execute(f"UPDATE {table} SET row_version = row_version + 1, updated_by = ?, updated_at = ? WHERE id = ?",
               (user_id, db.now(), row_id))


# ----------------------------------------------------------------- paging --

def cursor_args(req, default_limit: int = 100, max_limit: int = 1000) -> tuple[bool, int | None, int]:
    """(cursor paging asked for, the id to continue below, page size)."""
    from .web import bad
    try:
        limit = max(1, min(int(req.q("limit", default_limit)), max_limit))
    except (TypeError, ValueError):
        raise bad("limit must be a number.") from None
    if "cursor" not in req.query:
        return False, None, limit
    raw = req.q("cursor") or ""
    if not raw:
        return True, None, limit
    try:
        text = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        if not text.startswith("id:"):
            raise ValueError
        return True, int(text[3:]), limit
    except (ValueError, UnicodeDecodeError):
        raise bad("This cursor is not valid. Start again without it.", "bad_cursor") from None


def next_cursor(rows: list[dict], limit: int) -> tuple[list[dict], str | None]:
    """Rows were read with LIMIT limit + 1: the page and the cursor of the next one."""
    if len(rows) <= limit:
        return rows, None
    page = rows[:limit]
    return page, base64.urlsafe_b64encode(f"id:{page[-1]['id']}".encode()).decode().rstrip("=")
