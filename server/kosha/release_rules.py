# SPDX-License-Identifier: AGPL-3.0-or-later
"""The checks before a file moves on in its lifecycle (specification 7.1 to 7.3), each answered in plain words.

* **Before review:** part number and description filled; for a part made in-house its material; the fields its
  category asks for (``required`` in the pack); for an assembly, every child released (or spares only), or waiting
  in review or in the same change order.
* **Before release:** the four-eyes rule (the approver is not the person who made the last change). A small company
  can limit the rule to some categories, or allow approving one's own work with a written reason, kept in the
  audit log. Open duplicate warnings must be decided, or accepted with a reason.
* **Spares only:** a replacement part for new machines is named.
* **Obsolete:** not used in any released assembly.

All problems are reported together, so the engineer fixes them in one go.
"""
from __future__ import annotations

from . import db
from .web import ApiError

FIELD_LABEL = {"description": "description", "material": "material", "weight_kg": "weight", "uom": "unit",
               "make_buy": "make or buy", "family": "product family", "customer": "customer", "hsn": "HSN code"}
LIVE = ("released", "spares_only")


def refuse(problems: list[str], what: str) -> None:
    if problems:
        raise ApiError(409, f"{what}: " + " ".join(f"({i}) {p}" for i, p in enumerate(problems, 1)), "checks_failed",
                       {"problems": problems})


def category_of(part_number: str) -> dict | None:
    """The pack category whose code starts the part number (SHF-000123 → SHF), if packs define one."""
    code = (part_number or "").split("-", 1)[0].upper()
    if not code:
        return None
    try:
        from .packs import PackConfig
        for r in PackConfig.current().records("category"):
            if r.value.get("code", "").upper() == code:
                return r.value
    except Exception:       # a broken pack must not stop engineering work; Admin › Packs shows the problem
        return None
    return None


def review_problems(d: dict, co_id: int | None = None) -> list[str]:
    out: list[str] = []
    item = db.one("SELECT * FROM items WHERE id = ?", (d["item_id"],)) if d.get("item_id") else None
    if d["doc_type"] in ("part", "assembly", "model", "drawing") and not item:
        out.append("Give the file its part number first (Properties).")
    if item:
        if not (item["description"] or "").strip():
            out.append(f"{item['part_number']} has no description.")
        cat = category_of(item["part_number"])
        needed = list((cat or {}).get("required") or [])
        if item["make_buy"] == "make" and item["item_type"] != "assembly" and d["doc_type"] != "assembly" \
                and "material" not in needed:
            needed.append("material")
        for f in needed:
            if f == "description":
                continue
            v = item.get(f)
            if v is None or (isinstance(v, str) and not v.strip()):
                why = f" (category {cat['code']})" if cat and f in (cat.get("required") or []) else ""
                out.append(f"{item['part_number']} needs its {FIELD_LABEL.get(f, f)}{why}.")
        if d["doc_type"] == "assembly" or item["item_type"] == "assembly":
            out += _children_problems(item, d["revision"], co_id)
    return out


def _children_problems(item: dict, revision: str, co_id: int | None) -> list[str]:
    bom = db.one("SELECT id FROM boms WHERE item_id = ? AND revision = ?", (item["id"], revision)) or \
        db.one("SELECT id FROM boms WHERE item_id = ? ORDER BY id DESC LIMIT 1", (item["id"],))
    if not bom:
        return []
    in_co: set[int] = set()
    if co_id:
        in_co = {r["item_id"] for r in db.rows("SELECT d.item_id FROM co_items ci JOIN documents d ON d.id = ci.document_id "
                                               "WHERE ci.co_id = ? AND d.item_id IS NOT NULL", (co_id,))}
    waiting = []
    for c in db.rows("SELECT DISTINCT i.id, i.part_number, i.state FROM bom_lines bl JOIN items i ON i.id = bl.child_item_id "
                     "WHERE bl.bom_id = ? ORDER BY i.part_number", (bom["id"],)):
        if c["state"] in LIVE or c["id"] in in_co:
            continue
        if db.scalar("SELECT 1 FROM documents WHERE item_id = ? AND deleted = 0 AND state IN ('in_review', 'released', "
                     "'spares_only')", (c["id"],)):
            continue
        waiting.append(c["part_number"])
    if not waiting:
        return []
    names = ", ".join(waiting[:8]) + (f" and {len(waiting) - 8} more" if len(waiting) > 8 else "")
    return [f"These parts of the assembly are not released yet: {names}. Send them for approval too, or release them "
            "first."]


def four_eyes_applies(item: dict | None) -> bool:
    cats = [c.upper() for c in (db.get_setting("four_eyes_categories") or [])]
    if not cats:
        return True
    cat = (item["part_number"].split("-", 1)[0].upper() if item else "")
    return cat in cats


def release_problems(user: dict, d: dict, reason: str = "") -> list[str]:
    out: list[str] = []
    item = db.one("SELECT * FROM items WHERE id = ?", (d["item_id"],)) if d.get("item_id") else None
    last = db.one("SELECT created_by FROM versions WHERE document_id = ? ORDER BY version DESC LIMIT 1", (d["id"],))
    if last and last["created_by"] == user["id"] and four_eyes_applies(item):
        if not db.get_setting("allow_self_approval_with_reason"):
            out.append("You made the last change to this file, so someone else must approve it (four-eyes rule).")
        elif len((reason or "").strip()) < 5:
            out.append("You made the last change to this file. Write why you approve your own work; the reason is "
                       "kept in the audit log.")
    groups = db.rows("SELECT DISTINCT g.id, g.label FROM cp_groups g LEFT JOIN cp_members m ON m.group_id = g.id "
                     "LEFT JOIN cp_item_members im ON im.group_id = g.id WHERE g.status = 'open' AND "
                     "(m.document_id = ? OR im.item_id = ?)", (d["id"], d.get("item_id") or 0))
    if groups and len((reason or "").strip()) < 5:
        out.append("Kosha found possible duplicates of this part that nobody has decided on yet (Duplicates). Decide "
                   "them first, or write why it is released anyway.")
    return out


def spares_only_problems(d: dict, replacement: str) -> tuple[list[str], int | None]:
    out: list[str] = []
    rid = None
    if not (replacement or "").strip():
        out.append("Name the part that replaces it in new machines.")
    else:
        rid = db.scalar("SELECT id FROM items WHERE part_number = ? COLLATE NOCASE", (replacement.strip(),))
        if not rid:
            out.append(f"There is no part {replacement.strip()}.")
        elif rid == d.get("item_id"):
            out.append("A part cannot replace itself.")
    return out, rid


def obsolete_problems(d: dict) -> list[str]:
    if not d.get("item_id"):
        return []
    used = db.rows("SELECT DISTINCT p.part_number FROM bom_lines bl JOIN boms b ON b.id = bl.bom_id "
                   "JOIN items p ON p.id = b.item_id WHERE bl.child_item_id = ? AND b.state = 'released' "
                   "AND p.state IN ('released', 'spares_only') ORDER BY p.part_number", (d["item_id"],))
    if not used:
        return []
    names = ", ".join(u["part_number"] for u in used[:8]) + (f" and {len(used) - 8} more" if len(used) > 8 else "")
    return [f"It is still used in released assemblies: {names}. Replace it there first, or make it spares only."]
