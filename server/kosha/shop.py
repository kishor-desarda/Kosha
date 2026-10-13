# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shop view (specification 5.7): for a part number, the drawings the shop floor may use.

Only released revisions are shown. While a new revision is being worked on, the shop still sees the last released
one (with a note that a change is in work); a part that never had a released drawing shows nothing to build from.
"""
from __future__ import annotations

from . import access, db
from .web import not_found

WARN = {"obsolete": "Obsolete: do not make this part.",
        "spares_only": "Spares only: make it only for spares orders."}


def lookup(part_number: str) -> dict:
    pn = " ".join((part_number or "").split())
    if not pn:
        raise not_found("Type or scan a part number.")
    item = db.one("SELECT id, part_number, description, revision, state, material, item_group FROM items "
                  "WHERE part_number = ? COLLATE NOCASE", (pn,))
    if not item or not access.scope().can_see_item(item["id"]):
        raise not_found(f"No part {pn} in Kosha. Check the number on the job card.")
    scope = access.scope()
    drawings = []
    for d in db.rows("SELECT * FROM documents WHERE item_id = ? AND deleted = 0 ORDER BY doc_type, filename",
                     (item["id"],)):
        if not scope.can_see_document(d):
            continue
        rev = db.scalar("SELECT revision FROM lifecycle_events WHERE document_id = ? AND to_state = 'released' "
                        "ORDER BY id DESC LIMIT 1", (d["id"],))
        if not rev:
            continue
        v = db.one("SELECT version, preview_kind FROM versions WHERE document_id = ? AND revision = ? "
                   "ORDER BY version DESC LIMIT 1", (d["id"], rev))
        if not v:
            continue
        pdf = db.scalar("SELECT id FROM release_outputs WHERE document_id = ? AND revision = ? AND kind = 'released_pdf' "
                        "AND status = 'ok' ORDER BY id DESC LIMIT 1", (d["id"], rev))
        note = WARN.get(d["state"], "")
        if not note and d["revision"] != rev:
            note = f"Rev {d['revision']} is being worked on; use rev {rev} until it is released."
        drawings.append({"id": d["id"], "filename": d["filename"], "doc_type": d["doc_type"], "revision": rev,
                         "version": v["version"], "preview_kind": v["preview_kind"], "pdf_output_id": pdf,
                         "state": d["state"], "note": note})
    return {"item": {**item, "warning": WARN.get(item["state"], "")}, "drawings": drawings}
