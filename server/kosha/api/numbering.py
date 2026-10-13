# SPDX-License-Identifier: AGPL-3.0-or-later
"""Number schemes and spec codes: list, preview and allocate. (/api/numbering/next stays in core.py.)"""
from __future__ import annotations

from ..numbering import NumberingError, NumberingService
from ..packs import PackConfig
from ..web import Req, api, bad


def _values(req: Req) -> dict:
    v = req.b("values") or {}
    if not isinstance(v, dict):
        raise bad("values must be a mapping of field name to value.")
    return v


@api("GET", "/api/numbering/schemes")
def schemes(req: Req):
    cfg = PackConfig.current()
    svc = NumberingService(cfg)
    return {"schemes": svc.describe(),
            "item_types": [{"code": r.value["code"], "name": r.value["name"], "base": r.value.get("base", "part")}
                           for r in cfg.records("item_type")],
            "can_allocate": req.has("engineer")}


@api("POST", "/api/numbering/preview")
def preview(req: Req):
    try:
        return NumberingService().preview(req.b("scheme") or "", _values(req), (req.b("description") or "").strip())
    except NumberingError as e:
        raise bad(str(e), e.code, errors=e.errors)


@api("POST", "/api/numbering/allocate", role="engineer")
def allocate(req: Req):
    try:
        return NumberingService().allocate(req.b("scheme") or "", _values(req), req.uid,
                                           (req.b("description") or "").strip(), (req.b("item_type") or "").strip())
    except NumberingError as e:
        raise bad(str(e), e.code, errors=e.errors)


@api("GET", "/api/numbering/allocations", role="engineer")
def allocations(req: Req):
    limit = min(int(req.q("limit", 50)), 500)
    return {"allocations": NumberingService.recent(limit)}
