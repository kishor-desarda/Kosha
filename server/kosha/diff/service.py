# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measured changes of a document: which versions to compare, and what Kosha knows about each.

By default the latest version is compared with the latest version of the previous released
revision, so the list answers "what is different from what the shop floor has now".
"""
from __future__ import annotations

import json
import re

from .. import boms, db, lifecycle, store
from ..web import bad, not_found
from . import engine, snapshots
from .core import Side


def versions(doc_id: int) -> list[dict]:
    return db.rows("SELECT id, version, revision, blob_sha, meta_json, created_at, comment FROM versions "
                   "WHERE document_id = ? ORDER BY version", (doc_id,))


def released_revisions(doc_id: int) -> list[str]:
    seen: dict[str, None] = {}
    for r in db.rows("SELECT revision FROM lifecycle_events WHERE document_id = ? AND to_state = 'released' "
                     "ORDER BY id", (doc_id,)):
        seen[r["revision"]] = None
    return list(seen)


def latest_of_revision(vs: list[dict], revision: str) -> dict | None:
    rows = [v for v in vs if v["revision"].upper() == str(revision).upper()]
    return rows[-1] if rows else None


def resolve(vs: list[dict], ref: str | None) -> dict | None:
    """'v3' is version 3; 'B' is the latest version of revision B; a bare number is a revision if one has that
    name, otherwise a version."""
    if ref is None or str(ref).strip() == "":
        return None
    s = str(ref).strip()
    m = re.fullmatch(r"[vV](\d+)", s)
    if m:
        row = next((v for v in vs if v["version"] == int(m.group(1))), None)
        if not row:
            raise not_found(f"Version {s} does not exist.")
        return row
    row = latest_of_revision(vs, s)
    if row:
        return row
    if s.isdigit():
        row = next((v for v in vs if v["version"] == int(s)), None)
        if row:
            return row
    raise not_found(f"No version of revision {s} has been checked in.")


def default_from(doc: dict, to_row: dict, vs: list[dict]) -> tuple[dict | None, str]:
    """The previous released revision before ``to_row``; else the version before it. Returns (row, why)."""
    released = set(r.upper() for r in released_revisions(doc["id"]))
    best = None
    for v in vs:
        if v["version"] >= to_row["version"] or v["revision"].upper() == to_row["revision"].upper():
            continue
        if v["revision"].upper() in released and latest_of_revision(vs, v["revision"])["version"] == v["version"]:
            best = v
    if best:
        return best, "previous released revision"
    prev = [v for v in vs if v["version"] < to_row["version"]]
    if prev:
        return prev[-1], "previous version"
    return None, ""


def item_bom(item_id: int | None, revision: str) -> tuple[int | None, list[dict] | None]:
    if not item_id:
        return None, None
    b = db.one("SELECT id FROM boms WHERE item_id = ? AND revision = ?", (item_id, revision))
    if not b:
        return None, None
    return b["id"], boms.lines(b["id"])


def side(doc: dict, row: dict) -> Side:
    meta = json.loads(row.get("meta_json") or "{}")
    props = snapshots.get(doc["id"], row["revision"])
    if props is None and row["revision"].upper() == doc["revision"].upper():
        props = snapshots.item_props(doc["item_id"])   # the revision being worked on: the item as it is now
    bom_id, lines = item_bom(doc["item_id"], row["revision"])
    sha = row["blob_sha"]
    return Side(filename=doc["filename"], ext=doc["ext"], revision=row["revision"], version=row["version"],
                document_id=doc["id"], blob_sha=sha, meta=meta, props=props, bom_lines=lines, bom_id=bom_id,
                loader=lambda: store.read_bytes(sha))


def label(row: dict | None) -> dict | None:
    if not row:
        return None
    return {"version": row["version"], "revision": row["revision"], "label": f"revision {row['revision']} (v{row['version']})",
            "created_at": row["created_at"]}


def compare_rows(doc: dict, from_row: dict | None, to_row: dict | None) -> dict:
    out = {"from": label(from_row), "to": label(to_row), "changes": [], "notes": [], "comparers": [],
           "counts": {"major": 0, "minor": 0, "info": 0}, "total": 0}
    if not to_row:
        return out
    if not from_row:
        out["notes"].append("This is the first version, so there is nothing earlier to compare it with.")
        return out
    if from_row["version"] == to_row["version"]:
        out["notes"].append("Both sides are the same version.")
        return out
    d = engine().compare(side(doc, from_row), side(doc, to_row))
    out.update(d.to_dict())
    if from_row["blob_sha"] == to_row["blob_sha"] and not d.changes:
        out["notes"].append("The file content is identical in both versions.")
    return out


def compare_document(doc_id: int, from_ref: str | None = None, to_ref: str | None = None) -> dict:
    doc = lifecycle.doc_or_404(doc_id)
    vs = versions(doc_id)
    if not vs:
        raise bad("This file has no versions yet.")
    to_row = resolve(vs, to_ref) or vs[-1]
    basis = "chosen"
    if from_ref:
        from_row = resolve(vs, from_ref)
    else:
        from_row, basis = default_from(doc, to_row, vs)
    out = compare_rows(doc, from_row, to_row)
    out["document"] = {"id": doc["id"], "filename": doc["filename"], "part_number": doc["part_number"],
                       "revision": doc["revision"], "ext": doc["ext"]}
    out["basis"] = basis
    out["file_types"] = engine().file_types
    return out
