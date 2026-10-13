# SPDX-License-Identifier: AGPL-3.0-or-later
"""The item master beyond number and description (specification sections 3 and 4):

* product family, and the spare / wear-part / recommended-spare flags ("one physical part has one number";
  being a spare is a flag, not a second number);
* OEM cross-references: the other maker, their machine model and part number, how the design came about and
  whether it fits directly. An OEM part number typed anywhere finds the company's part. The OEM's name never
  becomes part of the company's own part number or description;
* customer-specific parts: the customer and the customer's part number, the restricted flag and the
  do-not-reuse lock. A locked part goes into another customer's assembly only after a data steward allows it, with a
  reason that is kept.
"""
from __future__ import annotations

import re

from . import db
from .web import bad, conflict, not_found

FLAGS = ("spare", "wear_part", "recommended_spare", "restricted", "no_reuse")
TEXT_FIELDS = ("family", "customer", "customer_part_number", "hsn")
OEM_CODE = re.compile(r"^[A-Z]{2,4}$")
SOURCES = {"reverse_engineered": "Reverse engineered", "customer_drawing": "Customer drawing",
           "own_design": "Own design"}
FITS = {"direct": "Direct fit", "modified": "Fit with modification"}
# changing who may see or reuse a part is a decision for an approver, not for whoever edits the part
GUARDED = ("customer", "restricted", "no_reuse")


def item_or_404(item_id: int) -> dict:
    it = db.one("SELECT * FROM items WHERE id = ?", (item_id,))
    if not it:
        raise not_found("Item not found")
    return it


def clean_fields(body: dict, current: dict | None = None, cfg=None) -> dict:
    """The item-master fields in a PATCH body, checked against the item as it is now (``current``).
    Raises a plain 400 for a bad value."""
    current = current or {}
    out: dict = {}
    for k in FLAGS:
        if k in body:
            v = body[k]
            if v not in (True, False, 0, 1, "0", "1", "true", "false"):
                raise bad(f"{k.replace('_', ' ').capitalize()} must be yes or no.")
            out[k] = 1 if v in (True, 1, "1", "true") else 0
    for k in TEXT_FIELDS:
        if k in body:
            out[k] = " ".join(str(body[k] or "").split())[:120]
    if out.get("family"):
        out["family"] = out["family"].upper()
        if cfg is None:
            from .packs import PackConfig
            cfg = PackConfig.current()
        known = {f["code"] for f in cfg.families()}
        if known and out["family"] not in known:
            raise bad(f"There is no product family '{out['family']}'. Choose one of {', '.join(sorted(known))}, "
                      "or ask the admin to add it under Admin › Packs.")
    if out.get("hsn") and not re.match(r"^\d{4}(\d{2}){0,2}$", out["hsn"]):
        raise bad("The HSN code has 4, 6 or 8 digits, for example 84833000.")
    customer = out["customer"] if "customer" in out else current.get("customer")
    if out.get("restricted", current.get("restricted")) and not customer:
        raise bad("Name the customer first: a restricted part is restricted to one customer's team.")
    if out.get("restricted") and not current.get("restricted") or (customer != current.get("customer")
                                                                    and out.get("restricted", current.get("restricted"))):
        if not any(r["value"].lower() == customer.lower() for r in
                   db.rows("SELECT value FROM access_rules WHERE scope = 'customer' AND deleted = 0")):
            raise bad(f"Nobody has access to {customer} yet, so nobody could see this part. An admin first names the "
                      "customer's team under Admin › Access; then restrict the part.")
    return out


# -------------------------------------------------------------------- OEMs --

def oems() -> list[dict]:
    return db.rows("SELECT code, name FROM oems ORDER BY code")


def add_oem(user_id: int, code: str, name: str) -> dict:
    code = (code or "").strip().upper()
    name = " ".join((name or "").split())
    if not OEM_CODE.match(code):
        raise bad("An OEM code is 2 to 4 capital letters, for example ABC. It is used instead of the maker's name.")
    if not name:
        raise bad("Give the maker's name, so people know which code is which.")
    if db.one("SELECT 1 FROM oems WHERE code = ?", (code,)):
        raise conflict(f"The OEM code {code} is already used.")
    with db.tx():
        db.insert("oems", {"code": code, "name": name, "created_by": user_id, "created_at": db.now()})
        db.audit(user_id, "oem_add", "oem", None, f"{code} {name}")
    return {"code": code, "name": name}


def oem_refs(item_id: int) -> list[dict]:
    return db.rows("SELECT r.*, o.name AS oem_name FROM item_oem_refs r LEFT JOIN oems o ON o.code = r.oem "
                   "WHERE r.item_id = ? AND r.deleted = 0 ORDER BY r.id", (item_id,))


def add_oem_ref(user_id: int, item_id: int, body: dict) -> dict:
    item_or_404(item_id)
    oem = str(body.get("oem") or "").strip().upper()
    if not db.one("SELECT 1 FROM oems WHERE code = ?", (oem,)):
        raise bad(f"There is no OEM code '{oem}'. Pick one from the list, or ask an approver to add the maker.")
    pn = " ".join(str(body.get("oem_part_number") or "").split())[:80]
    model = " ".join(str(body.get("oem_model") or "").split())[:80]
    if not pn and not model:
        raise bad("Give the OEM part number or the OEM machine model (or both).")
    source = str(body.get("source") or "")
    fit = str(body.get("fit") or "")
    if source and source not in SOURCES:
        raise bad(f"Source of design must be one of: {', '.join(SOURCES.values())}.")
    if fit and fit not in FITS:
        raise bad(f"Interchangeable must be one of: {', '.join(FITS.values())}.")
    if pn and db.one("SELECT 1 FROM item_oem_refs WHERE item_id = ? AND oem = ? AND oem_part_number = ? "
                     "AND deleted = 0", (item_id, oem, pn)):
        raise conflict(f"{oem} {pn} is already a cross-reference of this part.")
    with db.tx():
        rid = db.insert("item_oem_refs", {
            "item_id": item_id, "oem": oem, "oem_model": model, "oem_part_number": pn, "source": source, "fit": fit,
            "notes": " ".join(str(body.get("notes") or "").split())[:300], "created_by": user_id,
            "created_at": db.now()})
        db.update("items", item_id, {"updated_at": db.now()})
        db.audit(user_id, "oem_ref_add", "item", item_id, f"{oem} {pn or model}")
    return db.one("SELECT * FROM item_oem_refs WHERE id = ?", (rid,))


def remove_oem_ref(user_id: int, ref_id: int) -> None:
    r = db.one("SELECT * FROM item_oem_refs WHERE id = ? AND deleted = 0", (ref_id,))
    if not r:
        raise not_found("This cross-reference was already removed.")
    with db.tx():
        db.execute("UPDATE item_oem_refs SET deleted = 1 WHERE id = ?", (ref_id,))
        db.audit(user_id, "oem_ref_remove", "item", r["item_id"], f"{r['oem']} {r['oem_part_number'] or r['oem_model']}")


def find_by_oem(q: str, limit: int = 50) -> list[dict]:
    """The company's parts for an OEM part number or machine model (spaces, dashes and dots ignored)."""
    key = _squash(q)
    if len(key) < 2:
        return []
    return db.rows(
        "SELECT r.id AS ref_id, r.oem, o.name AS oem_name, r.oem_model, r.oem_part_number, r.fit, r.source, "
        "i.id AS item_id, i.part_number, i.description, i.state, i.revision, i.restricted, i.customer "
        "FROM item_oem_refs r JOIN items i ON i.id = r.item_id LEFT JOIN oems o ON o.code = r.oem "
        "WHERE r.deleted = 0 AND (REPLACE(REPLACE(REPLACE(UPPER(r.oem_part_number), ' ', ''), '-', ''), '.', '') "
        "LIKE ? OR UPPER(r.oem_model) LIKE ?) ORDER BY i.part_number LIMIT ?",
        (f"%{key}%", f"%{q.strip().upper()}%", limit))


def _squash(s: str) -> str:
    return re.sub(r"[\s\-.]", "", (s or "").upper())


# ------------------------------------------------------------- reuse lock --

def reuse_problems(parent_item_id: int, child_ids: list[int], keep: set[int] | None = None) -> list[str]:
    """Children locked against reuse that may not go into this parent: a different (or no) customer, and no
    data steward's decision allowing it. Children already in the BOM (``keep``) are not checked again."""
    parent = db.one("SELECT part_number, customer FROM items WHERE id = ?", (parent_item_id,)) or {}
    out = []
    for cid in set(child_ids) - (keep or set()):
        c = db.one("SELECT id, part_number, customer, no_reuse FROM items WHERE id = ?", (cid,))
        if not c or not c["no_reuse"]:
            continue
        if c["customer"] and c["customer"].lower() == (parent.get("customer") or "").lower():
            continue
        if db.one("SELECT 1 FROM item_reuse_approvals WHERE item_id = ? AND parent_item_id = ?",
                  (cid, parent_item_id)):
            continue
        who = f" for {c['customer']}" if c["customer"] else ""
        out.append(f"{c['part_number']} is made{who} and locked against reuse. A data steward can allow it in "
                   f"{parent.get('part_number', 'this assembly')} with a reason (on the part's page).")
    return out


def allow_reuse(user_id: int, item_id: int, parent_item_id: int, reason: str) -> dict:
    item_or_404(item_id)
    item_or_404(parent_item_id)
    reason = " ".join((reason or "").split())
    if len(reason) < 5:
        raise bad("Write why this part may be reused here; the reason is kept in the audit log.")
    if db.one("SELECT 1 FROM item_reuse_approvals WHERE item_id = ? AND parent_item_id = ?", (item_id, parent_item_id)):
        raise conflict("Reuse in this assembly is already allowed.")
    with db.tx():
        db.insert("item_reuse_approvals", {"item_id": item_id, "parent_item_id": parent_item_id, "reason": reason,
                                           "approved_by": user_id, "approved_at": db.now()})
        parent = db.scalar("SELECT part_number FROM items WHERE id = ?", (parent_item_id,))
        db.audit(user_id, "reuse_allow", "item", item_id, f"in {parent}: {reason}")
    return {"message": f"Allowed in {parent}. The reason is in the audit log."}
