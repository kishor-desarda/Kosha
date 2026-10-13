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

from . import addins, config, db, erp, notify_outbox, security, web
from .api import load_all as _load_api

_load_api()   # every module in kosha/api registers its routes

log = logging.getLogger("kosha")


def bootstrap() -> None:
    config.ensure_dirs()
    db.init()
    if not db.scalar("SELECT COUNT(*) FROM users"):
        pw = os.environ.get("KOSHA_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
        db.insert("users", {"username": "admin", "full_name": "Kosha Administrator", "email": "", "role": "admin",
                            "password_hash": security.hash_password(pw), "ui_mode": "expert", "created_at": db.now()})
        log.warning("Created first user 'admin' with password %r. Change it after signing in.", pw)
    db.execute("UPDATE import_jobs SET status = 'interrupted', phase = 'Server restarted', finished_at = ? "
               "WHERE status = 'running'", (db.now(),))
    db.execute("UPDATE cp_runs SET status = 'failed', message = 'Server restarted' WHERE status = 'running'")
    if not db.scalar("SELECT COUNT(*) FROM folders"):
        db.insert("folders", {"parent_id": None, "name": "Kosha", "created_at": db.now()})
    addins.registry.discover()


@contextlib.asynccontextmanager
async def lifespan(app):
    bootstrap()
    if os.environ.get("KOSHA_DISABLE_WORKER") != "1":
        erp.start_worker()
        notify_outbox.start_worker()
    yield


async def index(request):
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def health(request):
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

    return Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(GZipMiddleware, minimum_size=1024)],
                     exception_handlers={404: not_found})


app = create_app()
