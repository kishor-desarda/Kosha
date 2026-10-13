# SPDX-License-Identifier: AGPL-3.0-or-later
"""Items, BOMs, change orders, ERP, common parts and the CAD add-in API."""
from __future__ import annotations

import json

from starlette.responses import Response

from .. import access, addins, boms, changes, db, erp, itemmaster, lifecycle, similarity
from ..cad import process as cadproc
from ..web import Req, api, as_bool, bad, conflict, not_found
from ..packs import PackConfig
from .core import DOC_SELECT, _with_preview

from kosha_sdk import AddinError  # noqa: E402

# ------------------------------------------------------------------ items --

ITEM_EDITABLE = ("description", "material", "uom", "make_buy", "weight_kg", "item_group", "item_type")


@api("GET", "/api/items")
def items(req: Req):
    q = (req.q("q") or "").strip()
    limit = min(int(req.q("limit", 100)), 1000)
    if q:
        # an OEM part number or the customer's part number finds the company's part as well
        rows = db.rows("SELECT * FROM items i WHERE (part_number LIKE ? OR description LIKE ? OR customer_part_number "
                       "LIKE ? OR id IN (SELECT item_id FROM item_oem_refs WHERE deleted = 0 AND (oem_part_number LIKE ? "
                       "OR oem_model LIKE ?)))" + access.scope().items() + " ORDER BY part_number LIMIT ?",
                       (*[f"%{q}%"] * 5, limit))
    else:
        rows = db.rows("SELECT * FROM items i WHERE 1 = 1" + access.scope().items() + " ORDER BY updated_at DESC LIMIT ?",
                       (limit,))
    return {"items": rows}


@api("POST", "/api/items", role="engineer")
def create_item(req: Req):
    pn = (req.b("part_number") or "").strip().upper()
    if not pn:
        raise bad("Part number is required")
    if db.one("SELECT id FROM items WHERE part_number = ? COLLATE NOCASE", (pn,)):
        raise conflict(f"{pn} already exists.")
    with db.tx():
        iid = boms.ensure_item(pn, req.b("description") or "", req.uid, req.b("uom") or "Nos", req.b("material") or "",
                               req.b("make_buy") or None)
        upd = {k: req.b(k) for k in ("item_group", "item_type") if req.b(k)}
        if upd:
            db.update("items", iid, upd)
        db.audit(req.uid, "item_create", "item", iid, pn)
    return {"item": db.one("SELECT * FROM items WHERE id = ?", (iid,))}


@api("GET", "/api/items/{id:int}")
def item(req: Req):
    iid = req.int_param("id")
    it = db.one("SELECT * FROM items WHERE id = ?", (iid,))
    if not it:
        raise not_found("Item not found")
    access.check_item(iid)
    it["props"] = json.loads(it.pop("props_json") or "{}")
    names = lifecycle.user_names()
    docs = _with_preview(db.rows(DOC_SELECT + "WHERE d.item_id = ? AND d.deleted = 0" + access.scope().documents(),
                                 (iid,)), names)
    item_boms = db.rows("SELECT id, revision, state, source, updated_at FROM boms WHERE item_id = ? ORDER BY id DESC", (iid,))
    jobs = db.rows("SELECT * FROM erp_jobs WHERE (kind IN ('item','refresh') AND entity_id = ?) OR (kind = 'bom' AND "
                   "entity_id IN (SELECT id FROM boms WHERE item_id = ?)) ORDER BY id DESC LIMIT 20", (iid, iid))
    std = db.one("SELECT id, part_number, description FROM items WHERE id = ?", (it["standard_item_id"],)) \
        if it["standard_item_id"] else None
    dups = db.rows("SELECT id, part_number, description FROM items WHERE standard_item_id = ?", (iid,))
    families = [{"code": f["code"], "name": f.get("name", f["code"])} for f in PackConfig.current().families()]
    fam = next((f for f in families if f["code"] == it.get("family")), None)
    reuse = db.rows("SELECT a.parent_item_id, i.part_number AS parent_part_number, a.reason, a.approved_by, "
                    "a.approved_at FROM item_reuse_approvals a JOIN items i ON i.id = a.parent_item_id "
                    "WHERE a.item_id = ? ORDER BY a.id", (iid,))
    for r in reuse:
        r["approved_by_name"] = names.get(r["approved_by"], "?")
    return {"item": it, "documents": docs, "boms": item_boms, "where_used": boms.where_used(iid), "erp_jobs": jobs,
            "standard_part": std, "duplicates": dups, "family": fam, "families": families, "oem_refs": itemmaster.oem_refs(iid),
            "reuse_allowed": reuse}


@api("PATCH", "/api/items/{id:int}", role="engineer")
def update_item(req: Req):
    iid = req.int_param("id")
    it = db.one("SELECT * FROM items WHERE id = ?", (iid,))
    if not it:
        raise not_found("Item not found")
    access.check_item(iid)
    body = req.body if isinstance(req.body, dict) else {}
    upd = {}
    for k in ITEM_EDITABLE:
        if k in body:
            v = body[k]
            if k == "make_buy" and v not in ("make", "buy"):
                raise bad("make_buy must be make or buy")
            if k == "weight_kg":
                v = float(v) if v not in (None, "") else None
            upd[k] = v
    upd.update(itemmaster.clean_fields(body, it))
    if not upd:
        return {"item": it}
    guarded = [k for k in itemmaster.GUARDED if k in upd and upd[k] != it.get(k)]
    if guarded and not req.has("approver"):
        raise conflict("Only an approver can change the customer, the restricted flag or the reuse lock of a part.")
    if it["state"] == "released" and not req.has("approver"):
        locked = {"description", "material", "uom"} & set(upd)
        if locked:
            raise conflict("Released items can only have description, material and unit changed by an approver "
                           "or through a new revision.")
    upd["updated_at"] = db.now()
    db.update("items", iid, upd)
    db.audit(req.uid, "item_update", "item", iid, ",".join(upd))
    return {"item": db.one("SELECT * FROM items WHERE id = ?", (iid,))}


# ------------------------------------------------------------------- BOMs --

@api("GET", "/api/boms")
def list_boms(req: Req):
    return {"boms": db.rows(
        "SELECT b.id, b.revision, b.state, b.source, b.updated_at, i.id AS item_id, i.part_number, i.description, "
        "(SELECT COUNT(*) FROM bom_lines WHERE bom_id = b.id) AS lines FROM boms b JOIN items i ON i.id = b.item_id "
        "ORDER BY b.updated_at DESC LIMIT 500")}


@api("POST", "/api/boms", role="engineer")
def create_bom(req: Req):
    item_id = req.b("item_id")
    if not item_id and req.b("part_number"):
        item_id = boms.ensure_item(req.b("part_number"), req.b("description") or "", req.uid)
    if not item_id:
        raise bad("Choose the assembly item")
    bid = boms.create(req.uid, int(item_id), req.b("revision"), "manual")
    return {"bom_id": bid}


@api("GET", "/api/boms/{id:int}")
def get_bom(req: Req):
    bid = req.int_param("id")
    b = boms.bom_or_404(bid)
    revs = db.rows("SELECT id, revision, state FROM boms WHERE item_id = ? ORDER BY id", (b["item_id"],))
    doc = db.one("SELECT id, filename FROM documents WHERE item_id = ? AND doc_type = 'assembly' AND deleted = 0 "
                 "ORDER BY updated_at DESC LIMIT 1", (b["item_id"],))
    jobs = db.rows("SELECT id, status, error, updated_at FROM erp_jobs WHERE kind = 'bom' AND entity_id = ? ORDER BY id "
                   "DESC LIMIT 5", (bid,))
    return {"bom": b, "lines": boms.lines(bid), "tree": boms.explode(bid), "issues": boms.validate(bid),
            "revisions": revs, "assembly_document": doc, "erp_jobs": jobs}


@api("PUT", "/api/boms/{id:int}/lines", role="engineer")
def put_lines(req: Req):
    bid = req.int_param("id")
    lines = (req.body or {}).get("lines") if isinstance(req.body, dict) else None
    if lines is None:
        raise bad("lines are required")
    boms.set_lines(req.uid, bid, lines)
    return get_bom(req)


@api("POST", "/api/boms/generate", role="engineer")
def generate(req: Req):
    did = req.b("document_id")
    if not did:
        raise bad("document_id is required")
    return boms.generate_from_document(req.user, int(did))


@api("POST", "/api/boms/{id:int}/import", role="engineer")
def import_bom(req: Req):
    files = req.files.get("file") or []
    if not files:
        raise bad("Choose the BOM file (.xlsx or .csv) exported from SolidWorks")
    f = files[0]
    return boms.import_table(req.uid, req.int_param("id"), f.filename, f.file.read())


@api("POST", "/api/boms/{id:int}/release", role="approver")
def release_bom(req: Req):
    bid = req.int_param("id")
    b = boms.bom_or_404(bid)
    if b["state"] == "released":
        return {"ok": True}
    errors = [i for i in boms.validate(bid) if i["severity"] == "error"]
    if errors:
        raise conflict("Fix the errors first: " + "; ".join(e["message"] for e in errors[:5]), "bom_errors")
    if not boms.lines(bid):
        raise conflict("The BOM is empty.", "bom_empty")
    db.update("boms", bid, {"state": "released", "updated_at": db.now()})
    db.audit(req.uid, "bom_release", "bom", bid, f"{b['part_number']} rev {b['revision']}")
    if (erp.settings() or {}).get("auto_push_on_release", True):
        erp.enqueue("item", b["item_id"], req.uid, f"BOM {b['part_number']} rev {b['revision']}")
        erp.enqueue("bom", bid, req.uid, f"BOM {b['part_number']} rev {b['revision']}")
    addins.registry.emit("bom.released", {"bom_id": bid, "part_number": b["part_number"], "revision": b["revision"],
                                          "user_id": req.uid})
    return {"ok": True}


@api("POST", "/api/boms/{id:int}/push", role="approver")
def push_bom(req: Req):
    bid = req.int_param("id")
    b = boms.bom_or_404(bid)
    erp.enqueue("item", b["item_id"], req.uid, "manual")
    jid = erp.enqueue("bom", bid, req.uid, "manual")
    erp.process_pending()
    return {"job": db.one("SELECT * FROM erp_jobs WHERE id = ?", (jid,))}


@api("GET", "/api/boms/{id:int}/compare")
def compare(req: Req):
    other = req.q("with")
    if not other:
        raise bad("with= is required")
    return boms.compare(int(other), req.int_param("id"))


@api("GET", "/api/boms/{id:int}/export")
def export(req: Req):
    data, name, mime = boms.export(req.int_param("id"), req.q("format", "xlsx"), req.q("indented", "1") != "0")
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@api("GET", "/api/items/{id:int}/where-used")
def where_used(req: Req):
    return {"where_used": boms.where_used(req.int_param("id"))}


# ---------------------------------------------------------------- changes --

@api("GET", "/api/changes")
def list_changes(req: Req):
    st = req.q("state")
    names = lifecycle.user_names()
    rows = db.rows("SELECT c.*, (SELECT COUNT(*) FROM co_items WHERE co_id = c.id) AS items FROM change_orders c" +
                   (" WHERE c.state = ?" if st else "") + " ORDER BY c.id DESC LIMIT 500", (st,) if st else ())
    for r in rows:
        r["requested_by_name"] = names.get(r["requested_by"], "?")
    return {"changes": rows}


@api("POST", "/api/changes", role="engineer")
def create_change(req: Req):
    co_id = changes.create(req.user, req.b("title") or "", req.b("reason") or "", req.b("description") or "",
                           req.b("priority") or "normal", req.b("document_ids") or [], req.b("approver_ids") or [])
    return changes.detail(co_id)


@api("GET", "/api/changes/{id:int}")
def get_change(req: Req):
    return changes.detail(req.int_param("id"))


@api("PATCH", "/api/changes/{id:int}", role="engineer")
def patch_change(req: Req):
    cid = req.int_param("id")
    co = changes.co_or_404(cid)
    changes._editable(co, req.user)
    upd = {k: req.b(k) for k in ("title", "reason", "description", "priority") if req.b(k) is not None}
    db.update("change_orders", cid, upd)
    return changes.detail(cid)


@api("POST", "/api/changes/{id:int}/items", role="engineer")
def add_change_item(req: Req):
    cid = req.int_param("id")
    for d in req.b("document_ids") or ([req.b("document_id")] if req.b("document_id") else []):
        changes.add_item(req.user, cid, int(d), req.b("action") or "release")
    return changes.detail(cid)


@api("DELETE", "/api/changes/{id:int}/items/{doc:int}", role="engineer")
def remove_change_item(req: Req):
    changes.remove_item(req.user, req.int_param("id"), req.int_param("doc"))
    return changes.detail(req.int_param("id"))


@api("POST", "/api/changes/{id:int}/approvers", role="engineer")
def add_change_approver(req: Req):
    changes.add_approver(req.user, req.int_param("id"), int(req.b("user_id")))
    return changes.detail(req.int_param("id"))


@api("DELETE", "/api/changes/{id:int}/approvers/{uid:int}", role="engineer")
def remove_change_approver(req: Req):
    changes.remove_approver(req.user, req.int_param("id"), req.int_param("uid"))
    return changes.detail(req.int_param("id"))


@api("POST", "/api/changes/{id:int}/revise", role="engineer")
def revise_change_items(req: Req):
    n = changes.revise_items(req.user, req.int_param("id"))
    return {**changes.detail(req.int_param("id")), "revised": n}


@api("POST", "/api/changes/{id:int}/submit", role="engineer")
def submit_change(req: Req):
    changes.submit(req.user, req.int_param("id"))
    addins.registry.emit("change.submitted", {"change_id": req.int_param("id"), "user_id": req.uid})
    return changes.detail(req.int_param("id"))


@api("POST", "/api/changes/{id:int}/decide", role="approver")
def decide_change(req: Req):
    changes.decide(req.user, req.int_param("id"), req.b("decision") or "", req.b("comment") or "")
    return changes.detail(req.int_param("id"))


@api("POST", "/api/changes/{id:int}/implement", role="approver")
def implement_change(req: Req):
    res = changes.implement(req.user, req.int_param("id"))
    addins.registry.emit("change.implemented", {"change_id": req.int_param("id"), "user_id": req.uid})
    erp.process_pending()
    return {**changes.detail(req.int_param("id")), **res}


@api("POST", "/api/changes/{id:int}/cancel", role="engineer")
def cancel_change(req: Req):
    changes.cancel(req.user, req.int_param("id"))
    return changes.detail(req.int_param("id"))


@api("POST", "/api/changes/{id:int}/comments", role="viewer")
def comment_change(req: Req):
    cid = req.int_param("id")
    changes.co_or_404(cid)
    text = (req.b("text") or "").strip()
    if not text:
        raise bad("Write a comment first.")
    db.insert("co_comments", {"co_id": cid, "user_id": req.uid, "text": text[:4000], "at": db.now()})
    return changes.detail(cid)


# -------------------------------------------------------------------- ERP --

def _connector_info() -> dict:
    """Which connector add-in is in use, and the fields its manifest asks for."""
    try:
        cid = erp.connector_id()
    except erp.ErpError as ex:
        return {"connector": "", "connector_name": "", "connector_fields": [], "connector_help": str(ex)}
    a = addins.registry.get(cid)
    m = a.manifest if a else None
    return {
        "connector": cid,
        "connector_name": (m.name.replace(" connector", "") if m else cid),
        "connector_status": a.status if a else "missing",
        "connector_fields": [{"key": s.key, "type": s.type, "label": s.label, "hint": s.hint,
                              "required": s.required, "choices": list(s.choices)} for s in (m.settings if m else ())],
        "connector_help": m.help if m else "",
        "connectors": [{"id": x.id, "name": x.manifest.name, "status": x.status}
                       for x in addins.registry.by_kind("erp-connector", enabled_only=False)],
    }


@api("GET", "/api/erp/settings", role="approver")
def erp_settings(req: Req):
    cfg = dict(erp.settings())
    info = _connector_info()
    if info["connector"] and addins.registry.get(info["connector"]):
        cfg.update(addins.registry.masked_settings(info["connector"]))
    cfg.update(info)
    return cfg


@api("PUT", "/api/erp/settings", role="admin")
def put_erp_settings(req: Req):
    body = req.body if isinstance(req.body, dict) else {}
    generic = {}
    for k in erp.GENERIC_KEYS:
        if k not in body:
            continue
        v = body[k]
        if k in ("enabled", "dry_run", "auto_push_on_release"):
            v = as_bool(v)
        if k == "uom_map" and not isinstance(v, dict):
            raise bad("uom_map must be an object")
        if k == "connector" and v and v not in {a.id for a in addins.registry.by_kind("erp-connector", enabled_only=False)}:
            raise bad("Choose one of the ERP connector add-ins that are installed.")
        generic[k] = v
    cid = _connector_info()["connector"]     # the connection fields on the form belong to the connector shown
    erp.save_settings(generic)
    rest = {k: v for k, v in body.items() if k not in erp.GENERIC_KEYS}
    if cid and rest:
        try:
            addins.registry.save_settings(cid, rest, req.uid)
        except AddinError as ex:
            raise bad(str(ex))
    db.audit(req.uid, "erp_settings", "settings", None, "")
    return erp_settings(req)


@api("POST", "/api/erp/test", role="admin")
def erp_test(req: Req):
    try:
        h = erp.call("health")
        return {"ok": h.ok, "message": h.message}
    except AddinError as ex:
        return {"ok": False, "message": str(ex)}


@api("POST", "/api/erp/webhook", role=None, auth=False)
async def erp_webhook(req: Req):
    """Calls from the ERP (stock moved, item changed, BOM cancelled). The connector add-in checks the signature;
    Kosha then refreshes stock and cost for the items concerned. Nothing else can be changed this way."""
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse
    body = await req.request.body()
    if len(body) > 1_000_000:
        return JSONResponse({"error": "Too large", "code": "too_large"}, status_code=413)
    headers = dict(req.request.headers)
    try:
        events = await run_in_threadpool(erp.call, "handle_webhook", headers, body)
    except AddinError as ex:
        status = getattr(ex, "status", None) or 400
        return JSONResponse({"error": str(ex), "code": "refused"}, status_code=status if status in (400, 401, 403) else 400)
    return await run_in_threadpool(_apply_erp_events, events)


def _apply_erp_events(events) -> dict:
    queued = 0
    for e in events:
        item = db.one("SELECT id FROM items WHERE part_number = ? COLLATE NOCASE", (e.code,))
        if not item:
            continue
        if e.kind in ("stock_changed", "item_changed"):
            erp.enqueue("refresh", item["id"], None, f"ERP: {e.kind}")
            queued += 1
        elif e.kind == "bom_cancelled":
            db.audit(None, "erp_bom_cancelled", "item", item["id"], e.detail or "BOM cancelled in the ERP")
    return {"accepted": len(events), "queued": queued}


@api("GET", "/api/erp/jobs", role="viewer")
def erp_jobs(req: Req):
    st = req.q("status")
    rows = db.rows("SELECT * FROM erp_jobs" + (" WHERE status = ?" if st else "") + " ORDER BY id DESC LIMIT 300",
                   (st,) if st else ())
    for r in rows:
        if r["kind"] in ("item", "refresh"):
            it = db.one("SELECT part_number, description FROM items WHERE id = ?", (r["entity_id"],))
        else:
            it = db.one("SELECT i.part_number, i.description, b.revision FROM boms b JOIN items i ON i.id = b.item_id "
                        "WHERE b.id = ?", (r["entity_id"],))
        r["label"] = (f"{it['part_number']} {it['description']}" + (f" (BOM rev {it['revision']})" if r["kind"] == "bom" else "")) \
            if it else f"#{r['entity_id']}"
    counts = {s: db.scalar("SELECT COUNT(*) FROM erp_jobs WHERE status = ?", (s,))
              for s in ("pending", "running", "sent", "failed", "dry_run")}
    return {"jobs": rows, "counts": counts}


@api("POST", "/api/erp/jobs/{id:int}/retry", role="approver")
def erp_retry(req: Req):
    jid = req.int_param("id")
    db.update("erp_jobs", jid, {"status": "pending", "updated_at": db.now()})
    return {"job": erp.run_job(jid)}


@api("POST", "/api/erp/process", role="approver")
def erp_process(req: Req):
    return {"processed": erp.process_pending()}


@api("POST", "/api/erp/push", role="approver")
def erp_push(req: Req):
    kind = req.b("kind")
    if kind not in ("item", "bom", "refresh"):
        raise bad("kind must be item, bom or refresh")
    jid = erp.enqueue(kind, int(req.b("id")), req.uid, "manual")
    return {"job": erp.run_job(jid)}


# ----------------------------------------------------------- common parts --

@api("POST", "/api/common-parts/analyze", role="engineer")
def cp_analyze(req: Req):
    folder = req.b("folder_id")
    if db.scalar("SELECT COUNT(*) FROM cp_runs WHERE status = 'running'"):
        raise conflict("A duplicate analysis is already running.")
    run_id = similarity.start_run(req.uid, int(folder) if folder else None, float(req.b("threshold") or 0.9),
                                  req.b("kinds") or None)
    return {"run_id": run_id}


@api("GET", "/api/common-parts/runs/latest")
def cp_latest(req: Req):
    r = db.one("SELECT * FROM cp_runs ORDER BY id DESC LIMIT 1")
    total = db.scalar("SELECT COUNT(*) FROM documents d JOIN versions v ON v.document_id = d.id AND v.version = "
                      "d.latest_version WHERE d.deleted = 0 AND v.signature_json IS NOT NULL")
    no_sig = db.scalar("SELECT COUNT(*) FROM documents d JOIN versions v ON v.document_id = d.id AND v.version = "
                       "d.latest_version WHERE d.deleted = 0 AND v.signature_json IS NULL AND d.ext IN ('dwg','dxf','sldprt')")
    counts = {r_["kind"]: r_["n"] for r_ in db.rows("SELECT kind, COUNT(*) AS n FROM cp_groups WHERE status = 'open' "
                                                     "GROUP BY kind")}
    return {"run": r, "comparable": total, "not_comparable": no_sig, "open_by_kind": counts,
            "files": db.scalar("SELECT COUNT(*) FROM documents WHERE deleted = 0"),
            "items": db.scalar("SELECT COUNT(*) FROM items")}


@api("GET", "/api/common-parts/groups")
def cp_groups(req: Req):
    st = req.q("status", "open")
    kind = req.q("kind")
    where, params = [], []
    if st != "all":
        where.append("g.status = ?")
        params.append(st)
    if kind:
        where.append("g.kind = ?")
        params.append(kind)
    groups = db.rows("SELECT g.*, (SELECT COUNT(*) FROM cp_members m WHERE m.group_id = g.id) + "
                     "(SELECT COUNT(*) FROM cp_item_members m WHERE m.group_id = g.id) AS n FROM cp_groups g" +
                     (" WHERE " + " AND ".join(where) if where else "") +
                     " ORDER BY CASE g.kind WHEN 'identical' THEN 0 WHEN 'part_number' THEN 1 WHEN 'geometry' THEN 2 "
                     "ELSE 3 END, n DESC, g.score_max DESC LIMIT 1000", params)
    for g in groups:
        g["preview_ids"] = [r["document_id"] for r in db.rows(
            "SELECT document_id FROM cp_members WHERE group_id = ? ORDER BY is_reference DESC, score DESC LIMIT 4", (g["id"],))]
        g["item_numbers"] = [r["part_number"] for r in db.rows(
            "SELECT i.part_number FROM cp_item_members m JOIN items i ON i.id = m.item_id WHERE m.group_id = ? "
            "ORDER BY m.is_reference DESC LIMIT 4", (g["id"],))]
    return {"groups": groups, "labels": similarity.KIND_LABEL}


@api("GET", "/api/common-parts/groups/{id:int}")
def cp_group(req: Req):
    g = similarity.group_detail(req.int_param("id"))
    if not g:
        raise not_found("Group not found")
    return g


@api("POST", "/api/common-parts/groups/{id:int}/decide", role="engineer")
def cp_decide(req: Req):
    return similarity.decide(req.user, req.int_param("id"), req.b("decision") or "", req.b("standard_document_id"),
                             req.b("note") or "", req.b("standard_item_id"))


@api("GET", "/api/common-parts/report")
def cp_report(req: Req):
    try:
        data, name = similarity.report_bytes(req.q("status", "open"))
    except ImportError:
        raise bad("The duplicate report needs the openpyxl package on the server.")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------ CAD add-in --

@api("GET", "/api/addin/status")
def addin_status(req: Req):
    folder = req.q("folder") or ""
    filename = req.q("filename") or ""
    try:
        fid = _find_folder(folder)
    except Exception:
        return {"exists": False}
    d = db.one("SELECT * FROM documents WHERE folder_id = ? AND filename = ? COLLATE NOCASE AND deleted = 0",
               (fid, filename)) if fid else None
    if not d:
        return {"exists": False}
    from .files import actions_for
    s = lifecycle.doc_summary(d, lifecycle.user_names())
    s["exists"] = True
    s["actions"] = actions_for(d, req.user)
    s["latest_sha"] = db.scalar("SELECT blob_sha FROM versions WHERE document_id = ? AND version = ?",
                                (d["id"], d["latest_version"]))
    return s


def _find_folder(path: str) -> int | None:
    import re
    parts = [p for p in re.split(r"[\\/]+", path or "") if p]
    if parts and parts[0].lower() == "kosha":
        parts = parts[1:]
    parent = lifecycle.root_folder_id()
    for name in parts:
        row = db.one("SELECT id FROM folders WHERE IFNULL(parent_id,0) = ? AND name = ? COLLATE NOCASE", (parent, name))
        if not row:
            return None
        parent = row["id"]
    return parent or None


@api("POST", "/api/addin/checkout", role="engineer")
def addin_checkout(req: Req):
    fid = _find_folder(req.b("folder") or "")
    d = db.one("SELECT id FROM documents WHERE folder_id = ? AND filename = ? COLLATE NOCASE AND deleted = 0",
               (fid, req.b("filename") or "")) if fid else None
    if not d:
        raise not_found("This file is not in Kosha yet.")
    return lifecycle.doc_summary(lifecycle.checkout(req.user, d["id"]), lifecycle.user_names())


@api("POST", "/api/addin/checkin", role="engineer")
def addin_checkin(req: Req):
    """Check-in from a CAD add-in: file + properties + preview + mesh + assembly structure."""
    files = req.files.get("file") or []
    if not files:
        raise bad("No file")
    f = files[0]
    fid = lifecycle.ensure_folder_path(req.b("folder") or "", req.uid)
    props = {}
    if req.b("props"):
        try:
            raw = json.loads(req.b("props"))
        except ValueError:
            raise bad("props must be JSON")
        keymap = {"partno": "part_number", "part number": "part_number", "part_number": "part_number",
                  "number": "part_number", "description": "description", "material": "material",
                  "weight": "weight_kg", "mass": "weight_kg", "finish": "finish", "vendor": "vendor"}
        for k, v in raw.items():
            key = keymap.get(str(k).strip().lower())
            if key and v not in (None, ""):
                props[key] = str(v).strip()
    preview = None
    if req.files.get("preview"):
        preview = ("png", req.files["preview"][0].file.read())
    mesh = None
    if req.files.get("mesh"):
        mesh = ("stl", req.files["mesh"][0].file.read())
    structure = None
    if req.b("structure"):
        try:
            structure = json.loads(req.b("structure"))
        except ValueError:
            raise bad("structure must be JSON")
    extra_meta = {"source": (req.b("source") or "cad-addin")[:60]}
    if req.b("props"):   # CAD custom properties of this version, for the measured change list
        extra_meta["properties"] = {str(k)[:80]: str(v)[:500] for k, v in list(raw.items())[:200]}
    if structure is not None:
        extra_meta["structure"] = structure
    if req.b("configuration"):
        extra_meta["configuration"] = req.b("configuration")
    d = lifecycle.checkin(req.user, fid, f.filename, f.file, req.b("comment") or "",
                         as_bool(req.b("keep_checked_out")), props, preview, mesh, extra_meta)
    out = lifecycle.doc_summary(d, lifecycle.user_names())
    if structure and d["doc_type"] == "assembly" and d["item_id"]:
        b = db.one("SELECT state FROM boms WHERE item_id = ? AND revision = ?", (d["item_id"], d["revision"]))
        if not b or b["state"] != "released":
            out["bom"] = boms.generate_from_document(req.user, d["id"])
    return out


@api("GET", "/api/addin/similar")
def addin_similar(req: Req):
    """Warn designers when a part with near-identical size already exists."""
    try:
        sig = {"kind": "mesh", "bbox": sorted([float(req.q("x")), float(req.q("y")), float(req.q("z"))], reverse=True),
               "volume": float(req.q("volume")), "area": float(req.q("area"))}
    except (TypeError, ValueError):
        raise bad("x, y, z, volume and area are required")
    out = []
    for r in db.rows("SELECT d.id, d.filename, d.part_number, d.description, d.state, v.signature_json FROM documents d "
                     "JOIN versions v ON v.document_id = d.id AND v.version = d.latest_version WHERE d.deleted = 0 AND "
                     "v.signature_json LIKE '%\"mesh\"%'"):
        s = similarity.score(sig, json.loads(r["signature_json"]))
        if s >= float(req.q("threshold", 0.93)):
            out.append({"id": r["id"], "filename": r["filename"], "part_number": r["part_number"],
                        "description": r["description"], "state": r["state"], "score": s})
    out.sort(key=lambda x: -x["score"])
    return {"similar": out[:8]}


@api("GET", "/api/meta/types")
def types(req: Req):
    return {"types": cadproc.DOC_TYPES, "labels": cadproc.CAD_LABEL, "states": lifecycle.STATE_LABEL}
