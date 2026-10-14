# SPDX-License-Identifier: AGPL-3.0-or-later
"""Manufacturing BOMs and process plans: made from the engineering BOM, checked against it, released and sent to the
ERP when the ERP takes manufacturing BOMs."""
from __future__ import annotations

from starlette.responses import Response

from .. import mbom
from ..web import Req, api, bad

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _body(req: Req) -> dict:
    return req.body if isinstance(req.body, dict) else {}


@api("GET", "/api/mboms")
def list_mboms(req: Req):
    item = req.q("item_id")
    return {"mboms": mbom.listing(int(item) if item and str(item).isdigit() else None, req.q("q") or "")}


@api("POST", "/api/mboms", role="engineer")
def create_mbom(req: Req):
    """Make the manufacturing BOM of an assembly from its engineering BOM (``revision``, or the newest)."""
    try:
        item_id = int(req.b("item_id"))
    except (TypeError, ValueError):
        raise bad("Choose the assembly.") from None
    return mbom.create(req.user, item_id, req.b("revision") or None)


@api("GET", "/api/mboms/{id:int}")
def get_mbom(req: Req):
    return mbom.detail(req.int_param("id"))


@api("PUT", "/api/mboms/{id:int}/plan", role="engineer")
def save_plan(req: Req):
    """Replace the parts (``lines``) and/or the operations (``ops``), checked together."""
    mbom.save_plan(req.user, req.int_param("id"), _body(req))
    return mbom.detail(req.int_param("id"))


@api("POST", "/api/mboms/{id:int}/flatten", role="engineer")
def flatten(req: Req):
    """Build a sub-assembly with its parent (``on``: true) or as its own line again (false)."""
    try:
        item_id = int(req.b("item_id"))
    except (TypeError, ValueError):
        raise bad("Choose the sub-assembly.") from None
    mbom.flatten(req.user, req.int_param("id"), item_id, req.b("on", True) not in (False, "0", "false"))
    return mbom.detail(req.int_param("id"))


@api("POST", "/api/mboms/{id:int}/rebuild", role="engineer")
def rebuild(req: Req):
    mbom.rebuild(req.user, req.int_param("id"))
    return mbom.detail(req.int_param("id"))


@api("POST", "/api/mboms/{id:int}/release", role="approver")
def release(req: Req):
    mbom.release(req.user, req.int_param("id"))
    return mbom.detail(req.int_param("id"))


@api("GET", "/api/mboms/{id:int}/export")
def export(req: Req):
    try:
        data, name = mbom.export_xlsx(req.int_param("id"))
    except ImportError:
        raise bad("The Excel export needs the openpyxl package on the server.") from None
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})
