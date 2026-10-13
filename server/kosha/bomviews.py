# SPDX-License-Identifier: AGPL-3.0-or-later
"""BOM views and cost roll-up (specification 6.10 and 6.12).

One BOM screen with a view selector:

* **Latest released** - what production should build today: each level is the newest released BOM.
* **In work** - the released structure with the pending changes of BOMs still in work, line by line: added,
  quantity changed, removed.
* **As of a date** - each level as it was released on that day (BOMs that were released later are left out).
* **As built** - the BOM of one delivered machine, by serial number, as recorded when it was built
  (``kosha.machines``), with the deviations that applied to that machine.

The cost roll-up adds up any of these views: a part's own cost is its ERP rate when there is one; parts without one
(usually still in work) get an **estimate** from their weight and the per-kg material and process rates set by the
admin, clearly marked. Assemblies made in-house are the sum of their lines. *What-if* compares two views (two
revisions, two dates, two machines) and lists the biggest cost differences first. Costs stay owned by the ERP; Kosha
only reads and adds them up.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date

from . import boms, db
from .web import bad, not_found

VIEWS = {"released": "Latest released", "in_work": "In work", "as_of": "As of a date", "as_built": "As built"}
RATES_KEY = "cost_rates"
DEFAULT_RATES = {"currency": "INR", "material_rates": {}, "process_rate_per_kg": 0}
MAX_DEPTH = 12


# ------------------------------------------------------------------ which BOM --

def _released(item_id: int, day: str | None = None) -> dict | None:
    """The item's newest released BOM, or the newest released by the end of ``day``."""
    if day:
        return db.one("SELECT * FROM boms WHERE item_id = ? AND state = 'released' AND "
                      "substr(COALESCE(released_at, updated_at), 1, 10) <= ? "
                      "ORDER BY COALESCE(released_at, updated_at) DESC, id DESC LIMIT 1", (item_id, day))
    return db.one("SELECT * FROM boms WHERE item_id = ? AND state = 'released' "
                  "ORDER BY COALESCE(released_at, updated_at) DESC, id DESC LIMIT 1", (item_id,))


def _working(item_id: int) -> dict | None:
    return db.one("SELECT * FROM boms WHERE item_id = ? AND state != 'released' ORDER BY updated_at DESC, id DESC "
                  "LIMIT 1", (item_id,))


def _day(value) -> str:
    v = str(value or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        raise bad("Give the date as YYYY-MM-DD, for example 2025-03-01.")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise bad(f"{v} is not a date.") from None
    return v


def _walk(root: dict, pick, mark=None) -> list[dict]:
    """Indented rows of ``root``; ``pick(item_id)`` chooses each child's BOM, ``mark(bom, rows)`` may add statuses."""
    out: list[dict] = []

    def walk(bom: dict, level: int, mult: float, path: tuple, parent_part: str):
        rows = boms.lines(bom["id"])
        rows = mark(bom, rows) if mark else rows
        for ln in rows:
            child = pick(ln["item_id"]) if ln.get("status") != "removed" else None
            qty = ln["qty"] if ln.get("status") != "removed" else 0
            row = {**ln, "level": level, "ext_qty": round(qty * mult, 6), "parent": parent_part,
                   "path": "/".join(map(str, path + (ln["item_id"],))), "bom_id": bom["id"],
                   "bom_revision": bom["revision"], "has_children": bool(child),
                   "child_bom_id": child["id"] if child else None, "cycle": ln["item_id"] in path}
            out.append(row)
            if child and level < MAX_DEPTH and ln["item_id"] not in path:
                walk(child, level + 1, mult * qty, path + (ln["item_id"],), ln["part_number"])

    root_item = db.one("SELECT part_number FROM items WHERE id = ?", (root["item_id"],))
    walk(root, 1, 1.0, (root["item_id"],), root_item["part_number"] if root_item else "")
    return out


def _pending(bom: dict, rows: list[dict]) -> list[dict]:
    """Mark a BOM in work against the released one of the same item: added, changed, removed lines."""
    if bom["state"] == "released":
        return [{**r, "status": ""} for r in rows]
    base = _released(bom["item_id"])
    if not base:
        return [{**r, "status": "added"} for r in rows]
    before = {r["item_id"]: r for r in boms.lines(base["id"])}
    out = []
    for r in rows:
        old = before.pop(r["item_id"], None)
        out.append({**r, "status": "added" if not old else "changed" if abs(old["qty"] - r["qty"]) > 1e-9 else "",
                    "was_qty": old["qty"] if old else None})
    out += [{**r, "status": "removed", "was_qty": r["qty"]} for r in before.values()]
    return out


def view(item_id: int, kind: str = "released", day: str | None = None, serial: str | None = None) -> dict:
    """The rows of one view of an item's BOM."""
    item = db.one("SELECT * FROM items WHERE id = ?", (item_id,))
    if not item:
        raise not_found("Part not found")
    from . import access
    access.check_item(item_id)
    if kind not in VIEWS:
        raise bad(f"Choose a view: {', '.join(VIEWS)}.")
    head = {"item_id": item_id, "part_number": item["part_number"], "description": item["description"],
            "view": kind, "view_label": VIEWS[kind]}
    if kind == "as_built":
        from . import machines
        b = machines.build_by_serial(serial)
        if b["item_id"] != item_id:
            raise bad(f"Machine {b['serial']} is a {b['part_number']}, not a {item['part_number']}.")
        return {**head, "serial": b["serial"], "revision": b["revision"], "build": machines.summary(b),
                "rows": machines.as_built_rows(b), "empty_why": "" if b["lines"] else "Nothing was recorded for this "
                "machine's BOM when it was built."}
    if kind == "released":
        root = _released(item_id)
        why = "This part has no released BOM yet."
        pick = _released
        mark = None
    elif kind == "as_of":
        day = _day(day)
        root = _released(item_id, day)
        why = f"No BOM of this part had been released by {day}."
        pick = lambda i: _released(i, day)          # noqa: E731
        mark = None
        head["day"] = day
    else:
        root = _working(item_id) or _released(item_id)
        why = "This part has no BOM yet."
        pick = lambda i: _working(i) or _released(i)    # noqa: E731
        mark = _pending
    if not root:
        return {**head, "revision": None, "bom_id": None, "rows": [], "empty_why": why}
    rows = _walk(root, pick, mark)
    if kind == "in_work":
        head["changes"] = sum(1 for r in rows if r.get("status"))
    return {**head, "revision": root["revision"], "bom_id": root["id"], "bom_state": root["state"],
            "released_at": root.get("released_at"), "rows": rows, "empty_why": "" if rows else "This BOM is empty."}


# ------------------------------------------------------------------------ cost --

def rates() -> dict:
    return {**DEFAULT_RATES, **(db.get_setting(RATES_KEY) or {})}


def save_rates(user_id: int, body: dict) -> dict:
    cur = rates()
    if "currency" in body:
        c = str(body["currency"] or "").strip().upper()
        if not re.match(r"^[A-Z]{3}$", c):
            raise bad("The currency is a three-letter code, for example INR or EUR.")
        cur["currency"] = c
    if "material_rates" in body:
        m = body["material_rates"]
        if not isinstance(m, dict):
            raise bad("List the material rates as material and rate per kg.")
        clean = {}
        for k, v in m.items():
            name = " ".join(str(k).split()).upper()[:40]
            try:
                rate = float(v)
            except (TypeError, ValueError):
                raise bad(f"The rate of {name} is not a number.") from None
            if name and not 0 <= rate <= 1e7:
                raise bad(f"The rate of {name} must be between 0 and 10,000,000 per kg.")
            if name:
                clean[name] = rate
        cur["material_rates"] = clean
    if "process_rate_per_kg" in body:
        try:
            p = float(body["process_rate_per_kg"] or 0)
        except (TypeError, ValueError):
            raise bad("The process rate is a number per kg.") from None
        if not 0 <= p <= 1e7:
            raise bad("The process rate must be between 0 and 10,000,000 per kg.")
        cur["process_rate_per_kg"] = p
    db.set_setting(RATES_KEY, cur)
    db.audit(user_id, "cost_rates", "setting", None, f"{len(cur['material_rates'])} material rate(s)")
    return cur


def _unit(row: dict, r: dict) -> tuple[float | None, str, str]:
    """(unit cost, source, how) of one part on its own."""
    item = db.one("SELECT erp_cost, weight_kg, material FROM items WHERE id = ?", (row["item_id"],)) or {}
    if item.get("erp_cost") is not None:
        return float(item["erp_cost"]), "erp", "ERP rate"
    w = item.get("weight_kg")
    mat = (item.get("material") or "").strip().upper()
    rate = r["material_rates"].get(mat)
    if w and rate is not None:
        per_kg = rate + float(r["process_rate_per_kg"] or 0)
        return round(w * per_kg, 2), "estimate", f"Estimate: {w} kg × {per_kg:g} per kg ({mat})"
    if not w:
        return None, "missing", "No ERP rate and no weight"
    return None, "missing", f"No ERP rate and no rate for material {mat or '(none)'}"


def cost(v: dict) -> dict:
    """Add a cost to every row of a view and roll it up; top cost drivers first."""
    r = rates()
    rows = v["rows"]
    # a row's own cost counts unless it is an in-house assembly whose lines are listed below it
    bought_at = None        # level of a bought assembly: what is below it is the supplier's, not counted again
    for row in rows:
        if bought_at is not None and row["level"] > bought_at:
            row.update({"unit_cost": None, "cost_source": "in_bought", "ext_cost": None,
                        "cost_how": "Part of a bought assembly (its price includes this)"})
            continue
        bought_at = None
        unit, source, how = _unit(row, r)
        rolled = row["has_children"] and row.get("make_buy") != "buy"
        if row["has_children"] and not rolled:
            bought_at = row["level"]
        row.update({"unit_cost": unit, "cost_source": "rolled_up" if rolled else source, "cost_how":
                    "Sum of its lines" if rolled else how})
        row["ext_cost"] = None if rolled or unit is None or row.get("status") == "removed" \
            else round(unit * row["ext_qty"], 2)
    # assemblies: the sum of what is below them (per one assembly)
    for i, row in enumerate(rows):
        if row["cost_source"] != "rolled_up":
            continue
        total = 0.0
        for below in rows[i + 1:]:
            if below["level"] <= row["level"]:
                break
            total += below["ext_cost"] or 0
        row["ext_cost_below"] = round(total, 2)
        row["unit_cost"] = round(total / row["ext_qty"], 2) if row["ext_qty"] else None
    counted = [x for x in rows if x["ext_cost"] is not None]
    total = round(sum(x["ext_cost"] for x in counted), 2)
    missing = [x for x in rows if x["cost_source"] == "missing" and x.get("status") != "removed"]
    estimated = round(sum(x["ext_cost"] for x in counted if x["cost_source"] == "estimate"), 2)
    by_part: dict[str, dict] = {}
    for x in counted:
        p = by_part.setdefault(x["part_number"], {"part_number": x["part_number"], "description": x["description"],
                                                  "qty": 0.0, "cost": 0.0, "source": x["cost_source"]})
        p["qty"] = round(p["qty"] + x["ext_qty"], 6)
        p["cost"] = round(p["cost"] + x["ext_cost"], 2)
    drivers = sorted(by_part.values(), key=lambda p: -p["cost"])[:10]
    for d in drivers:
        d["share"] = round(100 * d["cost"] / total, 1) if total else 0
    return {**v, "currency": r["currency"], "total": total, "estimated": estimated, "missing": len(missing),
            "missing_parts": sorted({x["part_number"] for x in missing})[:50], "drivers": drivers}


def what_if(a: dict, b: dict) -> dict:
    """Where the cost difference between two costed views comes from, biggest first."""
    def parts(v):
        out: dict[str, dict] = {}
        for x in v["rows"]:
            if x["ext_cost"] is None and x["cost_source"] == "rolled_up":
                continue
            p = out.setdefault(x["part_number"], {"description": x["description"], "qty": 0.0, "cost": 0.0,
                                                  "missing": False})
            p["qty"] = round(p["qty"] + (x["ext_qty"] if x.get("status") != "removed" else 0), 6)
            p["cost"] = round(p["cost"] + (x["ext_cost"] or 0), 2)
            p["missing"] = p["missing"] or x["cost_source"] == "missing"
        return out
    pa, pb = parts(a), parts(b)
    diff = []
    for pn in set(pa) | set(pb):
        x, y = pa.get(pn), pb.get(pn)
        d = round((y["cost"] if y else 0) - (x["cost"] if x else 0), 2)
        dq = round((y["qty"] if y else 0) - (x["qty"] if x else 0), 6)
        if abs(d) < 0.005 and abs(dq) < 1e-9:
            continue
        diff.append({"part_number": pn, "description": (y or x)["description"],
                     "change": "added" if not x else "removed" if not y or not y["qty"] else "changed",
                     "from_qty": x["qty"] if x else 0, "to_qty": y["qty"] if y else 0,
                     "from_cost": x["cost"] if x else 0, "to_cost": y["cost"] if y else 0, "difference": d,
                     "missing_cost": bool((x and x["missing"]) or (y and y["missing"]))})
    diff.sort(key=lambda r: (-abs(r["difference"]), r["part_number"]))
    label = lambda v: f"{v['part_number']} " + (v.get("serial") or (f"rev {v['revision']}" if v.get("revision") else "")) \
        + (f" on {v['day']}" if v.get("day") else "") + f" ({v['view_label'].lower()})"    # noqa: E731
    return {"a": {"label": label(a), "total": a["total"], "missing": a["missing"]},
            "b": {"label": label(b), "total": b["total"], "missing": b["missing"]},
            "difference": round(b["total"] - a["total"], 2), "currency": a["currency"], "lines": diff}


def export_cost(v: dict) -> tuple[bytes, str, str]:
    """The costed view as an Excel workbook (CSV when openpyxl is not installed), for quotations."""
    head = ["Level", "Part number", "Description", "Qty", "Total qty", "Unit cost", "Total cost", "Source", "How"]
    src = {"erp": "ERP", "estimate": "Estimate", "rolled_up": "Rolled up", "missing": "Missing",
           "in_bought": "In bought assembly"}
    data = [[r["level"], r["part_number"], r["description"], r["qty"], r["ext_qty"], r["unit_cost"],
             r["ext_cost"] if r["ext_cost"] is not None else r.get("ext_cost_below"), src[r["cost_source"]],
             r["cost_how"]] for r in v["rows"] if r.get("status") != "removed"]
    base = f"Cost_{v['part_number']}_" + (v.get("serial") or f"rev{v.get('revision') or ''}") \
        + (f"_{v['day']}" if v.get("day") else "")
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(head)
        w.writerows(data)
        w.writerow(["", "", "Total", "", "", "", v["total"], v["currency"], f"{v['missing']} part(s) without a cost"])
        return buf.getvalue().encode("utf-8-sig"), base + ".csv", "text/csv"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cost"
    ws.append([f"{v['part_number']} {v['description']}", v["view_label"], f"Currency: {v['currency']}"])
    ws.append([])
    ws.append(head)
    for c in ws[3]:
        c.font = Font(bold=True)
    for r in data:
        ws.append(r)
    ws.append([])
    ws.append(["", "", "Total", "", "", "", v["total"], v["currency"],
               f"{v['estimated']} estimated; {v['missing']} part(s) without a cost"])
    for col, width in zip("ABCDEFGHI", (7, 20, 40, 8, 10, 12, 12, 11, 40)):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), base + ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
