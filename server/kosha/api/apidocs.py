# SPDX-License-Identifier: AGPL-3.0-or-later
"""The API description: /api/openapi.json (OpenAPI 3.1) and /api/docs (a readable page). Public and read-only."""
from __future__ import annotations

from starlette.responses import HTMLResponse, JSONResponse

from .. import apidoc
from ..web import Req, api

DOCS_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_cache: dict = {}


def _spec(req: Req) -> dict:
    routes = list(req.request.app.routes)
    key = len(routes)
    if _cache.get("key") != key:   # routes are fixed once the server runs; build the description once
        _cache.update(key=key, spec=apidoc.openapi(routes))
    return _cache["spec"]


@api("GET", "/api/openapi.json", role=None, auth=False)
def openapi_json(req: Req):
    """This API described in OpenAPI 3.1 (JSON), generated from the routes the server has registered."""
    return JSONResponse(_spec(req), headers={"Cache-Control": "no-cache"})


@api("GET", "/api/docs", role=None, auth=False)
def docs(req: Req):
    """This API as a readable page, grouped by area (HTML)."""
    return HTMLResponse(apidoc.html(_spec(req)), headers={"Content-Security-Policy": DOCS_CSP,
                                                          "Cache-Control": "no-cache"})
