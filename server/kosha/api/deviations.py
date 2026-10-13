# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deviations: temporary, approved departures from the released design (DEV-YY-NNNN)."""
from __future__ import annotations

from starlette.responses import Response

from .. import deviations, store
from ..web import Req, api, bad, not_found
from .files import SAFE_HEADERS, _disposition

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FILTER_KEYS = ("state", "status", "item", "item_id", "q", "date_from", "date_to")


def _filters(req: Req) -> dict:
    return {k: req.q(k) for k in FILTER_KEYS if req.q(k) not in (None, "")}


def _detail(req: Req, dev_id: int | None = None, **extra) -> dict:
    return {**deviations.detail(dev_id or req.int_param("id"), req.user), **extra}


@api("GET", "/api/deviations")
def list_deviations(req: Req):
    deviations.sweep()
    return {"deviations": deviations.listing(_filters(req)), "states": deviations.STATE_LABEL}


@api("POST", "/api/deviations", role="engineer")
def create(req: Req):
    body = req.body if isinstance(req.body, dict) else {}
    return _detail(req, deviations.create(req.user, body))


@api("GET", "/api/deviations/summary")
def summary(req: Req):
    deviations.sweep()
    return deviations.summary_counts(req.user)


@api("GET", "/api/deviations/home")
def home(req: Req):
    deviations.sweep()
    return deviations.home(req.user)


@api("GET", "/api/deviations/active")
def active(req: Req):
    deviations.sweep()
    try:
        eid = int(req.q("entity_id"))
    except (TypeError, ValueError):
        raise bad("entity_id is required")
    return deviations.active_for(req.q("entity_type") or "", eid)


@api("GET", "/api/deviations/export")
def export(req: Req):
    deviations.sweep()
    try:
        data, name = deviations.report_xlsx(_filters(req))
    except ImportError:
        raise bad("The deviations register needs the openpyxl package on the server.")
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@api("GET", "/api/deviations/{id:int}")
def get_deviation(req: Req):
    deviations.sweep()
    return _detail(req)


@api("PATCH", "/api/deviations/{id:int}", role="engineer")
def patch_deviation(req: Req):
    deviations.update(req.user, req.int_param("id"), req.body if isinstance(req.body, dict) else {})
    return _detail(req)


@api("POST", "/api/deviations/{id:int}/affected", role="engineer")
def add_affected(req: Req):
    deviations.add_affected(req.user, req.int_param("id"), req.b("item_id"), req.b("document_id"), req.b("revision"),
                            req.b("note") or "")
    return _detail(req)


@api("DELETE", "/api/deviations/{id:int}/affected/{aid:int}", role="engineer")
def remove_affected(req: Req):
    deviations.remove_affected(req.user, req.int_param("id"), req.int_param("aid"))
    return _detail(req)


@api("POST", "/api/deviations/{id:int}/approvers", role="engineer")
def add_approver(req: Req):
    try:
        uid = int(req.b("user_id"))
    except (TypeError, ValueError):
        raise bad("Choose the approver")
    deviations.add_approver(req.user, req.int_param("id"), uid)
    return _detail(req)


@api("DELETE", "/api/deviations/{id:int}/approvers/{uid:int}", role="engineer")
def remove_approver(req: Req):
    deviations.remove_approver(req.user, req.int_param("id"), req.int_param("uid"))
    return _detail(req)


@api("POST", "/api/deviations/{id:int}/submit", role="engineer")
def submit(req: Req):
    deviations.submit(req.user, req.int_param("id"))
    return _detail(req)


@api("POST", "/api/deviations/{id:int}/decide", role="approver")
def decide(req: Req):
    deviations.decide(req.user, req.int_param("id"), req.b("decision") or "", req.b("comment") or "")
    return _detail(req)


@api("POST", "/api/deviations/{id:int}/usage", role="engineer")
def usage(req: Req):
    deviations.record_usage(req.user, req.int_param("id"), req.b("qty"), req.b("reference") or "", req.b("note") or "")
    return _detail(req)


@api("POST", "/api/deviations/{id:int}/close", role="engineer")
def close(req: Req):
    deviations.close(req.user, req.int_param("id"), req.b("qty_used"), req.b("note") or "")
    return _detail(req)


@api("POST", "/api/deviations/{id:int}/cancel", role="engineer")
def cancel(req: Req):
    deviations.cancel(req.user, req.int_param("id"), req.b("note") or "")
    return _detail(req)


@api("POST", "/api/deviations/{id:int}/make-permanent", role="engineer")
def make_permanent(req: Req):
    res = deviations.make_permanent(req.user, req.int_param("id"))
    return _detail(req, **res)


@api("POST", "/api/deviations/{id:int}/changes", role="engineer")
def link_change(req: Req):
    try:
        co_id = int(req.b("change_id"))
    except (TypeError, ValueError):
        raise bad("Choose the change order")
    deviations.link_change(req.user, req.int_param("id"), co_id)
    return _detail(req)


@api("DELETE", "/api/deviations/{id:int}/changes/{co:int}", role="engineer")
def unlink_change(req: Req):
    deviations.unlink_change(req.user, req.int_param("id"), req.int_param("co"))
    return _detail(req)


@api("POST", "/api/deviations/{id:int}/attachments", role="engineer")
def attach(req: Req):
    files = req.files.get("file") or req.files.get("files") or []
    if not files:
        raise bad("Choose a file to attach (photo, inspection report, supplier certificate ...).")
    for f in files:
        deviations.add_attachment(req.user, req.int_param("id"), f.filename, f.file)
    return _detail(req)


@api("GET", "/api/deviations/{id:int}/attachments/{aid:int}")
def download_attachment(req: Req):
    a = deviations.attachment_or_404(req.int_param("id"), req.int_param("aid"))
    if not store.exists(a["blob_sha"]):
        raise not_found("The stored file is missing on the server.")
    return Response(store.read_bytes(a["blob_sha"]), media_type="application/octet-stream",
                    headers={"Content-Disposition": _disposition("attachment", a["filename"]), **SAFE_HEADERS})


@api("DELETE", "/api/deviations/{id:int}/attachments/{aid:int}", role="engineer")
def remove_attachment(req: Req):
    deviations.remove_attachment(req.user, req.int_param("id"), req.int_param("aid"))
    return _detail(req)
