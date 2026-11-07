# SPDX-License-Identifier: AGPL-3.0-or-later
"""Items, BOMs, change orders, ERP, common parts and the CAD add-in API."""
from __future__ import annotations

import json
import logging

from starlette.responses import Response

from .. import access, addins, apisafety, auditchain, boms, changes, db, deletionlog, erp, erp_inbound, itemmaster, lifecycle, similarity
from ..cad import process as cadproc
from ..web import ApiError, Req, api, as_bool, bad, conflict, forbidden, not_found
from ..packs import PackConfig
from .core import DOC_SELECT, _with_preview

from kosha_sdk import AddinError  # noqa: E402

log = logging.getLogger("kosha.api.eng")

# ------------------------------------------------------------------ items --

ITEM_EDITABLE = ("description", "material", "uom", "make_buy", "weight_kg", "item_group", "item_type")


@api("GET", "/api/items")
def items(req: Req):
    q = (req.q("q") or "").strip()
    paged, after, limit = apisafety.cursor_args(req, 100)
    select = """SELECT i.*,
        (SELECT COUNT(*) FROM documents d WHERE d.item_id = i.id AND d.deleted = 0 AND d.purged_at IS NULL) AS active_documents,
        (SELECT COUNT(*) FROM documents d WHERE d.item_id = i.id AND d.deleted = 1 AND d.purged_at IS NULL) AS recycle_bin_documents,
        (SELECT COUNT(*) FROM documents d WHERE d.item_id = i.id AND d.purged_at IS NOT NULL) AS purged_documents
        FROM items i WHERE 1 = 1"""
    if paged:
        sql = select + access.scope().items()
        params: list = []
        if q:
            sql += " AND (i.part_number LIKE ? OR i.description LIKE ?)"
            params += [f"%{q}%"] * 2
        for field in ("state", "item_type", "family"):
            if req.q(field):
                sql += f" AND i.{field} = ?"
                params.append(req.q(field))
        if req.q("category"):
            sql += " AND i.part_number LIKE ?"
            params.append(req.q("category").upper() + "-%")
        if after is not None:
            sql += " AND i.id < ?"
            params.append(after)
        rows, nxt = apisafety.next_cursor(
            db.rows(sql + " ORDER BY i.id DESC LIMIT ?", [*params, limit + 1]), limit)
        return {"items": rows, "next_cursor": nxt}
    if q:
        sql = """SELECT i.*,
            (SELECT COUNT(*) FROM documents d WHERE d.item_id = i.id AND d.deleted = 0 AND d.purged_at IS NULL) AS active_documents,
            (SELECT COUNT(*) FROM documents d WHERE d.item_id = i.id AND d.deleted = 1 AND d.purged_at IS NULL) AS recycle_bin_documents,
            (SELECT COUNT(*) FROM documents d WHERE d.item_id = i.id AND d.purged_at IS NOT NULL) AS purged_documents
            FROM items i
            WHERE (i.part_number LIKE ? OR i.description LIKE ? OR i.customer_part_number LIKE ?
              OR i.id IN (SELECT item_id FROM item_oem_refs WHERE deleted = 0
                          AND (oem_part_number LIKE ? OR oem_model LIKE ?)))"""
        rows = db.rows(sql + access.scope().items() + " ORDER BY i.part_number LIMIT ?",
                       (*[f"%{q}%"] * 5, limit))
    else:
        rows = db.rows(select + access.scope().items() + " ORDER BY i.updated_at DESC LIMIT ?", (limit,))
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
    addins.registry.emit("item.created", {"item_id": iid, "part_number": pn, "user_id": req.uid})
    return {"item": db.one("SELECT * FROM items WHERE id = ?", (iid,))}


@api("DELETE", "/api/items/{id:int}", role="engineer")
def delete_item(req: Req):
    """Permanently delete an in-work item and its exclusively owned file/BOM tree.

    Shared BOM children are retained. Consequential change orders, released records,
    ERP history and other protected dependencies prevent deletion.
    """
    iid = req.int_param("id")
    from .. import filesafety, store

    document_ids: list[int] = []
    deleted_documents: list[dict] = []
    owned_bom_ids: list[int] = []
    content_shas: set[str] = set()
    deleted_items: list[dict] = []
    journal = deletionlog.DeletionJournal()

    # The journal wraps the DB transaction. COMMITTED is written only after the
    # transaction has actually committed; an exception is classified after rollback.
    with journal:
        with db.tx():
            root = db.one("SELECT * FROM items WHERE id = ?", (iid,))
            if not root:
                raise not_found("Item not found")
            access.check_item(iid)

            candidate_ids = {iid}
            pending = [iid]
            while pending:
                parent_id = pending.pop()
                for row in db.rows(
                    "SELECT DISTINCT bl.child_item_id FROM bom_lines bl JOIN boms b ON b.id = bl.bom_id "
                    "WHERE b.item_id = ?", (parent_id,)
                ):
                    child_id = row["child_item_id"]
                    if child_id not in candidate_ids:
                        candidate_ids.add(child_id)
                        pending.append(child_id)

            # Keep a child if any BOM outside this deletion tree still uses it.
            owned = set(candidate_ids)
            changed = True
            while changed:
                changed = False
                for child_id in list(owned - {iid}):
                    outside = db.scalar(
                        "SELECT 1 FROM bom_lines bl JOIN boms b ON b.id = bl.bom_id "
                        "WHERE bl.child_item_id = ? AND b.item_id NOT IN (" +
                        ",".join("?" for _ in owned) + ") LIMIT 1",
                        (child_id, *sorted(owned)),
                    )
                    if outside:
                        owned.remove(child_id)
                        changed = True

            if db.scalar(
                "SELECT 1 FROM bom_lines bl JOIN boms b ON b.id = bl.bom_id "
                "WHERE bl.child_item_id = ? AND b.item_id NOT IN (" +
                ",".join("?" for _ in owned) + ") LIMIT 1",
                (iid, *sorted(owned)),
            ):
                raise conflict("This item is still used by another assembly. Remove that relationship first.",
                               "item_in_use", reference="external BOM")

            marks = ",".join("?" for _ in owned)
            params = tuple(sorted(owned))
            if db.scalar(
                f"SELECT 1 FROM items WHERE standard_item_id IN ({marks}) AND id NOT IN ({marks}) LIMIT 1",
                (*params, *params),
            ):
                raise conflict("An item in this BOM tree is still used as a standard part by another item.",
                               "item_in_use", reference="standard-part links")

            for item_id in sorted(owned):
                item = db.one("SELECT * FROM items WHERE id = ?", (item_id,))
                access.check_item(item_id)
                if item["state"] != "in_work":
                    raise conflict(f"{item['part_number']} is not in work and cannot be deleted.",
                                   "item_lifecycle", part_number=item["part_number"])
                if item["created_by"] != req.uid and not req.has("approver"):
                    raise forbidden(f"Only the creator or an approver can delete {item['part_number']}.")

                bom_ids = [r["id"] for r in db.rows("SELECT id FROM boms WHERE item_id = ?", (item_id,))]
                owned_bom_ids.extend(bom_ids)
                if bom_ids:
                    bm = ",".join("?" for _ in bom_ids)
                    if db.scalar(f"SELECT 1 FROM boms WHERE id IN ({bm}) AND state = 'released' LIMIT 1", bom_ids):
                        raise conflict(f"{item['part_number']} has a released BOM and cannot be deleted.",
                                       "bom_released")
                    if db.scalar(f"SELECT 1 FROM erp_jobs WHERE kind = 'bom' AND entity_id IN ({bm}) LIMIT 1", bom_ids):
                        raise conflict(f"{item['part_number']} has BOM ERP sync history and cannot be deleted.",
                                       "bom_erp_history")
                if db.scalar("SELECT 1 FROM erp_jobs WHERE kind IN ('item', 'refresh') AND entity_id = ? LIMIT 1",
                             (item_id,)):
                    raise conflict(f"{item['part_number']} has ERP sync history and cannot be deleted.",
                                   "item_erp_history")

                docs = db.rows("SELECT * FROM documents WHERE item_id = ?", (item_id,))
                for doc in docs:
                    if db.scalar("SELECT 1 FROM lifecycle_events WHERE document_id = ? AND to_state = 'released' LIMIT 1",
                                 (doc["id"],)):
                        raise conflict(f"{doc['filename']} has been released and cannot be deleted.",
                                       "document_released")
                    if doc["checked_out_by"] and doc["checked_out_by"] != req.uid:
                        raise conflict(f"{doc['filename']} is checked out by another user.", "checked_out_by_other")
                    if doc["created_by"] != req.uid and not req.has("approver"):
                        raise forbidden(f"Only the creator or an approver can delete {doc['filename']}.")
                    document_ids.append(doc["id"])
                    deleted_documents.append(doc)
                deleted_items.append(item)

            if document_ids:
                dm = ",".join("?" for _ in document_ids)
                linked_cos = db.rows(
                    f"SELECT DISTINCT c.number, c.state FROM co_items ci "
                    f"JOIN change_orders c ON c.id = ci.co_id WHERE ci.document_id IN ({dm})",
                    document_ids,
                )
                blocking_cos = [c for c in linked_cos if c["state"] not in ("draft", "rejected", "cancelled")]
                if blocking_cos:
                    labels = ", ".join(f"{c['number']} ({c['state']})" for c in blocking_cos)
                    raise conflict(
                        f"Documents linked to consequential change orders cannot be permanently deleted: {labels}.",
                        "document_in_change_order",
                    )
                if db.scalar(f"SELECT 1 FROM release_outputs WHERE document_id IN ({dm}) LIMIT 1", document_ids):
                    raise conflict("A linked document has release outputs and cannot be permanently deleted.",
                                   "document_release_output")

            # These records have mandatory item references and require their own workflow.
            protected_tables = (
                ("machine_builds", "item_id"),
                ("mboms", "item_id"),
                ("mbom_lines", "child_item_id"),
                ("config_options", "item_id"),
                ("config_rules", "item_id"),
                ("config_rules", "parent_item_id"),
                ("config_rules", "child_item_id"),
                ("config_constraints", "item_id"),
                ("variants", "item_id"),
                ("fmeas", "item_id"),
            )
            for table, column in protected_tables:
                if db.scalar(f"SELECT 1 FROM {table} WHERE {column} IN ({marks}) LIMIT 1", params):
                    raise conflict(
                        f"An item in this deletion tree is referenced by {table.replace('_', ' ')} history or configuration. "
                        "Resolve that record through its own workflow first.",
                        "item_has_dependent_records", reference=table,
                    )

            actor = db.scalar("SELECT full_name FROM users WHERE id = ?", (req.uid,)) or "Unknown user"
            reason = str(req.b("reason") or "User initiated permanent deletion").strip()[:500]
            journal.prepare(actor=actor, reason=reason, items=deleted_items, documents=deleted_documents)

            # The number allocation belongs to the in-work item and is removed with it.
            db.execute(f"DELETE FROM number_allocations WHERE item_id IN ({marks})", params)

            # Preserve quality/deviation records, but detach their optional item/file links.
            for table in ("deviation_affected", "ncrs", "capas"):
                db.execute(f"UPDATE {table} SET item_id = NULL WHERE item_id IN ({marks})", params)
            if document_ids:
                dm = ",".join("?" for _ in document_ids)
                db.execute(f"UPDATE deviation_affected SET document_id = NULL WHERE document_id IN ({dm})",
                           document_ids)

            # Remove project and reference-library associations owned by these items.
            for table in ("project_items", "input_items"):
                db.execute(f"DELETE FROM {table} WHERE item_id IN ({marks})", params)

            # Remove legacy-import traces for these items, including source rows that
            # were imported as part records without a document_id. Keep other rows in
            # the same import batch untouched.
            part_numbers = [item["part_number"] for item in deleted_items]
            legacy_clauses = []
            legacy_params = []
            if document_ids:
                legacy_clauses.append("document_id IN (" + ",".join("?" for _ in document_ids) + ")")
                legacy_params.extend(document_ids)
            if part_numbers:
                legacy_clauses.append("part_number COLLATE NOCASE IN (" +
                                      ",".join("?" for _ in part_numbers) + ")")
                legacy_params.extend(part_numbers)
            legacy_rows = db.rows(
                "SELECT id, source_path FROM legacy_records WHERE " + " OR ".join(legacy_clauses),
                legacy_params,
            ) if legacy_clauses else []
            legacy_record_ids = [row["id"] for row in legacy_rows]
            legacy_source_paths = sorted({row["source_path"] for row in legacy_rows if row["source_path"]})
            if document_ids or legacy_source_paths:
                ref_clauses = []
                ref_params = []
                if document_ids:
                    ref_clauses.append("document_id IN (" + ",".join("?" for _ in document_ids) + ")")
                    ref_params.extend(document_ids)
                if legacy_source_paths:
                    ref_clauses.append("(kind = 'path' AND value IN (" +
                                        ",".join("?" for _ in legacy_source_paths) + "))")
                    ref_params.extend(legacy_source_paths)
                db.execute("DELETE FROM legacy_refs WHERE " + " OR ".join(ref_clauses), ref_params)
            if legacy_record_ids:
                db.execute("DELETE FROM legacy_records WHERE id IN (" +
                           ",".join("?" for _ in legacy_record_ids) + ")", legacy_record_ids)

            if document_ids:
                dm = ",".join("?" for _ in document_ids)
                db.execute(f"DELETE FROM cp_members WHERE document_id IN ({dm})", document_ids)
                db.execute(f"DELETE FROM lifecycle_events WHERE document_id IN ({dm})", document_ids)
                # Draft/rejected/cancelled ECO associations are explicitly detached.
                db.execute(f"DELETE FROM co_items WHERE document_id IN ({dm})", document_ids)
                db.execute(f"UPDATE cp_groups SET standard_document_id = NULL WHERE standard_document_id IN ({dm})",
                           document_ids)
                for version in db.rows(
                    f"SELECT blob_sha, preview_sha, viewer_sha FROM versions WHERE document_id IN ({dm})",
                    document_ids,
                ):
                    content_shas.update(sha for sha in version.values() if sha)
                db.execute(f"UPDATE change_requests SET document_id = NULL WHERE document_id IN ({dm})",
                           document_ids)
                # Legacy import rows are item/document-specific source records, not
                # reusable history. Remove them rather than leaving detached rows behind.
                db.execute(f"DELETE FROM versions WHERE document_id IN ({dm})", document_ids)
                db.execute(f"DELETE FROM documents WHERE id IN ({dm})", document_ids)

            db.execute(f"DELETE FROM cp_item_members WHERE item_id IN ({marks})", params)
            db.execute(f"UPDATE cp_groups SET standard_item_id = NULL WHERE standard_item_id IN ({marks})", params)
            db.execute(f"DELETE FROM item_oem_refs WHERE item_id IN ({marks})", params)
            db.execute(f"DELETE FROM item_reuse_approvals WHERE item_id IN ({marks}) OR parent_item_id IN ({marks})",
                       (*params, *params))
            db.execute(f"UPDATE items SET standard_item_id = NULL WHERE standard_item_id IN ({marks})", params)
            db.execute(f"DELETE FROM bom_lines WHERE bom_id IN (SELECT id FROM boms WHERE item_id IN ({marks})) "
                       f"OR child_item_id IN ({marks})", (*params, *params))
            db.execute(f"DELETE FROM boms WHERE item_id IN ({marks})", params)
            for item in reversed(deleted_items):
                db.execute("DELETE FROM items WHERE id = ?", (item["id"],))

            # A draft BOM may have been deleted before its parent item. Its delete
            # event preserves the BOM id and part/revision label. Use that event to
            # remove the earlier audit history for that BOM as well. Bound deletion
            # by the bom_delete audit id so a later BOM that reuses the integer id
            # cannot lose its unrelated audit history.
            item_prefixes = tuple(f'{item["part_number"]} rev ' for item in deleted_items)
            historical_bom_deletes = []
            for entry in db.rows(
                "SELECT id, entity_id, details FROM audit "
                "WHERE entity = 'bom' AND action = 'bom_delete' AND entity_id IS NOT NULL"
            ):
                details = entry["details"] or ""
                if details.startswith(item_prefixes):
                    historical_bom_deletes.append((entry["entity_id"], entry["id"]))

            # Remove all direct audit records for deleted items, documents and BOMs
            # still present, plus the bounded history of previously deleted BOMs.
            for entity, entity_ids in (
                ("item", sorted(owned)),
                ("document", document_ids),
            ):
                if entity_ids:
                    audit_marks = ",".join("?" for _ in entity_ids)
                    db.execute(f"DELETE FROM audit WHERE entity = ? AND entity_id IN ({audit_marks})",
                               (entity, *entity_ids))
            for bom_id in owned_bom_ids:
                previous_generation_end = db.scalar(
                    "SELECT MAX(id) FROM audit WHERE entity = 'bom' AND entity_id = ? AND action = 'bom_delete'",
                    (bom_id,),
                ) or 0
                db.execute(
                    "DELETE FROM audit WHERE entity = 'bom' AND entity_id = ? AND id > ?",
                    (bom_id, previous_generation_end),
                )
            for historical_bom_id, deleted_audit_id in historical_bom_deletes:
                db.execute(
                    "DELETE FROM audit WHERE entity = 'bom' AND entity_id = ? AND id <= ?",
                    (historical_bom_id, deleted_audit_id),
                )

            # Remove audit entries in other entities whose details encode item/document references.
            item_add_prefixes = tuple(f'{item["part_number"]} rev ' for item in deleted_items)
            doc_add_prefixes = tuple(f'{doc["filename"]} rev ' for doc in deleted_documents)
            item_remove_details = {f'item {item["id"]}' for item in deleted_items}
            doc_remove_prefixes = tuple(f'document {doc["id"]} rev ' for doc in deleted_documents)
            item_id_strings = {str(item_id) for item_id in owned}
            indirect_audit_ids = []
            for entry in db.rows(
                "SELECT id, entity, action, details FROM audit WHERE "
                "(entity = 'deviation' AND action IN ('deviation_affected_add', 'deviation_affected_remove')) "
                "OR (entity = 'project' AND action IN ('project_link_item', 'project_unlink_item')) "
                "OR (entity = 'input' AND action IN ('input_link', 'input_unlink', "
                "'input_bulk_link', 'input_bulk_unlink'))"
            ):
                details = entry["details"] or ""
                matches = False
                if entry["entity"] == "deviation":
                    if entry["action"] == "deviation_affected_add":
                        matches = details.startswith(item_add_prefixes + doc_add_prefixes)
                    else:
                        matches = details in item_remove_details or details.startswith(doc_remove_prefixes)
                elif entry["entity"] == "project":
                    matches = details in item_id_strings
                elif entry["entity"] == "input":
                    # Input-link audit details use "items=[1, 2] projects=[...]".
                    marker = "items=["
                    start = details.find(marker)
                    end = details.find("]", start + len(marker)) if start >= 0 else -1
                    if end >= 0:
                        raw_ids = details[start + len(marker):end]
                        try:
                            referenced_ids = {int(value.strip()) for value in raw_ids.split(",") if value.strip()}
                        except ValueError:
                            referenced_ids = set()
                        matches = bool(referenced_ids & owned)
                if matches:
                    indirect_audit_ids.append(entry["id"])
            if indirect_audit_ids:
                audit_marks = ",".join("?" for _ in indirect_audit_ids)
                db.execute(f"DELETE FROM audit WHERE id IN ({audit_marks})", indirect_audit_ids)

            # Rebuild the surviving chain after intentional audit-record removal.
            auditchain.rebuild()

    # Remove only content no remaining record references. Shared blobs survive.
    if content_shas:
        with store.LOCK:
            still_used = filesafety.used_shas()
            for sha in content_shas - still_used:
                try:
                    store.path_for(sha).unlink(missing_ok=True)
                except (OSError, ValueError):
                    # Database deletion has succeeded; leave an orphan for normal store cleanup.
                    pass
    return {"ok": True, "deleted_items": [x["id"] for x in deleted_items],
            "deleted_documents": document_ids, "deletion_log_status": journal.status or "pending_recovery"}


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
    apisafety.guard(req, "items", iid, it["part_number"])
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
    with db.tx():
        db.update("items", iid, upd)
        apisafety.bump("items", iid, req.uid)
        db.audit(req.uid, "item_update", "item", iid, ",".join(upd))
    addins.registry.emit("item.updated", {"item_id": iid, "part_number": it["part_number"],
                                          "fields": sorted(k for k in upd if k != "updated_at"), "user_id": req.uid})
    return {"item": db.one("SELECT * FROM items WHERE id = ?", (iid,))}


# ------------------------------------------------------------------- BOMs --

@api("GET", "/api/boms")
def list_boms(req: Req):
    return {"boms": db.rows(
        "SELECT b.id, b.revision, b.state, b.source, b.updated_at, i.id AS item_id, i.part_number, i.description, "
        "(SELECT COUNT(*) FROM bom_lines WHERE bom_id = b.id) AS lines FROM boms b JOIN items i ON i.id = b.item_id "
        "ORDER BY b.updated_at DESC LIMIT 500")}


@api("DELETE", "/api/boms/{id:int}", role="engineer")
def delete_bom(req: Req):
    boms.delete(req.user, req.int_param("id"), req.has("approver"))
    return {"ok": True}


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
    apisafety.guard(req, "boms", bid, "this BOM")
    boms.set_lines(req.uid, bid, lines)
    with db.tx():
        apisafety.bump("boms", bid, req.uid)
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
    db.update("boms", bid, {"state": "released", "updated_at": db.now(), "released_at": db.now()})
    db.audit(req.uid, "bom_release", "bom", bid, f"{b['part_number']} rev {b['revision']}")
    if (erp.settings() or {}).get("auto_push_on_release", True):
        erp.enqueue("item", b["item_id"], req.uid, f"BOM {b['part_number']} rev {b['revision']}")
        if not erp.planned(b["item_id"]):      # otherwise the ERP gets the manufacturing BOM when that is released
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
    paged, after, limit = apisafety.cursor_args(req, 500)
    where, params = [], []
    if st:
        where.append("c.state = ?")
        params.append(st)
    if after is not None:
        where.append("c.id < ?")
        params.append(after)
    rows = db.rows("SELECT c.*, (SELECT COUNT(*) FROM co_items WHERE co_id = c.id) AS items FROM change_orders c" +
                   (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY c.id DESC LIMIT ?",
                   [*params, limit + 1 if paged else limit])
    for r in rows:
        r["requested_by_name"] = names.get(r["requested_by"], "?")
    if paged:
        rows, nxt = apisafety.next_cursor(rows, limit)
        return {"changes": rows, "next_cursor": nxt}
    return {"changes": rows}


@api("POST", "/api/changes", role="engineer")
def create_change(req: Req):
    co_id = changes.create(req.user, req.b("title") or "", req.b("reason") or "", req.b("description") or "",
                           req.b("priority") or "normal", req.b("document_ids") or [], req.b("approver_ids") or [],
                           fields=req.body if isinstance(req.body, dict) else {})
    return changes.detail(co_id, req.user)


@api("GET", "/api/changes/{id:int}")
def get_change(req: Req):
    return changes.detail(req.int_param("id"), req.user)


@api("PATCH", "/api/changes/{id:int}", role="engineer")
def patch_change(req: Req):
    cid = req.int_param("id")
    apisafety.guard(req, "change_orders", cid, changes.co_or_404(cid)["number"])
    changes.update(req.user, cid, req.body if isinstance(req.body, dict) else {})
    with db.tx():
        apisafety.bump("change_orders", cid, req.uid)
    return changes.detail(cid, req.user)


@api("POST", "/api/changes/{id:int}/items", role="engineer")
def add_change_item(req: Req):
    cid = req.int_param("id")
    for d in req.b("document_ids") or ([req.b("document_id")] if req.b("document_id") else []):
        changes.add_item(req.user, cid, int(d), req.b("action") or "release", req.b("replacement") or "")
    return changes.detail(cid, req.user)


@api("DELETE", "/api/changes/{id:int}/items/{doc:int}", role="engineer")
def remove_change_item(req: Req):
    changes.remove_item(req.user, req.int_param("id"), req.int_param("doc"))
    return changes.detail(req.int_param("id"), req.user)


@api("POST", "/api/changes/{id:int}/approvers", role="engineer")
def add_change_approver(req: Req):
    changes.add_approver(req.user, req.int_param("id"), int(req.b("user_id")))
    return changes.detail(req.int_param("id"), req.user)


@api("DELETE", "/api/changes/{id:int}/approvers/{uid:int}", role="engineer")
def remove_change_approver(req: Req):
    changes.remove_approver(req.user, req.int_param("id"), req.int_param("uid"))
    return changes.detail(req.int_param("id"), req.user)


@api("POST", "/api/changes/{id:int}/revise", role="engineer")
def revise_change_items(req: Req):
    n = changes.revise_items(req.user, req.int_param("id"))
    return {**changes.detail(req.int_param("id"), req.user), "revised": n}


@api("POST", "/api/changes/{id:int}/submit", role="engineer")
def submit_change(req: Req):
    changes.submit(req.user, req.int_param("id"))
    addins.registry.emit("change.submitted", {"change_id": req.int_param("id"), "user_id": req.uid})
    return changes.detail(req.int_param("id"), req.user)


@api("POST", "/api/changes/{id:int}/route", role="engineer")
def use_change_route(req: Req):
    used = changes.use_route(req.user, req.int_param("id"))
    return {**changes.detail(req.int_param("id"), req.user), "routes_used": used}


@api("POST", "/api/changes/{id:int}/decide", role="viewer")
def decide_change(req: Req):
    changes.decide(req.user, req.int_param("id"), req.b("decision") or "", req.b("comment") or "")
    co = changes.co_or_404(req.int_param("id"))
    if co["state"] == "approved":
        addins.registry.emit("eco.approved", {"change_id": co["id"], "number": co["number"], "user_id": req.uid})
    return changes.detail(req.int_param("id"), req.user)


@api("POST", "/api/changes/{id:int}/implement", role="approver")
def implement_change(req: Req):
    res = changes.implement(req.user, req.int_param("id"))
    addins.registry.emit("change.implemented", {"change_id": req.int_param("id"), "user_id": req.uid})
    erp.process_pending()
    return {**changes.detail(req.int_param("id"), req.user), **res}


@api("POST", "/api/changes/{id:int}/cancel", role="engineer")
def cancel_change(req: Req):
    changes.cancel(req.user, req.int_param("id"))
    return changes.detail(req.int_param("id"), req.user)


@api("POST", "/api/changes/{id:int}/comments", role="viewer")
def comment_change(req: Req):
    cid = req.int_param("id")
    changes.co_or_404(cid)
    text = (req.b("text") or "").strip()
    if not text:
        raise bad("Write a comment first.")
    db.insert("co_comments", {"co_id": cid, "user_id": req.uid, "text": text[:4000], "at": db.now()})
    return changes.detail(cid, req.user)


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


def _category_map(v) -> dict:
    """{CATEGORY: {item_group, hsn}} with empty entries left out; a wrong HSN code is refused with an example."""
    import re
    if not isinstance(v, dict):
        raise bad("The category mapping must list categories with their item group and HSN code.")
    out = {}
    for code, m in v.items():
        code = str(code).strip().upper()
        if not code or not isinstance(m, dict):
            continue
        group = " ".join(str(m.get("item_group") or "").split())[:140]
        hsn = str(m.get("hsn") or "").strip()
        if hsn and not re.match(r"^\d{4}(\d{2}){0,2}$", hsn):
            raise bad(f"The HSN code for {code} has 4, 6 or 8 digits, for example 84833000.")
        if group or hsn:
            out[code] = {"item_group": group, "hsn": hsn}
    return out


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
        if k in ("attach_pdfs", "nightly_reconcile"):
            v = as_bool(v)
        if k == "category_map":
            v = _category_map(v)
        if k == "bom_source" and v not in erp.BOM_SOURCES:
            raise bad("Choose which BOM the ERP builds from: the engineering BOM or the manufacturing BOM.")
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
    projects_made = 0
    for e in events:
        if e.kind == "sales_order":         # a booked sales order gets its project and folders (6.2)
            from .. import projects
            customer, _, due = (e.detail or "").partition("|")
            try:
                _p, made = projects.from_sales_order(None, e.code, customer, due.strip() or None)
            except ApiError as ex:
                db.audit(None, "erp_sales_order_refused", "project", None, f"{e.code}: {ex}")
                continue
            projects_made += made
            continue
        if e.kind in erp_inbound.ORDER_KINDS or e.kind in erp_inbound.PARTY_KINDS or e.kind == "price_list":
            try:
                queued += erp_inbound.apply(e)
            except Exception:       # one bad event must not lose the others; the ERP's own record is untouched
                log.exception("Could not take in the ERP event %s %s", e.kind, e.code)
            continue
        item = db.one("SELECT id FROM items WHERE part_number = ? COLLATE NOCASE", (e.code,))
        if not item:
            continue
        if e.kind in ("stock_changed", "item_changed"):
            erp.enqueue("refresh", item["id"], None, f"ERP: {e.kind}")
            queued += 1
        elif e.kind == "bom_cancelled":
            db.audit(None, "erp_bom_cancelled", "item", item["id"], e.detail or "BOM cancelled in the ERP")
    out = {"accepted": len(events), "queued": queued}
    if any(e.kind == "sales_order" for e in events):
        out["projects_created"] = projects_made
    return out


@api("GET", "/api/erp/jobs", role="viewer")
def erp_jobs(req: Req):
    st = req.q("status")
    rows = db.rows("SELECT * FROM erp_jobs" + (" WHERE status = ?" if st else "") + " ORDER BY id DESC LIMIT 300",
                   (st,) if st else ())
    for r in rows:
        if r["kind"] in ("item", "refresh"):
            it = db.one("SELECT part_number, description FROM items WHERE id = ?", (r["entity_id"],))
        elif r["kind"] == "attach":
            it = db.one("SELECT part_number, filename AS description FROM documents WHERE id = ?", (r["entity_id"],))
        else:
            table = "mboms" if r["kind"] == "mbom" else "boms"
            it = db.one(f"SELECT i.part_number, i.description, b.revision FROM {table} b JOIN items i ON i.id = b.item_id "
                        "WHERE b.id = ?", (r["entity_id"],))
        what = {"bom": "BOM", "mbom": "manufacturing BOM"}.get(r["kind"])
        r["label"] = (f"{it['part_number']} {it['description']}" + (f" ({what} rev {it['revision']})" if what else "")) \
            if it else f"#{r['entity_id']}"
    counts = {s: db.scalar("SELECT COUNT(*) FROM erp_jobs WHERE status = ?", (s,))
              for s in ("pending", "running", "sent", "failed", "dry_run", "skipped")}
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
    # The browser action map also contains scalar metadata (for example,
    # release_needs_reason and next_revision). The SolidWorks add-in deserializes
    # this field as Dictionary<string, ActionCheck>, so expose only ActionCheck
    # entries on the add-in endpoint.
    s["actions"] = {
        key: value for key, value in actions_for(d, req.user).items()
        if isinstance(value, dict) and "ok" in value and "why" in value
    }
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
