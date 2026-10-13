# SPDX-License-Identifier: AGPL-3.0-or-later
"""ASGI application: API routes + the web client."""
from __future__ import annotations

import contextlib
import logging
import os
import secrets

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import addins, config, db, erp, notify_outbox, security, sessions, web
from .headers import SecurityHeadersMiddleware
from .api import load_all as _load_api

_load_api()   # every module in kosha/api registers its routes

log = logging.getLogger("kosha")


class StartupError(RuntimeError):
    """Kosha can't start safely; the message says what to change."""


def bootstrap() -> None:
    config.ensure_dirs()
    db.init()
    if not db.scalar("SELECT COUNT(*) FROM users"):
        pw = os.environ.get("KOSHA_ADMIN_PASSWORD") or ""
        if pw:
            problem = security.password_problem(pw, "admin")
            if problem:
                raise StartupError(f"KOSHA_ADMIN_PASSWORD is not accepted: {problem} Set a stronger first admin "
                                   "password in kosha.env (or leave it empty to have one made up) and start Kosha "
                                   "again. Nothing was created yet.")
        else:
            pw = secrets.token_urlsafe(15)
        db.insert("users", {"username": "admin", "full_name": "Kosha Administrator", "email": "", "role": "admin",
                            "password_hash": security.hash_password(pw), "ui_mode": "expert", "created_at": db.now()})
        if os.environ.get("KOSHA_ADMIN_PASSWORD"):
            log.warning("Created first user 'admin' with the password from KOSHA_ADMIN_PASSWORD. "
                        "Change it after signing in.")
        else:
            log.warning("Created first user 'admin' with password %r. Change it after signing in.", pw)
    try:
        sessions.purge()
    except Exception:   # housekeeping only
        log.exception("Could not tidy up old sessions")
    db.execute("UPDATE import_jobs SET status = 'interrupted', phase = 'Server restarted', finished_at = ? "
               "WHERE status = 'running'", (db.now(),))
    db.execute("UPDATE cp_runs SET status = 'failed', message = 'Server restarted' WHERE status = 'running'")
    if not db.scalar("SELECT COUNT(*) FROM folders"):
        db.insert("folders", {"parent_id": None, "name": "Kosha", "created_at": db.now()})
    addins.registry.discover()


@contextlib.asynccontextmanager
async def lifespan(app):
    try:
        bootstrap()
    except StartupError as e:
        log.error("%s", e)
        raise
    if os.environ.get("KOSHA_DISABLE_WORKER") != "1":
        erp.start_worker()
        notify_outbox.start_worker()
    from .inputs import start_worker as start_text_reader   # inputs library: reads file text in the background
    start_text_reader()
    yield


async def index(request):
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def health(request):
    """Server is up. Public; used by monitoring and by the start-up scripts."""
    return JSONResponse({"ok": True})


def create_app() -> Starlette:
    routes = list(web.routes) + [
        Route("/api/health", health),
        Route("/", index),
        Mount("/static", app=StaticFiles(directory=str(config.STATIC_DIR)), name="static"),
    ]

    async def not_found(request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Not found", "code": "not_found"}, status_code=404)
        return FileResponse(config.STATIC_DIR / "index.html")

    middleware = [Middleware(SecurityHeadersMiddleware), Middleware(GZipMiddleware, minimum_size=1024)]
    return Starlette(routes=routes, lifespan=lifespan, middleware=middleware, exception_handlers={404: not_found})


app = create_app()
