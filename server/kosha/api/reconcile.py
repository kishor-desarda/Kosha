# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP reconciliation: start a check, follow it, send fixable rows again, export the differences to Excel."""
from __future__ import annotations

from starlette.responses import Response

from .. import db, erp, erp_reconcile
from ..web import Req, api, bad, conflict


@api("POST", "/api/erp/reconcile", role="approver")
def start_check(req: Req):
    try:
        run_id = erp_reconcile.start(req.uid)
    except (erp_reconcile.ReconcileError, erp.ErpError) as e:
        raise conflict(str(e), "cannot_start")
    return erp_reconcile.run_detail(run_id)


@api("GET", "/api/erp/reconcile", role="approver")
def latest_check(req: Req):
    run = req.q("run")
    return erp_reconcile.run_detail(int(run) if run and str(run).isdigit() else None)


@api("POST", "/api/erp/reconcile/{id:int}/cancel", role="approver")
def cancel_check(req: Req):
    erp_reconcile.cancel(req.int_param("id"))
    return erp_reconcile.run_detail(req.int_param("id"))


@api("POST", "/api/erp/reconcile/{id:int}/resend", role="approver")
def resend_all(req: Req):
    n = erp_reconcile.resend_all(req.int_param("id"), req.uid)
    return {"queued": n, **erp_reconcile.run_detail(req.int_param("id"))}


@api("POST", "/api/erp/reconcile/rows/{id:int}/resend", role="approver")
def resend_row(req: Req):
    try:
        job = erp_reconcile.resend_row(req.int_param("id"), req.uid)
    except erp_reconcile.ReconcileError as e:
        raise conflict(str(e), "cannot_resend")
    run_id = db.scalar("SELECT run_id FROM erp_recon_rows WHERE id = ?", (req.int_param("id"),))
    return {"job": job, **erp_reconcile.run_detail(run_id)}


@api("GET", "/api/erp/reconcile/{id:int}/export", role="approver")
def export_check(req: Req):
    try:
        data, name = erp_reconcile.export_xlsx(req.int_param("id"))
    except ImportError:
        raise bad("The Excel export needs the openpyxl package on the server.")
    except erp_reconcile.ReconcileError as e:
        raise bad(str(e))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
