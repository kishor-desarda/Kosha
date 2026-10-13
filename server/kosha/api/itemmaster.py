# SPDX-License-Identifier: AGPL-3.0-or-later
"""Item master: OEM makers and cross-references, finding a part by an OEM number, and allowing a locked
customer part to be reused. (The item fields themselves are edited through PATCH /api/items/{id}.)"""
from __future__ import annotations

from .. import db, itemmaster
from ..web import Req, api, bad


@api("GET", "/api/oems")
def oems(req: Req):
    return {"oems": itemmaster.oems(), "sources": itemmaster.SOURCES, "fits": itemmaster.FITS,
            "can_add": req.has("approver")}


@api("POST", "/api/oems", role="approver")
def add_oem(req: Req):
    return {"oem": itemmaster.add_oem(req.uid, req.b("code") or "", req.b("name") or "")}


@api("GET", "/api/items/{id:int}/oem-refs")
def item_oem_refs(req: Req):
    iid = req.int_param("id")
    itemmaster.item_or_404(iid)
    return {"oem_refs": itemmaster.oem_refs(iid)}


@api("POST", "/api/items/{id:int}/oem-refs", role="engineer")
def add_item_oem_ref(req: Req):
    body = req.body if isinstance(req.body, dict) else {}
    return {"oem_ref": itemmaster.add_oem_ref(req.uid, req.int_param("id"), body)}


@api("DELETE", "/api/oem-refs/{id:int}", role="engineer")
def remove_oem_ref(req: Req):
    itemmaster.remove_oem_ref(req.uid, req.int_param("id"))
    return {"ok": True}


@api("GET", "/api/oem-refs")
def find_oem(req: Req):
    return {"matches": itemmaster.find_by_oem(req.q("q") or "")}


@api("POST", "/api/items/{id:int}/allow-reuse", role="admin")
def allow_reuse(req: Req):
    parent = req.b("parent_item_id")
    pn = (req.b("parent_part_number") or "").strip()
    if not isinstance(parent, int) and pn:
        parent = db.scalar("SELECT id FROM items WHERE part_number = ? COLLATE NOCASE", (pn,))
        if not parent:
            raise bad(f"There is no part {pn}. Type the part number of the assembly.")
    if not isinstance(parent, int):
        raise bad("Choose the assembly the part may be used in.")
    return itemmaster.allow_reuse(req.uid, req.int_param("id"), parent, req.b("reason") or "")
