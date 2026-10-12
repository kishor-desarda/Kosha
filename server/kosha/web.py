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

from . import db, security

log = logging.getLogger("kosha")

ROLE_LEVEL = {"viewer": 0, "engineer": 1, "approver": 2, "admin": 3}

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
        if not self.user or ROLE_LEVEL[self.user["role"]] < ROLE_LEVEL[role]:
            raise forbidden(f"This needs the '{role}' role or higher.")

    def has(self, role: str) -> bool:
        return bool(self.user) and ROLE_LEVEL[self.user["role"]] >= ROLE_LEVEL[role]

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


def _authenticate(request: Request) -> dict | None:
    token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.query_params.get("access_token")
    if not token:
        return None
    user_id = security.verify_token(token)
    if user_id is None:
        return None
    user = db.one("SELECT id, username, full_name, email, role, ui_mode, active FROM users WHERE id = ?", (user_id,))
    if not user or not user["active"]:
        return None
    return user


def api(method: str, path: str, role: str | None = "viewer", auth: bool = True):
    """Register a handler. role=None with auth=False makes it public."""

    def deco(fn: Callable[[Req], Any]):
        async def endpoint(request: Request) -> Response:
            form = None
            try:
                user = _authenticate(request) if auth else None
                if auth and user is None:
                    raise ApiError(401, "Please sign in.", "unauthorized")
                body = None
                files: dict = {}
                ctype = request.headers.get("content-type", "")
                if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                    if ctype.startswith("multipart/form-data"):
                        form = await request.form(max_files=500, max_fields=2000, max_part_size=64 * 1024 * 1024)
                        for key, value in form.multi_items():
                            if hasattr(value, "filename") and hasattr(value, "file"):
                                files.setdefault(key, []).append(value)
                    elif ctype.startswith("application/json"):
                        raw = await request.body()
                        body = json.loads(raw) if raw else {}
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
                if inspect.iscoroutinefunction(fn):
                    result = await fn(req)
                else:
                    result = await run_in_threadpool(_run_sync, fn, req)
                if isinstance(result, Response):
                    return result
                return JSONResponse(result if result is not None else {"ok": True})
            except ApiError as e:
                return JSONResponse({"error": e.message, "code": e.code, **e.extra}, status_code=e.status)
            except Exception:  # pragma: no cover - logged for admins with a reference the user can quote
                ref = secrets.token_hex(3).upper()
                log.error("Unhandled error ref %s on %s %s\n%s", ref, method, path, traceback.format_exc())
                return JSONResponse({"error": "Something went wrong on our side. Your work is safe and nothing was "
                                              f"changed. If it happens again, tell your admin: ref {ref}.",
                                     "code": "server_error", "ref": ref}, status_code=500)
            finally:
                if form is not None:
                    await form.close()

        routes.append(Route(path, endpoint, methods=[method]))
        return fn

    return deco


def _run_sync(fn, req):
    return fn(req)


def as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")
