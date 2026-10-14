# SPDX-License-Identifier: AGPL-3.0-or-later
"""The practice folder, *Training — safe to try* (specification 5.6).

People try every action there with their own permissions. What happens to it never reaches the ERP (the ERP
queue marks such jobs "skipped"), and once a day its files go to the recycle bin, so it starts empty again. Nothing
is lost: the bin keeps them for the usual number of days.
"""
from __future__ import annotations

from . import db

NAME = "Training — safe to try"
SETTING = "practice_folder_id"


def enabled() -> bool:
    return bool(db.get_setting("practice_folder"))


def folder_id() -> int | None:
    """The practice folder, if it exists (it is made when Kosha starts with the setting switched on)."""
    fid = db.get_setting(SETTING)
    if fid and db.one("SELECT id FROM folders WHERE id = ?", (fid,)):
        return int(fid)
    return None


def ensure() -> int | None:
    """Make the practice folder once, under the top folder. An admin who deletes it afterwards is not overruled."""
    if not enabled() or db.get_setting(SETTING):
        return folder_id()
    from .lifecycle import root_folder_id
    root = root_folder_id()
    with db.tx():
        row = db.one("SELECT id FROM folders WHERE parent_id = ? AND name = ? COLLATE NOCASE", (root, NAME))
        fid = row["id"] if row else db.insert("folders", {"parent_id": root, "name": NAME, "created_at": db.now()})
        db.set_setting(SETTING, fid)
    return fid


def _subtree(fid: int) -> list[int]:
    ids, todo = [], [fid]
    while todo:
        f = todo.pop()
        ids.append(f)
        todo += [r["id"] for r in db.rows("SELECT id FROM folders WHERE parent_id = ?", (f,))]
    return ids


def in_practice(doc: dict) -> bool:
    """A file in the practice folder, or one that was cleared out of it into the recycle bin."""
    fid = folder_id()
    if not fid:
        return False
    if doc.get("deleted") and doc.get("deleted_path") is not None:
        top = db.scalar("SELECT name FROM folders WHERE id = ?", (fid,)) or NAME
        return doc["deleted_path"] == top or doc["deleted_path"].startswith(top + "/")
    return doc["folder_id"] in _subtree(fid)


def is_practice_item(item_id: int | None) -> bool:
    """An item made only from practice files (an item with no files at all is a real one)."""
    if not item_id or not folder_id():
        return False
    docs = db.rows("SELECT id, folder_id, deleted, deleted_path FROM documents WHERE item_id = ?", (item_id,))
    return bool(docs) and all(in_practice(d) for d in docs)


def erp_job_is_practice(kind: str, entity_id: int) -> bool:
    if kind in ("item", "refresh"):
        return is_practice_item(entity_id)
    if kind == "bom":
        return is_practice_item(db.scalar("SELECT item_id FROM boms WHERE id = ?", (entity_id,)))
    if kind == "mbom":
        return is_practice_item(db.scalar("SELECT item_id FROM mboms WHERE id = ?", (entity_id,)))
    if kind == "attach":
        return is_practice_item(db.scalar("SELECT item_id FROM documents WHERE id = ?", (entity_id,)))
    return False


def clear() -> dict:
    """Once a day: every file in the practice folder goes to the recycle bin (its folders stay)."""
    fid = folder_id()
    if not fid:
        return {"cleared": 0}
    from .filesafety import to_bin
    ids = _subtree(fid)
    marks = ",".join("?" * len(ids))
    with db.tx():
        docs = db.rows(f"SELECT * FROM documents WHERE folder_id IN ({marks}) AND deleted = 0", ids)
        for d in docs:
            to_bin(None, d)
        if docs:
            db.audit(None, "practice_clear", "folder", fid, f"{len(docs)} file{'s' if len(docs) != 1 else ''} "
                     "from the practice folder to the recycle bin")
    return {"cleared": len(docs)}
