# SPDX-License-Identifier: AGPL-3.0-or-later
"""Machine builds and the installed base (specification 4.2, 6.12 and 7.8).

A machine build is one machine made for one customer order. It gets a serial number (``MX26-0042``: the product
family, the year and a running number, from the *Machine serial number* series) that goes on the nameplate, and its
**as-built BOM**: the released BOM of the machine at the moment it was recorded, every level, kept with the build so
it never changes when the design moves on. Deviations limited to that serial are shown with it, so later spares
match what was really built.

The list of builds is the **installed base**: which machines are at which customer, with which BOM. Builds are never
deleted, only withdrawn (with who and when), because spares depend on them.
"""
from __future__ import annotations

import json
import re
from datetime import date

from . import db
from .web import bad, conflict, not_found

SERIAL = re.compile(r"^[A-Z0-9][A-Z0-9\-/]{2,30}$")
KEEP = ("item_id", "part_number", "description", "qty", "uom", "item_uom", "find_number", "material", "make_buy",
        "revision", "level", "ext_qty", "parent", "has_children")


def _clean_text(body: dict, key: str, limit: int = 200) -> str:
    return " ".join(str(body.get(key) or "").split())[:limit]


def _date(value, label: str, required: bool = False) -> str | None:
    v = str(value or "").strip()
    if not v:
        if required:
            raise bad(f"Give the {label} as YYYY-MM-DD.")
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise bad(f"The {label} is not a date; write it as YYYY-MM-DD.") from None


def _row(b: dict) -> dict:
    return {**b, "lines": json.loads(b.pop("lines_json") or "[]")}


def build_or_404(build_id: int) -> dict:
    b = db.one("SELECT m.*, i.part_number, i.description FROM machine_builds m JOIN items i ON i.id = m.item_id "
               "WHERE m.id = ?", (build_id,))
    if not b:
        raise not_found("Machine not found")
    from . import access
    access.check_item(b["item_id"])
    return _row(b)


def build_by_serial(serial: str | None) -> dict:
    s = str(serial or "").strip().upper()
    if not s:
        raise bad("Give the machine's serial number, for example MX26-0042.")
    b = db.one("SELECT id FROM machine_builds WHERE serial = ?", (s,))
    if not b:
        raise not_found(f"There is no machine {s}. Check the number on the nameplate.")
    return build_or_404(b["id"])


def create(user: dict, body: dict) -> dict:
    from . import bomviews
    from .access import check_item
    item = None
    if body.get("item_id"):
        item = db.one("SELECT * FROM items WHERE id = ?", (int(body["item_id"]),))
    elif body.get("part_number"):
        item = db.one("SELECT * FROM items WHERE part_number = ?", (str(body["part_number"]).strip().upper(),))
    elif body.get("variant_id"):
        item = db.one("SELECT i.* FROM items i JOIN variants v ON v.item_id = i.id WHERE v.id = ?",
                      (int(body["variant_id"]),))
    if not item:
        raise bad("Choose the machine (its top assembly part number).")
    check_item(item["id"])
    variant = None
    if body.get("variant_id"):
        from .configurator import variant_or_404
        variant = variant_or_404(int(body["variant_id"]))
        if variant["item_id"] != item["id"]:
            raise bad(f"Variant {variant['number']} is a variant of another product.")
        if variant["withdrawn_at"]:
            raise conflict(f"Variant {variant['number']} was withdrawn.", "variant_withdrawn")
        v = {"bom_id": variant["bom_id"], "revision": variant["revision"], "rows": variant["lines"]}
    else:
        v = bomviews.view(item["id"], "released")
    if not v["bom_id"]:
        raise conflict(f"{item['part_number']} has no released BOM. Release its BOM first, so the machine's as-built "
                       "BOM is the one production built from.", "no_released_bom")
    built_on = _date(body.get("built_on"), "build date") or date.today().isoformat()
    commissioned = _date(body.get("commissioned_on"), "commissioning date")
    serial = str(body.get("serial") or "").strip().upper()
    if serial:
        if not SERIAL.match(serial):
            raise bad("A serial number has 3 to 31 letters, digits and dashes, for example MX26-0042.")
        if db.scalar("SELECT 1 FROM machine_builds WHERE serial = ?", (serial,)):
            raise conflict(f"Machine {serial} is already recorded.", "serial_taken")
    else:
        family = str(body.get("family") or item.get("family") or "").strip().upper()
        if not re.match(r"^[A-Z]{2}$", family):
            raise bad("Give the product family (two letters, for example MX) or type the serial number.")
        from .numbering import NumberingError, NumberingService
        try:
            serial = NumberingService().allocate("machine-build", {"family": family}, user["id"],
                                                 f"{item['part_number']} {item['description']}"[:120])["part_number"]
        except NumberingError as e:
            raise bad(str(e)) from None
    lines = [{k: r.get(k) for k in KEEP} for r in v["rows"]]
    now = db.now()
    with db.tx():
        bid = db.insert("machine_builds", {
            "serial": serial, "item_id": item["id"], "bom_id": v["bom_id"], "revision": v["revision"],
            "customer": _clean_text(body, "customer", 120), "site": _clean_text(body, "site", 120),
            "order_ref": _clean_text(body, "order_ref", 60), "built_on": built_on, "commissioned_on": commissioned,
            "note": _clean_text(body, "note", 1000), "lines_json": json.dumps(lines), "created_by": user["id"],
            "variant_id": variant["id"] if variant else None,
            "created_at": now, "updated_at": now})
        db.audit(user["id"], "machine_build", "machine", bid,
                 f"{serial}: {item['part_number']} rev {v['revision']}, {len(lines)} BOM rows"
                 + (f", variant {variant['number']}" if variant else ""))
    return summary(build_or_404(bid))


def update(user: dict, build_id: int, body: dict) -> dict:
    b = build_or_404(build_id)
    if b["deleted_at"]:
        raise conflict("This machine was withdrawn from the installed base.", "withdrawn")
    upd = {}
    for k, limit in (("customer", 120), ("site", 120), ("order_ref", 60), ("note", 1000)):
        if k in body:
            upd[k] = _clean_text(body, k, limit)
    if "commissioned_on" in body:
        upd["commissioned_on"] = _date(body["commissioned_on"], "commissioning date")
    if not upd:
        return summary(b)
    with db.tx():
        db.update("machine_builds", build_id, {**upd, "updated_at": db.now()})
        db.audit(user["id"], "machine_update", "machine", build_id, f"{b['serial']}: {', '.join(upd)}")
    return summary(build_or_404(build_id))


def withdraw(user: dict, build_id: int, reason: str) -> dict:
    """Take a machine out of the installed base (recorded by mistake, scrapped). Its record is kept."""
    b = build_or_404(build_id)
    reason = " ".join(str(reason or "").split())[:300]
    if not reason:
        raise bad("Say why the machine is taken out of the installed base.")
    if not b["deleted_at"]:
        with db.tx():
            db.update("machine_builds", build_id, {"deleted_at": db.now(), "deleted_by": user["id"]})
            db.audit(user["id"], "machine_withdraw", "machine", build_id, f"{b['serial']}: {reason}")
    return summary(build_or_404(build_id))


def deviations_for(b: dict) -> list[dict]:
    """Approved deviations limited to this machine's serial, on parts in its BOM."""
    items = {r["item_id"] for r in b["lines"]} | {b["item_id"]}
    out = []
    for d in db.rows("SELECT d.id, d.number, d.title, d.state, d.serials, a.item_id FROM deviations d "
                     "JOIN deviation_affected a ON a.deviation_id = d.id WHERE d.serials != '' AND "
                     "d.state NOT IN ('draft', 'submitted', 'rejected', 'cancelled')"):
        serials = {s.strip().upper() for s in re.split(r"[\s,;]+", d["serials"]) if s.strip()}
        if b["serial"] in serials and d["item_id"] in items:
            out.append({k: d[k] for k in ("id", "number", "title", "state", "item_id")})
    return out


def as_built_rows(b: dict) -> list[dict]:
    devs: dict[int, list] = {}
    for d in deviations_for(b):
        devs.setdefault(d["item_id"], []).append({"number": d["number"], "title": d["title"], "id": d["id"]})
    return [{**r, "deviations": devs.get(r["item_id"], []), "status": ""} for r in b["lines"]]


def summary(b: dict) -> dict:
    from .lifecycle import user_names
    names = user_names()
    return {k: b[k] for k in ("id", "serial", "item_id", "part_number", "description", "bom_id", "revision", "customer",
                              "site", "order_ref", "built_on", "commissioned_on", "note", "created_at",
                              "deleted_at")} | {
        "created_by_name": names.get(b["created_by"]), "rows": len(b["lines"]), "variant_id": b.get("variant_id"),
        "variant": db.scalar("SELECT number FROM variants WHERE id = ?", (b["variant_id"],)) if b.get("variant_id") else None,
        "deviations": [d["number"] for d in deviations_for(b)]}


def listing(q: str = "", item_id: int | None = None, include_withdrawn: bool = False) -> list[dict]:
    from .access import scope
    where, params = [], []
    if not include_withdrawn:
        where.append("m.deleted_at IS NULL")
    if item_id:
        where.append("m.item_id = ?")
        params.append(item_id)
    if q:
        where.append("(m.serial LIKE ? OR m.customer LIKE ? OR m.site LIKE ? OR m.order_ref LIKE ? OR "
                     "i.part_number LIKE ?)")
        params += [f"%{q}%"] * 5
    rows = db.rows("SELECT m.*, i.part_number, i.description FROM machine_builds m JOIN items i ON i.id = m.item_id"
                   + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY m.built_on DESC, m.id DESC "
                   "LIMIT 500", params)
    sc = scope()
    out = []
    for r in rows:
        if not sc.can_see_item(r["item_id"]):
            continue
        out.append(summary(_row(r)))
    return out
