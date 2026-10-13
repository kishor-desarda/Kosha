# SPDX-License-Identifier: AGPL-3.0-or-later
"""Standard-parts library: browse by category, filter by size, search by designation, use a part."""
from __future__ import annotations

from ..library import StandardPartsLibrary
from ..numbering import NumberingError
from ..web import Req, api, bad, not_found


def _lib() -> StandardPartsLibrary:
    return StandardPartsLibrary()


@api("GET", "/api/library")
def overview(req: Req):
    cats = _lib().overview()
    return {"categories": cats, "installed": bool(cats), "can_use": req.has("engineer")}


@api("GET", "/api/library/tables/{table_id}")
def table(req: Req):
    try:
        return _lib().browse(req.params["table_id"], (req.q("size") or "").strip(), (req.q("q") or "").strip())
    except LookupError as e:
        raise not_found(str(e))


@api("GET", "/api/library/search")
def search(req: Req):
    q = (req.q("q") or "").strip()
    limit = max(1, min(int(req.q("limit", 50)), 200))
    return _lib().search(q, limit)


def _opts(req: Req) -> dict:
    o = req.b("options") or {}
    if not isinstance(o, dict):
        raise bad("options must be a mapping of option name to value.")
    return o


@api("POST", "/api/library/preview")
def preview(req: Req):
    try:
        return _lib().preview(req.b("table") or "", req.b("row") or "", _opts(req))
    except LookupError as e:
        raise not_found(str(e))


@api("POST", "/api/library/use", role="engineer")
def use(req: Req):
    try:
        return _lib().use(req.b("table") or "", req.b("row") or "", _opts(req), req.uid)
    except LookupError as e:
        raise not_found(str(e))
    except NumberingError as e:
        raise bad(str(e), e.code, errors=e.errors)
