# SPDX-License-Identifier: AGPL-3.0-or-later
"""Item properties as they were when a revision was released.

Items keep only their current description, material and weight. To list a property change between
two released revisions, Kosha keeps a copy of the item's properties at each release (taken in the
same transaction as the release) and compares those copies.
"""
from __future__ import annotations

import json

from .. import db

FIELDS = ("description", "material", "weight_kg", "uom", "make_buy", "item_type", "item_group")


def item_props(item_id: int | None) -> dict | None:
    if not item_id:
        return None
    it = db.one("SELECT " + ", ".join(FIELDS) + " FROM items WHERE id = ?", (item_id,))
    return dict(it) if it else None


def record(document_id: int, revision: str, item_id: int | None) -> None:
    """Keep the first copy for a revision: a released revision does not change."""
    props = item_props(item_id)
    if props is None:
        return
    if db.scalar("SELECT id FROM diff_prop_snapshots WHERE document_id = ? AND revision = ?", (document_id, revision)):
        return
    db.insert("diff_prop_snapshots", {"document_id": document_id, "revision": revision, "item_id": item_id,
                                      "props_json": json.dumps(props), "taken_at": db.now()})


def on_release(doc: dict) -> None:
    """Called inside the release transaction (lifecycle.set_state)."""
    record(doc["id"], doc["revision"], doc.get("item_id"))


def get(document_id: int, revision: str) -> dict | None:
    r = db.scalar("SELECT props_json FROM diff_prop_snapshots WHERE document_id = ? AND revision = ?",
                  (document_id, revision))
    return json.loads(r) if r else None
