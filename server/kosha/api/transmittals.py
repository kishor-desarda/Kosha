# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transmittals and vendor packages, Pack and Go, and projects made from ERP sales orders."""
from __future__ import annotations

from starlette.responses import Response

from .. import projects, transmittals
from ..web import Req, api
from .files import SAFE_HEADERS, _disposition


@api("GET", "/api/transmittals")
def list_transmittals(req: Req):
    doc, proj = req.q("document_id"), req.q("project_id")
    return {"transmittals": transmittals.listing(req.user, int(doc) if doc and doc.isdigit() else None,
                                                 int(proj) if proj and proj.isdigit() else None, req.q("q") or "")}


@api("POST", "/api/transmittals", role="engineer")
def create_transmittal(req: Req):
    """Send released files to a vendor or customer: a numbered record, a transmittal sheet and a share link."""
    return {"transmittal": transmittals.create(req.user, req.body if isinstance(req.body, dict) else {})}


@api("GET", "/api/transmittals/{id:int}")
def get_transmittal(req: Req):
    return {"transmittal": transmittals.get(req.user, req.int_param("id"))}


@api("GET", "/api/transmittals/{id:int}/sheet")
def transmittal_sheet(req: Req):
    data, name = transmittals.sheet(req.user, req.int_param("id"))
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": _disposition("attachment", name), **SAFE_HEADERS})


@api("POST", "/api/transmittals/{id:int}/close", role="engineer")
def close_transmittal(req: Req):
    return {"transmittal": transmittals.close(req.user, req.int_param("id"))}


@api("GET", "/api/boms/{id:int}/pack-and-go")
def pack_and_go(req: Req):
    """Every file of every part in the BOM's tree as one ZIP, with a transmittal list (``released=1``: released only)."""
    buf, name = transmittals.pack_and_go(req.user, req.int_param("id"), req.q("released") in ("1", "true"))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": _disposition("attachment", name), **SAFE_HEADERS})


@api("POST", "/api/projects/from-sales-order", role="engineer")
def project_from_sales_order(req: Req):
    """The project of an ERP sales order with its standard folders (made once; the same order returns it again)."""
    p, created = projects.from_sales_order(req.uid, req.b("sales_order") or "", req.b("customer") or "",
                                           req.b("due_date") or None, req.b("name") or "")
    return {"project": projects.summary(p), "created": created}
