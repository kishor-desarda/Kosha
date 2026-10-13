# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP orders, customers and suppliers, and the category mapping (specification 12.1 and 12.4)."""
from __future__ import annotations

from .. import erp, erp_inbound
from ..packs import PackConfig
from ..web import Req, api


@api("GET", "/api/erp/orders", role="viewer")
def erp_orders(req: Req):
    return {"orders": erp_inbound.orders(req.q("status") or "")}


@api("POST", "/api/erp/orders/{id:int}/package", role="engineer")
def erp_order_package(req: Req):
    days = req.b("days")
    return {"transmittal": erp_inbound.make_package(req.user, req.int_param("id"), int(days) if days else None)}


@api("GET", "/api/erp/parties", role="viewer")
def erp_parties(req: Req):
    return {"parties": erp_inbound.parties(req.q("kind") or "customer", (req.q("q") or "").strip())}


@api("GET", "/api/erp/mapping", role="approver")
def erp_mapping(req: Req):
    """Every category of the industry packs with what the pack proposes and what the admin set."""
    cfg = erp.settings()
    mine = cfg.get("category_map") or {}
    rows = []
    for c in PackConfig.current().categories():
        code = str(c.get("code") or "").upper()
        if not code:
            continue
        m = mine.get(code) or {}
        rows.append({"code": code, "name": c.get("name") or code, "pack_item_group": c.get("erp_item_group") or "",
                     "pack_hsn": str(c.get("hsn") or ""), "item_group": m.get("item_group", ""), "hsn": m.get("hsn", "")})
    known = {r["code"] for r in rows}
    rows += [{"code": k, "name": k, "pack_item_group": "", "pack_hsn": "", **v} for k, v in mine.items() if k not in known]
    return {"categories": rows, "uom_map": cfg.get("uom_map") or {}, "default_uom": cfg.get("default_uom"),
            "attach_pdfs": cfg.get("attach_pdfs", True), "nightly_reconcile": cfg.get("nightly_reconcile", True)}
