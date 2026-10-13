# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tiny routing layer on top of Starlette.

Handlers are plain (synchronous) functions taking a `Req`.  The async wrapper
reads the body first and then runs the handler in a worker thread, so slow
work (CAD conversion, similarity analysis) never blocks the server.
"""
from __future__ import annotations

import inspect
import json
import logging
import secrets
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import db, sessions, uploads

log = logging.getLogger("kosha")

# the main roles, each able to do everything the ones below can; extra roles are in access.EXTRA_ROLES
ROLE_LEVEL = {"viewer": 0, "engineer": 1, "checker": 2, "approver": 3, "admin": 4}

routes: list[Route] = []


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "error", extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.extra = extra or {}


def bad(message: str, code: str = "bad_request", **extra) -> ApiError:
    return ApiError(400, message, code, extra)


def not_found(what: str = "Not found") -> ApiError:
    return ApiError(404, what, "not_found")


def conflict(message: str, code: str = "conflict", **extra) -> ApiError:
    return ApiError(409, message, code, extra)


def forbidden(message: str = "You don't have permission to do that.") -> ApiError:
    return ApiError(403, message, "forbidden")


@dataclass
class Req:
    request: Request
    user: dict | None
    params: dict
    query: dict
    body: Any = None
    form: Any = None
    files: dict = field(default_factory=dict)

    @property
    def uid(self) -> int | None:
        return self.user["id"] if self.user else None

    def require(self, role: str) -> None:
        if not self.has(role):
            raise forbidden(_needs(role))

    def has(self, role: str) -> bool:
        from .access import has_role
        return has_role(self.user, role)

    def int_param(self, name: str) -> int:
        try:
            return int(self.params[name])
        except (KeyError, ValueError):
            raise bad(f"Invalid {name}")

    def q(self, name: str, default: Any = None) -> Any:
        return self.query.get(name, default)

    def b(self, name: str, default: Any = None) -> Any:
        if isinstance(self.body, dict):
            return self.body.get(name, default)
        if self.form is not None:
            v = self.form.get(name)
            return default if v is None else v
        return default


def _needs(role: str) -> str:
    from .access import ROLE_NAMES
    name = ROLE_NAMES.get(role, role)
    if role in ROLE_LEVEL:
        return f"This needs the {name} role or higher."
    return f"This needs the {name} role. Ask your admin to add it to your account."


def _authenticate(request: Request) -> dict | None:
    """The signed-in user for a request (Bearer header, or access_token for links), when the session is open."""
    token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.headers.get("x-api-key", "").strip() or None
    from . import apikeys
    if not token:
        token = request.query_params.get("access_token")
        if token and token.startswith(apikeys.PREFIX):     # keys travel in a header, never in an address
            return None
    if not token:
        return None
    if token.startswith(apikeys.PREFIX):          # a program with an API key (service account)
        user = apikeys.authenticate(token, request.client.host if request.client else "")
        if user:
            user["roles"] = []
        return user
    # background polls (X-Kosha-Background) do not count as activity for the idle limit
    found = sessions.check(token, touch=not request.headers.get("x-kosha-background"))
    if found is None:
        return None
    user_id, request.state.session_id = found
    user = db.one("SELECT id, username, full_name, email, role, ui_mode, ui_theme, active, kind, view_only, "
                  "expires_on FROM users WHERE id = ?", (user_id,))
    if not user or not user["active"]:
        return None
    from . import access
    if access.sign_in_problem(user):      # an account past its end date stops working at once
        return None
    user["roles"] = access.extra_roles(user["id"])
    return user


def api(method: str, path: str, role: str | None = "viewer", auth: bool = True):
    """Register a handler. role=None with auth=False makes it public."""

    def deco(fn: Callable[[Req], Any]):
        async def endpoint(request: Request) -> Response:
            from . import apisafety
            form = None
            idem = None         # (user id, Idempotency-Key) while this request holds the key
            try:
                # in a worker thread: checking the session may write (last activity), which must never block the loop
                user = await run_in_threadpool(_authenticate, request) if auth else None
                client = request.client.host if request.client else "?"
                await run_in_threadpool(apisafety.check_rate, f"u{user['id']}" if user else f"ip{client}")
                if auth and user is None:
                    raise ApiError(401, "Please sign in.", "unauthorized")
                if user is not None and user.get("kind") == "vendor" and not _vendor_may(method, path):
                    raise forbidden("A vendor account can only open the parts and files of its projects.")
                if user is not None and "api_scopes" in user:
                    from .apikeys import allowed
                    why = allowed(user, method, path)
                    if why:
                        raise ApiError(403, why, "scope_missing")
                if role and auth and not Req(request, user, {}, {}).has(role):   # before reading any upload
                    raise forbidden(_needs(role))
                body = None
                files: dict = {}
                ctype = request.headers.get("content-type", "")
                if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                    request = uploads.limited(request)
                    if ctype.startswith("multipart/form-data"):
                        form = await request.form(max_files=500, max_fields=2000, max_part_size=64 * 1024 * 1024)
                        for key, value in form.multi_items():
                            if hasattr(value, "filename") and hasattr(value, "file"):
                                files.setdefault(key, []).append(value)
                        if files:
                            await run_in_threadpool(uploads.scan_files, files, user["id"] if user else None, path)
                    elif ctype.startswith("application/json"):
                        raw = await request.body()
                        body = json.loads(raw) if raw else {}
                    elif ctype.startswith("application/octet-stream"):     # a piece of a resumable upload
                        body = await request.body()
                    else:
                        raw = await request.body()
                        if raw:
                            try:
                                body = json.loads(raw)
                            except ValueError:
                                body = raw
                req = Req(request, user, dict(request.path_params), dict(request.query_params), body, form, files)
                if role and auth:
                    req.require(role)
                key = request.headers.get("idempotency-key")
                if key is not None and method == "POST" and user is not None:
                    fp = await run_in_threadpool(apisafety.fingerprint, method, path, body, files)
                    done = await run_in_threadpool(apisafety.begin, user["id"], key.strip(), method, path, fp)
                    if done is not None:
                        return JSONResponse(done["body"], status_code=done["status"],
                                            headers={"Idempotent-Replayed": "true"})
                    idem = (user["id"], key.strip())
                if inspect.iscoroutinefunction(fn):
                    from .access import current
                    tok = current.set(user)
                    try:
                        result = await fn(req)
                    finally:
                        current.reset(tok)
                else:
                    result = await run_in_threadpool(_run_sync, fn, req)
                if isinstance(result, Response):
                    if idem:
                        await run_in_threadpool(apisafety.finish, *idem, 0, None)
                    return result
                payload = result if result is not None else {"ok": True}
                if idem:
                    await run_in_threadpool(apisafety.finish, *idem, 200, payload)
                return JSONResponse(payload)
            except (ApiError, uploads.UploadRefused) as e:
                if idem:
                    await run_in_threadpool(apisafety.finish, *idem, e.status, None)
                headers = {"Retry-After": str(e.extra["retry_after"])} if "retry_after" in e.extra else None
                return JSONResponse(error_body(e.message, e.code, request, e.extra), status_code=e.status,
                                    headers=headers)
            except Exception:  # pragma: no cover - logged for admins with a reference the user can quote
                if idem:
                    await run_in_threadpool(apisafety.finish, *idem, 500, None)
                ref = secrets.token_hex(3).upper()
                rid = apisafety.current_request_id(request)
                log.error("Unhandled error ref %s (request %s) on %s %s\n%s", ref, rid, method, path,
                          traceback.format_exc())
                return JSONResponse(error_body("Something went wrong on our side. Your work is safe and nothing was "
                                               f"changed. If it happens again, tell your admin: ref {ref}.",
                                               "server_error", request, {"ref": ref}), status_code=500)
            finally:
                if form is not None:
                    await form.close()

        # read by the API description (/api/openapi.json, /api/docs)
        endpoint.kosha = {"method": method, "path": path, "role": role if auth else None, "auth": auth, "handler": fn}
        routes.append(Route(path, endpoint, methods=[method]))
        return fn

    return deco


def error_body(message: str, code: str, request: Request | None = None, extra: dict | None = None) -> dict:
    """Kosha's one error format: plain message, machine code, the request ID (also in the server log), details."""
    from .apisafety import current_request_id
    return {"error": message, "code": code, **(extra or {}), "request_id": current_request_id(request)}


def _run_sync(fn, req):
    from .access import current
    tok = current.set(req.user)      # what this person may see (access.scope) follows them into the handler
    try:
        return fn(req)
    finally:
        current.reset(tok)


# what a vendor account may call: read its projects' parts and files, and its own account
VENDOR_PATHS = {
    ("GET", "/api/auth/me"), ("POST", "/api/auth/mode"), ("POST", "/api/auth/theme"), ("POST", "/api/auth/password"),
    ("POST", "/api/auth/logout"), ("GET", "/api/settings"), ("GET", "/api/search"), ("GET", "/api/documents"),
    ("GET", "/api/documents/{id:int}"), ("GET", "/api/documents/{id:int}/file"),
    ("GET", "/api/documents/{id:int}/preview"), ("GET", "/api/documents/{id:int}/viewer"), ("GET", "/api/items"),
    ("GET", "/api/items/{id:int}"), ("GET", "/api/projects"), ("GET", "/api/projects/{id:int}"),
    ("GET", "/api/notifications"), ("GET", "/api/notifications/count"), ("POST", "/api/notifications/{id:int}/read"),
    ("POST", "/api/notifications/read-all"), ("GET", "/api/auth/password-rules"),
}


def _vendor_may(method: str, path: str) -> bool:
    return (method, path) in VENDOR_PATHS


def as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")
