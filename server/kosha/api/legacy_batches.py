# SPDX-License-Identifier: AGPL-3.0-or-later
"""Legacy migration batches (specification 11): work queues and the review grid, Excel round-trip, the count check,
sample audit and sign-off, Undo batch, and the old paths and numbers of a file."""
from __future__ import annotations

import json

from starlette.responses import Response

from .. import legacy
from ..web import Req, api, as_bool, bad

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@api("GET", "/api/import/jobs/{id:int}", role="engineer")
def batch(req: Req):
    j = dict(legacy.batch_or_404(req.int_param("id")))
    j["options"] = json.loads(j.pop("options_json") or "{}")
    j.pop("verify_json", None)
    return {"job": j}


@api("GET", "/api/import/jobs/{id:int}/records", role="engineer")
def records(req: Req):
    return legacy.records(req.int_param("id"), queue=req.q("queue") or "", q=(req.q("q") or "").strip(),
                          unconfirmed=as_bool(req.q("unconfirmed") or False), status=req.q("status") or "",
                          offset=int(req.q("offset") or 0), limit=int(req.q("limit") or 50))


@api("PATCH", "/api/import/records/{id:int}", role="engineer")
def correct(req: Req):
    body = req.body if isinstance(req.body, dict) else {}
    changes = {k: v for k, v in body.items() if k != "confirm"}
    return {"record": legacy.correct(req.user, req.int_param("id"), changes, as_bool(body.get("confirm", False)))}


@api("POST", "/api/import/jobs/{id:int}/records/confirm", role="engineer")
def confirm(req: Req):
    """Confirm many rows at once, and optionally set one field (description or material) across all of them."""
    ids = req.b("ids") or []
    if not isinstance(ids, list) or not ids:
        raise bad("Choose the rows to confirm.")
    field = req.b("field")
    changes = {field: req.b("value")} if field else {}
    return {"confirmed": legacy.bulk(req.user, req.int_param("id"), ids, changes)}


@api("GET", "/api/import/jobs/{id:int}/records.xlsx", role="engineer")
def export_xlsx(req: Req):
    bid = req.int_param("id")
    queue = req.q("queue") or ""
    name = f"kosha-batch-{bid}-{queue or 'all'}.xlsx"
    return Response(legacy.export_xlsx(bid, queue), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@api("POST", "/api/import/jobs/{id:int}/records.xlsx", role="engineer")
def import_xlsx(req: Req):
    files = req.files.get("file") or []
    if not files:
        raise bad("Choose the corrected Excel file.")
    return legacy.import_xlsx(req.user, req.int_param("id"), files[0].file.read())


@api("GET", "/api/import/jobs/{id:int}/reconcile", role="engineer")
def reconcile(req: Req):
    return legacy.reconcile(req.int_param("id"))


@api("POST", "/api/import/jobs/{id:int}/verify", role="admin")
def verify(req: Req):
    return legacy.verify_files(req.int_param("id"))


@api("POST", "/api/import/jobs/{id:int}/sample", role="admin")
def sample(req: Req):
    size = req.b("size")
    return {"sample": legacy.draw_sample(req.user, req.int_param("id"), int(size) if size else None)}


@api("PUT", "/api/import/records/{id:int}/sample", role="engineer")
def mark_sample(req: Req):
    return {"record": legacy.mark_sample(req.user, req.int_param("id"), req.b("result"))}


@api("POST", "/api/import/jobs/{id:int}/sign-off", role="admin")
def sign_off(req: Req):
    return legacy.sign_off(req.user, req.int_param("id"))


@api("GET", "/api/import/jobs/{id:int}/undo", role="admin")
def undo_plan(req: Req):
    return legacy.undo_plan(req.int_param("id"))


@api("POST", "/api/import/jobs/{id:int}/undo", role="admin")
def undo(req: Req):
    return legacy.undo(req.user, req.int_param("id"))


@api("GET", "/api/documents/{id:int}/legacy-refs")
def refs(req: Req):
    from .. import lifecycle
    lifecycle.doc_or_404(req.int_param("id"))
    return {"refs": legacy.refs(req.int_param("id"))}
