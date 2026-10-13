# SPDX-License-Identifier: AGPL-3.0-or-later
"""BOMs: lines added and removed, quantity and unit changes, and part-number swaps at the same position.

Builds on ``boms.compare`` (which keys lines by part number) and adds what a change log needs:
a part replaced by another at the same find number reads as a swap, not as one line removed and
another added.
"""
from __future__ import annotations

from .core import Change, Comparer, Side


def _merge(lines: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for ln in lines or []:
        pn = str(ln.get("part_number") or "").strip().upper()
        if not pn:
            continue
        if pn in out:
            out[pn]["qty"] = float(out[pn]["qty"]) + float(ln.get("qty") or 0)
        else:
            out[pn] = {"part_number": pn, "description": ln.get("description") or "", "qty": float(ln.get("qty") or 0),
                       "uom": (ln.get("uom") or ln.get("item_uom") or "").strip(),
                       "find_number": str(ln.get("find_number") or "").strip()}
    return out


def compare_lines(old: list[dict], new: list[dict]) -> list[Change]:
    """Pure function over BOM lines (dicts with part_number, description, qty, uom, find_number)."""
    a, b = _merge(old), _merge(new)
    removed = [a[k] for k in a if k not in b]
    added = [b[k] for k in b if k not in a]
    out: list[Change] = []
    # swaps: one part out, another in, at the same find number
    by_find = {}
    for r in removed:
        if r["find_number"]:
            by_find.setdefault(r["find_number"], []).append(r)
    swapped_new = set()
    swapped_old = set()
    for n in added:
        cands = [r for r in by_find.get(n["find_number"], []) if r["part_number"] not in swapped_old] if n["find_number"] else []
        if cands:
            r = cands[0]
            swapped_old.add(r["part_number"])
            swapped_new.add(n["part_number"])
            out.append(Change("bom_swap", "BOM", r["part_number"], n["part_number"], "major", None,
                              {"find_number": n["find_number"], "from_qty": r["qty"], "to_qty": n["qty"],
                               "from_description": r["description"], "description": n["description"]}))
    for n in added:
        if n["part_number"] not in swapped_new:
            out.append(Change("bom_added", "BOM", None, n["part_number"], "major", None,
                              {"qty": n["qty"], "description": n["description"], "find_number": n["find_number"]}))
    for r in removed:
        if r["part_number"] not in swapped_old:
            out.append(Change("bom_removed", "BOM", r["part_number"], None, "major", None,
                              {"qty": r["qty"], "description": r["description"], "find_number": r["find_number"]}))
    for k in b:
        if k not in a:
            continue
        x, y = a[k], b[k]
        if abs(x["qty"] - y["qty"]) > 1e-9:
            out.append(Change("bom_qty", "BOM", x["qty"], y["qty"], "major", None,
                              {"part_number": k, "description": y["description"]}))
        if x["uom"] and y["uom"] and x["uom"].upper() != y["uom"].upper():
            out.append(Change("bom_uom", "BOM", x["uom"], y["uom"], "minor", None, {"part_number": k}))
        if x["find_number"] and y["find_number"] and x["find_number"] != y["find_number"]:
            out.append(Change("bom_find", "BOM", x["find_number"], y["find_number"], "info", None, {"part_number": k}))
    return out


class BomComparer(Comparer):
    name = "bom"
    label = "BOM comparison"
    exts = ("*",)

    def applies(self, a: Side, b: Side) -> bool:
        if a.bom_lines is None or b.bom_lines is None:
            return False
        return not (a.bom_id and a.bom_id == b.bom_id)

    def compare(self, a: Side, b: Side) -> list[Change]:
        return compare_lines(a.bom_lines or [], b.bom_lines or [])
