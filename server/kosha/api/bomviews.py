# SPDX-License-Identifier: AGPL-3.0-or-later
"""BOM views (latest released, in work, as of a date, as built), cost roll-up and what-if, and machine builds (the
installed base)."""
from __future__ import annotations

from starlette.responses import Response

from .. import bomviews, machines
from ..web import Req, api, bad


def _view(spec: dict) -> dict:
    try:
        item_id = int(spec.get("item_id"))
    except (TypeError, ValueError):
        raise bad("Choose the part whose BOM to show.") from None
    return bomviews.view(item_id, spec.get("view") or "released", spec.get("date"), spec.get("serial"))


@api("GET", "/api/items/{id:int}/bom/view")
def bom_view(req: Req):
    """One view of a part's BOM: ``view`` = released, in_work, as_of (with ``date``) or as_built (with ``serial``);
    ``cost=1`` adds the cost roll-up."""
    v = _view({"item_id": req.int_param("id"), "view": req.q("view"), "date": req.q("date"), "serial": req.q("serial")})
    return bomviews.cost(v) if req.q("cost") in ("1", "true") else v


@api("GET", "/api/items/{id:int}/bom/cost/export")
def bom_cost_export(req: Req):
    v = bomviews.cost(_view({"item_id": req.int_param("id"), "view": req.q("view"), "date": req.q("date"),
                             "serial": req.q("serial")}))
    data, name, ctype = bomviews.export_cost(v)
    return Response(data, media_type=ctype, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@api("POST", "/api/boms/what-if")
def what_if(req: Req):
    """Compare the cost of two views (``a`` and ``b``, each {item_id, view, date, serial})."""
    a, b = req.b("a"), req.b("b")
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise bad("Choose the two BOMs to compare.")
    return bomviews.what_if(bomviews.cost(_view(a)), bomviews.cost(_view(b)))


@api("GET", "/api/cost/rates")
def get_rates(req: Req):
    return bomviews.rates()


@api("PUT", "/api/cost/rates", role="admin")
def put_rates(req: Req):
    return bomviews.save_rates(req.uid, req.body if isinstance(req.body, dict) else {})


# ------------------------------------------------------------ machine builds --

@api("GET", "/api/machines")
def list_machines(req: Req):
    item = req.q("item_id")
    return {"machines": machines.listing(req.q("q") or "", int(item) if item and item.isdigit() else None,
                                         req.q("withdrawn") in ("1", "true"))}


@api("POST", "/api/machines", role="engineer")
def add_machine(req: Req):
    return {"machine": machines.create(req.user, req.body if isinstance(req.body, dict) else {})}


@api("GET", "/api/machines/{id:int}")
def get_machine(req: Req):
    b = machines.build_or_404(req.int_param("id"))
    return {"machine": machines.summary(b), "rows": machines.as_built_rows(b)}


@api("PATCH", "/api/machines/{id:int}", role="engineer")
def edit_machine(req: Req):
    return {"machine": machines.update(req.user, req.int_param("id"), req.body if isinstance(req.body, dict) else {})}


@api("POST", "/api/machines/{id:int}/withdraw", role="approver")
def withdraw_machine(req: Req):
    return {"machine": machines.withdraw(req.user, req.int_param("id"), req.b("reason") or "")}
