# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quality: nonconformance reports (NCR-YY-NNNN), corrective and preventive actions (CAPA-YY-NNNN) and FMEA.

Who may do what is decided in kosha.quality (the quality role counts as well as the engineer and approver roles), so
these routes only need someone signed in."""
from __future__ import annotations

from starlette.responses import Response

from .. import quality, store
from ..web import Req, api, bad, forbidden, not_found
from .files import SAFE_HEADERS, _disposition

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _body(req: Req) -> dict:
    return req.body if isinstance(req.body, dict) else {}


def _ncr(req: Req, ncr_id: int | None = None) -> dict:
    return {"ncr": quality.ncr_detail(ncr_id or req.int_param("id"), req.user)}


def _capa(req: Req, capa_id: int | None = None) -> dict:
    return {"capa": quality.capa_detail(capa_id or req.int_param("id"), req.user)}


def _fmea(req: Req, fmea_id: int | None = None) -> dict:
    return {"fmea": quality.fmea_detail(fmea_id or req.int_param("id"))}


@api("GET", "/api/quality")
def overview(req: Req):
    return {**quality.overview(req.user), "found_at": quality.FOUND_AT, "dispositions": quality.DISPOSITIONS,
            "ncr_states": quality.NCR_STATES, "capa_states": quality.CAPA_STATES, "capa_kinds": quality.CAPA_KINDS,
            "fmea_kinds": quality.FMEA_KINDS, "may_raise": quality._may_raise(req.user),
            "may_decide": quality._may_decide(req.user)}


@api("PUT", "/api/quality/rpn-limit")
def set_rpn_limit(req: Req):
    if not quality._may_decide(req.user):
        raise forbidden("Setting the RPN limit needs the approver or quality role.")
    return {"rpn_limit": quality.set_rpn_limit(req.user, req.b("rpn_limit"))}


# ------------------------------------------------------------------ NCRs --

@api("GET", "/api/ncrs")
def list_ncrs(req: Req):
    f = {k: req.q(k) for k in ("state", "item_id", "q") if req.q(k) not in (None, "")}
    return {"ncrs": quality.ncr_listing(f)}


@api("POST", "/api/ncrs")
def create_ncr(req: Req):
    return _ncr(req, quality.create_ncr(req.user, _body(req)))


@api("GET", "/api/ncrs/{id:int}")
def get_ncr(req: Req):
    return _ncr(req)


@api("PATCH", "/api/ncrs/{id:int}")
def patch_ncr(req: Req):
    quality.update_ncr(req.user, req.int_param("id"), _body(req))
    return _ncr(req)


@api("POST", "/api/ncrs/{id:int}/disposition")
def disposition(req: Req):
    return {"ncr": quality.disposition(req.user, req.int_param("id"), _body(req))}


@api("POST", "/api/ncrs/{id:int}/close")
def close_ncr(req: Req):
    return {"ncr": quality.close_ncr(req.user, req.int_param("id"), _body(req))}


@api("POST", "/api/ncrs/{id:int}/cancel")
def cancel_ncr(req: Req):
    return {"ncr": quality.cancel_ncr(req.user, req.int_param("id"), req.b("reason") or "")}


@api("POST", "/api/ncrs/{id:int}/capa")
def ncr_to_capa(req: Req):
    """Start a corrective action from this report (or link it to an existing one with capa_id)."""
    n = quality.ncr_or_404(req.int_param("id"))
    body = _body(req)
    if body.get("capa_id"):
        quality.link_ncr(req.user, int(body["capa_id"]), n["id"])
        return _capa(req, int(body["capa_id"]))
    data = {"title": n["title"], "problem": n["description"] or n["title"], "item_id": n["item_id"], **body,
            "ncr_ids": [n["id"]]}
    return _capa(req, quality.create_capa(req.user, data))


# ----------------------------------------------------------------- CAPAs --

@api("GET", "/api/capas")
def list_capas(req: Req):
    f = {k: req.q(k) for k in ("state", "q") if req.q(k) not in (None, "")}
    if req.q("mine"):
        f["mine"] = req.uid
    return {"capas": quality.capa_listing(f)}


@api("POST", "/api/capas")
def create_capa(req: Req):
    return _capa(req, quality.create_capa(req.user, _body(req)))


@api("GET", "/api/capas/{id:int}")
def get_capa(req: Req):
    return _capa(req)


@api("PATCH", "/api/capas/{id:int}")
def patch_capa(req: Req):
    quality.update_capa(req.user, req.int_param("id"), _body(req))
    return _capa(req)


@api("POST", "/api/capas/{id:int}/ncrs")
def link_ncr(req: Req):
    try:
        ncr_id = int(req.b("ncr_id"))
    except (TypeError, ValueError):
        raise bad("Choose the nonconformance report to link.") from None
    quality.capa_or_404(req.int_param("id"))
    quality.link_ncr(req.user, req.int_param("id"), ncr_id)
    return _capa(req)


@api("POST", "/api/capas/{id:int}/actions")
def add_action(req: Req):
    quality.add_action(req.user, req.int_param("id"), _body(req))
    return _capa(req)


@api("PATCH", "/api/capas/{id:int}/actions/{aid:int}")
def finish_action(req: Req):
    quality.finish_action(req.user, req.int_param("id"), req.int_param("aid"), req.b("note") or "",
                          done=req.b("done", True) not in (False, "false", "0", 0))
    return _capa(req)


@api("DELETE", "/api/capas/{id:int}/actions/{aid:int}")
def remove_action(req: Req):
    quality.remove_action(req.user, req.int_param("id"), req.int_param("aid"))
    return _capa(req)


@api("POST", "/api/capas/{id:int}/advance")
def advance(req: Req):
    return {"capa": quality.advance(req.user, req.int_param("id"), _body(req))}


@api("POST", "/api/capas/{id:int}/cancel")
def cancel_capa(req: Req):
    return {"capa": quality.cancel_capa(req.user, req.int_param("id"), req.b("reason") or "")}


@api("POST", "/api/capas/{id:int}/change-request", role="engineer")
def raise_change_request(req: Req):
    return {"capa": quality.raise_change_request(req.user, req.int_param("id"), _body(req))}


# ------------------------------------------------------------------ FMEA --

@api("GET", "/api/fmeas")
def list_fmeas(req: Req):
    return {"fmeas": quality.fmea_listing(req.q("item_id")), "rpn_limit": quality.rpn_limit()}


@api("POST", "/api/fmeas")
def create_fmea(req: Req):
    return _fmea(req, quality.create_fmea(req.user, _body(req)))


@api("GET", "/api/fmeas/{id:int}")
def get_fmea(req: Req):
    return _fmea(req)


@api("PUT", "/api/fmeas/{id:int}")
def save_fmea(req: Req):
    body = _body(req)
    quality.save_fmea_rows(req.user, req.int_param("id"), body.get("rows"), body.get("title"), body.get("team"))
    return _fmea(req)


@api("GET", "/api/fmeas/{id:int}/export")
def export_fmea(req: Req):
    try:
        data, name = quality.export_fmea(req.int_param("id"))
    except ImportError:
        raise bad("The Excel export needs the openpyxl package on the server.") from None
    return Response(data, media_type=XLSX, headers={"Content-Disposition": _disposition("attachment", name),
                                                    **SAFE_HEADERS})


# ----------------------------------------------------------- attachments --

def _attachment_routes(kind: str) -> None:
    base = f"/api/{kind}s/{{id:int}}/attachments"
    detail = _ncr if kind == "ncr" else _capa

    def attach(req: Req):
        files = req.files.get("file") or req.files.get("files") or []
        if not files:
            raise bad("Choose a photo or file to add (photo of the fault, inspection report, 8D report ...).")
        for f in files:
            quality.add_attachment(req.user, kind, req.int_param("id"), f.filename, f.file)
        return detail(req)

    def download(req: Req):
        a = quality.attachment_or_404(kind, req.int_param("id"), req.int_param("aid"))
        if not store.exists(a["blob_sha"]):
            raise not_found("The stored file is missing on the server.")
        return Response(store.read_bytes(a["blob_sha"]), media_type="application/octet-stream",
                        headers={"Content-Disposition": _disposition("attachment", a["filename"]), **SAFE_HEADERS})

    def remove(req: Req):
        quality.remove_attachment(req.user, kind, req.int_param("id"), req.int_param("aid"))
        return detail(req)

    for fn in (attach, download, remove):
        fn.__name__ = f"{kind}_{fn.__name__}"
    api("POST", base)(attach)
    api("GET", base + "/{aid:int}")(download)
    api("DELETE", base + "/{aid:int}")(remove)


_attachment_routes("ncr")
_attachment_routes("capa")
