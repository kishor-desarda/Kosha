# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configurable products: options, rules on BOM lines, constraints, configuring, checking every combination, and
saved variants."""
from __future__ import annotations

from starlette.responses import Response

from .. import configurator
from ..web import Req, api, bad

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _source(req: Req) -> str:
    s = req.q("source") or (req.b("source") if isinstance(req.body, dict) else None) or "released"
    if s not in ("released", "in_work"):
        raise bad("source is released or in_work.")
    return s


@api("GET", "/api/products")
def list_products(req: Req):
    """Configurable products: assemblies that have options."""
    return {"products": configurator.products()}


@api("GET", "/api/items/{id:int}/config")
def get_config(req: Req):
    """A product's options, constraints, BOM with each line's rule (``source`` released or in_work) and variants."""
    return configurator.detail(req.int_param("id"), _source(req))


@api("PUT", "/api/items/{id:int}/config/options", role="engineer")
def put_options(req: Req):
    return configurator.save_options(req.user, req.int_param("id"), req.b("options"))


@api("PUT", "/api/items/{id:int}/config/rules", role="engineer")
def put_rules(req: Req):
    """Set the rules of the lines sent: [{parent_item_id, child_item_id, rule}]; an empty rule means always used."""
    return configurator.save_rules(req.user, req.int_param("id"), req.b("rules"))


@api("PUT", "/api/items/{id:int}/config/constraints", role="engineer")
def put_constraints(req: Req):
    """Replace the constraints: [{if, then, message}]."""
    return configurator.save_constraints(req.user, req.int_param("id"), req.b("constraints"))


@api("POST", "/api/items/{id:int}/configure")
def configure(req: Req):
    """The BOM one combination builds: ``choice`` {OPTION: CHOICE}."""
    return configurator.configure(req.int_param("id"), req.b("choice"), _source(req))


@api("POST", "/api/items/{id:int}/config/check")
def check(req: Req):
    """Every allowed combination: lines never used, choices that change nothing, empty assemblies."""
    return configurator.check_all(req.int_param("id"), _source(req))


@api("GET", "/api/variants")
def list_variants(req: Req):
    item = req.q("item_id")
    return {"variants": configurator.variants(int(item) if item and str(item).isdigit() else None, req.q("q") or "",
                                              req.q("withdrawn") in ("1", "true"))}


@api("POST", "/api/items/{id:int}/variants", role="engineer")
def save_variant(req: Req):
    """Save a configuration as a variant for an order: ``choice``, ``name``, ``customer``, ``order_ref``, ``note``."""
    return {"variant": configurator.save_variant(req.user, req.int_param("id"),
                                                 req.body if isinstance(req.body, dict) else {})}


@api("GET", "/api/variants/{id:int}")
def get_variant(req: Req):
    return {"variant": configurator.variant_or_404(req.int_param("id"))}


@api("POST", "/api/variants/{id:int}/withdraw", role="approver")
def withdraw_variant(req: Req):
    return {"variant": configurator.withdraw_variant(req.user, req.int_param("id"), req.b("reason") or "")}


@api("GET", "/api/variants/{id:int}/export")
def export_variant(req: Req):
    try:
        data, name = configurator.export_variant(req.int_param("id"))
    except ImportError:
        raise bad("The Excel export needs the openpyxl package on the server.") from None
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})
