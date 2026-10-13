# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the ERP tells Kosha (specification 12.1 and 12.3): orders, customers, suppliers and price lists.

* A **purchase order, RFQ or subcontracting order** for parts is kept with the revision of each part that was
  released when it came in, so Kosha knows which revision belongs to which order. Approvers are told, and a buyer
  makes the vendor package for it in one click (ERP sync › Orders); the package is an ordinary transmittal with a
  share link that ends, carries the supplier's name as a watermark and can be withdrawn.
* **Customers and suppliers** are kept by the names the ERP uses, for pickers, customer restrictions, projects and
  vendor packages. The ERP is their master; Kosha never changes them.
* A **price list** change makes Kosha read stock and cost again for the parts it has sent to the ERP.

Only these records are written. Nothing the ERP says can change a part, a file or a BOM in Kosha.
"""
from __future__ import annotations

from . import db, erp, lifecycle, notify_outbox
from .web import bad, conflict, not_found

ORDER_KINDS = {"purchase_order": "Purchase order", "rfq": "Request for quotation",
               "subcontracting_order": "Subcontracting order"}
PARTY_KINDS = ("customer", "supplier")


def _clean(text: str, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


def record_order(kind: str, number: str, detail: str) -> int:
    """Keep (or update) an order from the ERP. detail: "supplier|code,code,...|cancelled" (the last part optional)."""
    parts = (detail or "").split("|")
    party = _clean(parts[0], 140)
    codes = [c for c in dict.fromkeys(_clean(x, 140) for x in (parts[1] if len(parts) > 1 else "").split(",")) if c]
    status = "cancelled" if len(parts) > 2 and parts[2].strip().lower() in ("cancelled", "canceled", "closed") else "open"
    number = _clean(number, 140)
    now = db.now()
    with db.tx():
        row = db.one("SELECT * FROM erp_orders WHERE kind = ? AND number = ?", (kind, number))
        if row:
            db.update("erp_orders", row["id"], {"party": party or row["party"], "status": status, "updated_at": now})
            oid = row["id"]
            known = {r["code"].upper() for r in db.rows("SELECT code FROM erp_order_lines WHERE order_id = ?", (oid,))}
        else:
            oid = db.insert("erp_orders", {"kind": kind, "number": number, "party": party, "status": status,
                                           "received_at": now, "updated_at": now})
            known = set()
        for code in codes[:200]:
            if code.upper() in known:
                continue        # the revision recorded when the order first came in stays
            item = db.one("SELECT id, revision, state FROM items WHERE part_number = ? COLLATE NOCASE", (code,))
            rev = item["revision"] if item and item["state"] in lifecycle.LIVE_STATES else ""
            db.insert("erp_order_lines", {"order_id": oid, "code": code, "item_id": item["id"] if item else None,
                                          "revision": rev})
        db.audit(None, "erp_order", "erp_order", oid, f"{ORDER_KINDS[kind]} {number}" + (f" from {party}" if party else "")
                 + (" cancelled" if status == "cancelled" else ""))
    if not row and status == "open":
        approvers = [u["id"] for u in db.rows("SELECT id FROM users WHERE active = 1 AND role IN ('approver', 'admin')")]
        notify_outbox.enqueue(approvers, f"{ORDER_KINDS[kind]} {number} needs drawings",
                              f"{party or 'A supplier'} needs the released drawings of {len(codes)} part(s). Make the "
                              "vendor package from ERP sync › Orders.", "#/erp?tab=orders", "general",
                              key=f"erp-order:{kind}:{number}")
    return oid


def record_party(kind: str, name: str, detail: str) -> None:
    name = _clean(name, 140)
    active = 0 if (detail or "").strip().lower() in ("disabled", "inactive") else 1
    with db.tx():
        row = db.one("SELECT id FROM erp_parties WHERE kind = ? AND name = ?", (kind, name))
        if row:
            db.update("erp_parties", row["id"], {"active": active, "updated_at": db.now()})
        else:
            db.insert("erp_parties", {"kind": kind, "name": name, "active": active, "updated_at": db.now()})


def price_list_changed(name: str) -> int:
    """Read stock and cost again for every part Kosha has sent to the ERP. Returns how many were queued."""
    n = 0
    for it in db.rows("SELECT id FROM items WHERE erp_synced_at IS NOT NULL ORDER BY id"):
        erp.enqueue("refresh", it["id"], None, f"ERP: price list {_clean(name, 60)}")
        n += 1
    return n


def apply(event) -> int:
    """One event of the kinds above. Returns the number of jobs queued (refreshes)."""
    if event.kind in ORDER_KINDS:
        record_order(event.kind, event.code, event.detail)
        return 0
    if event.kind in PARTY_KINDS:
        record_party(event.kind, event.code, event.detail)
        return 0
    if event.kind == "price_list":
        return price_list_changed(event.code)
    return 0


# ----------------------------------------------------------------- screens --

def _lines(order_id: int) -> list[dict]:
    out = []
    for ln in db.rows("SELECT * FROM erp_order_lines WHERE order_id = ? ORDER BY id", (order_id,)):
        item = db.one("SELECT id, part_number, description, revision, state FROM items WHERE id = ?",
                      (ln["item_id"],)) if ln["item_id"] else None
        if not item:
            note = "Not in Kosha"
        elif not ln["revision"]:
            note = "Not released when the order came in"
        elif item["revision"] != ln["revision"] and item["state"] in lifecycle.LIVE_STATES:
            note = f"Rev {item['revision']} has been released since"
        else:
            note = ""
        out.append({"code": ln["code"], "item_id": ln["item_id"], "revision": ln["revision"],
                    "description": item["description"] if item else "", "current_revision": item["revision"] if item else "",
                    "note": note})
    return out


def orders(status: str = "") -> list[dict]:
    rows = db.rows("SELECT * FROM erp_orders" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC LIMIT 300",
                   (status,) if status else ())
    out = []
    for r in rows:
        t = db.one("SELECT number FROM transmittals WHERE id = ?", (r["transmittal_id"],)) if r["transmittal_id"] else None
        out.append({**r, "kind_label": ORDER_KINDS.get(r["kind"], r["kind"]), "lines": _lines(r["id"]),
                    "transmittal_number": t["number"] if t else ""})
    return out


def parties(kind: str, q: str = "") -> list[dict]:
    if kind not in PARTY_KINDS:
        raise bad("Choose customers or suppliers.")
    sql = "SELECT name, active, updated_at FROM erp_parties WHERE kind = ?"
    args: list = [kind]
    if q:
        sql += " AND name LIKE ?"
        args.append(f"%{q}%")
    return db.rows(sql + " ORDER BY active DESC, name LIMIT 200", tuple(args))


def make_package(user: dict, order_id: int, days: int | None = None) -> dict:
    """The vendor package for an order: the released files of the order's parts, as released when it came in."""
    from . import transmittals
    o = db.one("SELECT * FROM erp_orders WHERE id = ?", (order_id,))
    if not o:
        raise not_found("Order not found")
    if o["status"] != "open":
        raise conflict("This order was cancelled in the ERP, so no package is sent.", "cancelled")
    ids = []
    for ln in _lines(order_id):
        if not ln["item_id"] or not ln["revision"]:
            continue
        doc = db.one("SELECT id FROM documents WHERE item_id = ? AND revision = ? AND deleted = 0 AND state IN "
                     "('released', 'spares_only') ORDER BY CASE WHEN ext = 'pdf' THEN 0 ELSE 1 END, id LIMIT 1",
                     (ln["item_id"], ln["revision"]))
        if doc:
            ids.append(doc["id"])
    if not ids:
        raise conflict("None of the order's parts has a released drawing in Kosha, so there is nothing to send.",
                       "nothing_released")
    t = transmittals.create(user, {"kind": "vendor_package", "document_ids": ids, "include_related": True,
                                   "recipient": o["party"] or "Supplier", "order_ref": o["number"], "days": days,
                                   "purpose": f"{ORDER_KINDS.get(o['kind'], o['kind'])} {o['number']}"})
    with db.tx():
        db.update("erp_orders", order_id, {"transmittal_id": t["id"], "updated_at": db.now()})
    return t
