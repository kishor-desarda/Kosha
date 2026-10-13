# SPDX-License-Identifier: AGPL-3.0-or-later
"""Change-log drafts built from measured changes, without AI.

Three things are kept apart on purpose:

* the **input**: measured changes, where-used and stock, the change order's reason. Facts only,
  produced by the diff engine (``document_input`` / ``change_input``), stored with ``input_hash``;
* the **draft**: text generated from an input and the engineer's short note. ``draft()`` is the
  rule-based generator ("rules-v1"); an AI job can later read the same stored input and write its
  own draft into ``changelog_drafts`` with another ``generator``;
* the **saved change log**: the text the engineer edited and saved (``changelogs``).

The draft always has the same four sections, in this order.
"""
from __future__ import annotations

import hashlib
import json

from .. import boms, changes, db, lifecycle
from ..web import ROLE_LEVEL, bad
from . import service

SECTIONS = (("what", "What changed"), ("why", "Why"),
            ("effect", "Effect on parts in stock and work in progress"), ("todo", "What to do"))
GENERATOR = "rules-v1"
MAX_LINES_PER_FILE = 15
DRAWING_KINDS = {"dimension", "holes", "extents", "entities", "note", "model_size", "volume", "area"}


# ------------------------------------------------------------------ inputs --

def _doc_info(d: dict) -> dict:
    return {"id": d["id"], "filename": d["filename"], "part_number": d["part_number"], "description": d["description"],
            "ext": d["ext"], "doc_type": d["doc_type"], "item_id": d["item_id"]}


def _effects(item_ids: list[int]) -> tuple[list[dict], list[dict]]:
    used, stock = [], []
    seen = set()
    for iid in dict.fromkeys(i for i in item_ids if i):
        it = db.one("SELECT part_number, description, uom, erp_stock, erp_synced_at FROM items WHERE id = ?", (iid,))
        if not it:
            continue
        stock.append({"part_number": it["part_number"], "stock": it["erp_stock"], "uom": it["uom"],
                      "synced_at": it["erp_synced_at"]})
        for w in boms.where_used(iid):
            key = (w["item_id"], w["bom_revision"], it["part_number"])
            if key in seen:
                continue
            seen.add(key)
            used.append({"part_number": w["part_number"], "description": w["description"], "bom_revision": w["bom_revision"],
                         "bom_state": w["bom_state"], "qty": w["qty"], "child": it["part_number"]})
    return used, stock


def _erp_enabled() -> bool:
    return bool((db.get_setting("erp") or {}).get("enabled"))


def document_input(doc_id: int, revision: str | None = None) -> dict:
    doc = lifecycle.doc_or_404(doc_id)
    rev = (revision or doc["revision"]).strip()
    vs = service.versions(doc_id)
    to_row = service.latest_of_revision(vs, rev)
    group = {"document": _doc_info(doc), "revision": rev}
    if not to_row:
        if rev.upper() != doc["revision"].upper() and \
                rev.upper() not in {r.upper() for r in service.released_revisions(doc_id)}:
            raise bad(f"{doc['filename']} has no revision {rev}.")
        group.update(service.compare_rows(doc, None, None))
        group["notes"] = [f"Revision {rev} has no checked-in version yet; the measured changes appear after the "
                          "first check-in."]
    else:
        from_row, _ = service.default_from(doc, to_row, vs)
        group.update(service.compare_rows(doc, from_row, to_row))
    used, stock = _effects([doc["item_id"]])
    return {"kind": "document", "subject": {"id": doc_id, "label": doc["filename"], "part_number": doc["part_number"],
                                            "description": doc["description"], "revision": rev},
            "groups": [group], "where_used": used, "stock": stock, "reason": "", "erp_enabled": _erp_enabled()}


def change_input(co_id: int) -> dict:
    co = changes.co_or_404(co_id)
    groups = []
    item_ids = []
    for ci in db.rows("SELECT * FROM co_items WHERE co_id = ? ORDER BY id", (co_id,)):
        d = db.one("SELECT * FROM documents WHERE id = ?", (ci["document_id"],))
        if not d:
            continue
        item_ids.append(d["item_id"])
        g = {"document": _doc_info(d), "revision": ci["to_revision"] or d["revision"], "action": ci["action"]}
        if ci["action"] == "obsolete":
            g.update(service.compare_rows(d, None, None))
            g["changes"] = [{"kind": "lifecycle", "severity": "major", "where": d["filename"], "before": d["revision"],
                             "after": None, "text": f"{d['filename']} is made obsolete (revision {d['revision']})",
                             "zone": None, "detail": {}, "source": "change_order"}]
            g["counts"] = {"major": 1, "minor": 0, "info": 0}
            g["total"] = 1
            groups.append(g)
            continue
        vs = service.versions(d["id"])
        to_rev = ci["to_revision"] or d["revision"]
        to_row = service.latest_of_revision(vs, to_rev)
        from_row = service.latest_of_revision(vs, ci["from_revision"]) if ci["from_revision"] else None
        g.update(service.compare_rows(d, from_row if to_row else None, to_row))
        if not to_row:
            g["notes"] = [f"No version of revision {to_rev} has been checked in yet."]
        elif not ci["from_revision"]:
            g["notes"] = ["New file: there is no earlier released revision to compare with."]
        elif from_row and to_row and from_row["version"] == to_row["version"]:
            g["notes"] = [f"Revision {to_rev} has no new version yet; check in the changed file first."]
        groups.append(g)
    used, stock = _effects(item_ids)
    return {"kind": "change_order", "subject": {"id": co_id, "number": co["number"], "title": co["title"],
                                                "label": f"{co['number']} {co['title']}", "state": co["state"]},
            "groups": groups, "where_used": used, "stock": stock, "reason": co["reason"] or "",
            "description": co["description"] or "", "erp_enabled": _erp_enabled()}


def input_hash(inp: dict) -> str:
    facts = {"groups": [{"document": g["document"]["id"], "from": g.get("from"), "to": g.get("to"),
                         "changes": [c["text"] for c in g.get("changes", [])]} for g in inp.get("groups", [])],
             "where_used": inp.get("where_used"), "reason": inp.get("reason"), "description": inp.get("description")}
    return hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------------- draft --

def _rev_span(g: dict) -> str:
    f, t = g.get("from"), g.get("to")
    if f and t:
        return f"revision {f['revision']} (v{f['version']}) → revision {t['revision']} (v{t['version']})"
    return f"revision {g.get('revision')}"


def draft(inp: dict, note: str = "") -> dict:
    """The rule-based draft: four sections of plain text, from facts only."""
    groups = inp.get("groups") or []
    multi = len(groups) > 1 or inp.get("kind") == "change_order"
    what: list[str] = []
    for g in groups:
        lines = [c["text"] for c in g.get("changes") or []]
        extra = len(lines) - MAX_LINES_PER_FILE
        lines = lines[:MAX_LINES_PER_FILE]
        body = [f"- {t}" for t in lines]
        if extra > 0:
            body.append(f"- … and {extra} more measured changes (see the change list).")
        if not body:
            body = [f"- {n}" for n in (g.get("notes") or [])] or ["- No measured differences."]
        if multi:
            what.append(f"{g['document']['filename']}, {_rev_span(g)}:")
        elif g.get("from"):
            what.append(f"Compared with {g['from']['label']}:")
        what.extend(body)
        if multi:
            what.append("")
    why = (note or "").strip()
    if not why and inp.get("kind") == "change_order":
        why = "\n\n".join(x for x in (inp.get("reason", "").strip(), inp.get("description", "").strip()) if x)
    if not why:
        why = "Reason not given yet. Say in one or two sentences why this change is needed."

    effect: list[str] = []
    used = inp.get("where_used") or []
    if used:
        effect.append("Used in:")
        for w in used:
            effect.append(f"- {w['part_number']} {w['description']}, BOM revision {w['bom_revision']} ({w['bom_state']}), "
                          f"qty {_n(w['qty'])}" + (f" of {w['child']}" if multi else ""))
    else:
        effect.append("Not used in any assembly BOM in Kosha.")
    known = [s for s in inp.get("stock") or [] if s.get("stock") is not None]
    for s in known:
        effect.append(f"Stock of {s['part_number']} in the ERP: {_n(s['stock'])} {s['uom']}.")
    unknown = [s["part_number"] for s in inp.get("stock") or [] if s.get("stock") is None]
    if unknown:
        effect.append(f"Stock of {', '.join(unknown)}: not known (not read from the ERP yet).")
    effect.append("Work in progress: Kosha does not see production orders; check open orders in the ERP.")

    todo: list[str] = []
    all_changes = [c for g in groups for c in g.get("changes") or []]
    drawing = [g for g in groups if any(c["kind"] in DRAWING_KINDS for c in g.get("changes") or [])]
    for g in drawing:
        if g.get("from") and g.get("to"):
            todo.append(f"- Replace printed copies of {g['document']['filename']} revision {g['from']['revision']} "
                        f"with revision {g['to']['revision']} once it is released.")
    if used and any(c["severity"] == "major" for c in all_changes):
        parents = list(dict.fromkeys(w["part_number"] for w in used))
        todo.append(f"- Check the assemblies that use it: {', '.join(parents)}.")
    if any(c["kind"].startswith("bom_") or c["kind"] == "component" for c in all_changes):
        todo.append("- Tell purchasing and production about the BOM changes listed above.")
    if any((s.get("stock") or 0) > 0 for s in known):
        for s in known:
            if (s.get("stock") or 0) > 0:
                todo.append(f"- Decide what happens to the {_n(s['stock'])} {s['uom']} of {s['part_number']} in stock: "
                            "use as is, rework or scrap.")
    elif all_changes:
        todo.append("- Decide what happens to parts in stock and in production: use as is, rework or scrap.")
    if inp.get("erp_enabled") and all_changes:
        todo.append("- Kosha sends the new revision to the ERP when it is released.")
    if not all_changes:
        if groups and all(g.get("to") and not g.get("from") for g in groups):
            todo.append("- First release: there is nothing earlier to compare with, so no measured changes are listed.")
        else:
            todo.append("- Nothing measurable has changed yet: check that the changed files are checked in.")
    return {"what": "\n".join(what).strip(), "why": why, "effect": "\n".join(effect), "todo": "\n".join(todo)}


def _n(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else f"{f:g}"


# ----------------------------------------------------------------- storage --

def load(subject: str, subject_id: int, revision: str = "") -> dict | None:
    row = db.one("SELECT * FROM changelogs WHERE subject = ? AND subject_id = ? AND revision = ?",
                 (subject, subject_id, revision or ""))
    if not row:
        return None
    names = lifecycle.user_names()
    return {"id": row["id"], "note": row["note"], "sections": json.loads(row["sections_json"] or "{}"),
            "source": row["source"], "input_hash": row["input_hash"], "updated_at": row["updated_at"],
            "updated_by": names.get(row["updated_by"], "?"), "created_at": row["created_at"]}


def save(user: dict, subject: str, subject_id: int, revision: str, note: str, sections: dict, inp: dict) -> dict:
    if not isinstance(sections, dict):
        raise bad("sections must be an object with what, why, effect and todo.")
    clean = {k: str(sections.get(k) or "")[:20000] for k, _ in SECTIONS}
    if not any(v.strip() for v in clean.values()):
        raise bad("The change log is empty. Build a draft or write it first.")
    note = str(note or "")[:4000]
    h = input_hash(inp)
    rule = draft(inp, note)
    source = "rules" if clean == rule else "edited"
    now = db.now()
    with db.tx():
        row = db.one("SELECT id FROM changelogs WHERE subject = ? AND subject_id = ? AND revision = ?",
                     (subject, subject_id, revision or ""))
        data = {"note": note, "sections_json": json.dumps(clean), "input_json": json.dumps(inp, default=str),
                "input_hash": h, "source": source, "updated_by": user["id"], "updated_at": now}
        if row:
            db.update("changelogs", row["id"], data)
        else:
            db.insert("changelogs", {"subject": subject, "subject_id": subject_id, "revision": revision or "",
                                     "created_by": user["id"], "created_at": now, **data})
        if not db.scalar("SELECT id FROM changelog_drafts WHERE subject = ? AND subject_id = ? AND revision = ? AND "
                         "generator = ? AND input_hash = ? AND note = ?",
                         (subject, subject_id, revision or "", GENERATOR, h, note)):
            db.insert("changelog_drafts", {"subject": subject, "subject_id": subject_id, "revision": revision or "",
                                           "generator": GENERATOR, "input_hash": h, "input_json": json.dumps(inp, default=str),
                                           "note": note, "sections_json": json.dumps(rule), "status": "ready",
                                           "created_by": user["id"], "created_at": now})
        db.audit(user["id"], "changelog_save", subject, subject_id, f"revision {revision}" if revision else "")
    return load(subject, subject_id, revision)


def payload(subject: str, subject_id: int, revision: str, inp: dict, can_edit: dict) -> dict:
    saved = load(subject, subject_id, revision)
    h = input_hash(inp)
    if saved:
        saved["stale"] = saved.pop("input_hash") != h
    return {"subject": subject, "subject_id": subject_id, "revision": revision,
            "sections": [{"key": k, "label": label} for k, label in SECTIONS], "input": inp,
            "draft": draft(inp, saved["note"] if saved else ""), "saved": saved, "can_edit": can_edit,
            "generator": GENERATOR}


# ------------------------------------------------------------- permissions --

def can_edit_document(user: dict, doc: dict, revision: str) -> dict:
    lvl = ROLE_LEVEL[user["role"]]
    if lvl < ROLE_LEVEL["engineer"]:
        return {"ok": False, "why": "You have view-only access."}
    released = revision.upper() != doc["revision"].upper() or doc["state"] in ("released", "obsolete")
    if released and lvl < ROLE_LEVEL["approver"]:
        return {"ok": False, "why": f"Revision {revision} is released; only an approver can change its change log."}
    return {"ok": True, "why": ""}


def can_edit_change(user: dict, co: dict) -> dict:
    lvl = ROLE_LEVEL[user["role"]]
    if lvl < ROLE_LEVEL["engineer"]:
        return {"ok": False, "why": "You have view-only access."}
    if co["state"] in ("implemented", "cancelled") and lvl < ROLE_LEVEL["approver"]:
        return {"ok": False, "why": "This change order is closed; only an approver can change its change log."}
    return {"ok": True, "why": ""}
