# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workbench (specification 9.1): favourites, saved views and personal screen preferences.

* **Favourites** point at a file, part, BOM, folder, project or change. Their names are read when they are listed,
  so a renamed part shows its new name, and a record the person may no longer see (or that was deleted) is left out.
* **Saved views** are named addresses of a screen with its filters (``#/files/12?state=released``), so a person gets
  back to "Released pump drawings" in one click or from Ctrl+K.
* **Preferences**: accent colour, text size and the pages pinned to the strip at the bottom of the screen.

Everything here belongs to one person and is theirs alone; personal-data export includes it and erasing removes it.
"""
from __future__ import annotations

import json
import re

from . import access, db
from .web import bad, not_found

KINDS = ("doc", "item", "bom", "folder", "project", "change")
ACCENTS = ("blue", "teal", "green", "purple", "orange")
TEXT_SIZES = (90, 100, 115, 130)
MAX_FAVOURITES = 200
MAX_VIEWS = 100
MAX_PINS = 8
HASH = re.compile(r"^#/[A-Za-z0-9/_\-?=&%.,:+~*]{0,300}$")


def _resolve(kind: str, ref_id: int) -> dict | None:
    """The label, address and state of a favourite, or None when it is gone or not visible to this person."""
    sc = access.scope()
    if kind == "doc":
        d = db.one("SELECT id, filename, part_number, revision, state, folder_id, item_id, deleted FROM documents "
                   "WHERE id = ?", (ref_id,))
        if not d or d["deleted"] or not sc.can_see_document(d):
            return None
        return {"label": d["filename"], "sub": f"{d['part_number']} Rev {d['revision']}".strip(), "state": d["state"],
                "hash": f"#/doc/{d['id']}"}
    if kind == "item":
        it = db.one("SELECT id, part_number, description, revision, state FROM items WHERE id = ?", (ref_id,))
        if not it or not sc.can_see_item(it["id"]):
            return None
        return {"label": it["part_number"], "sub": it["description"], "state": it["state"], "hash": f"#/item/{it['id']}"}
    if kind == "bom":
        b = db.one("SELECT b.id, b.revision, b.state, b.item_id, i.part_number, i.description FROM boms b JOIN items i "
                   "ON i.id = b.item_id WHERE b.id = ?", (ref_id,))
        if not b or not sc.can_see_item(b["item_id"]):
            return None
        return {"label": f"BOM {b['part_number']} Rev {b['revision']}", "sub": b["description"], "state": b["state"],
                "hash": f"#/bom/{b['id']}"}
    if kind == "folder":
        f = db.one("SELECT id, name FROM folders WHERE id = ?", (ref_id,))
        if not f or not sc.can_see_folder(f["id"]):
            return None
        return {"label": f["name"], "sub": "Folder", "state": "", "hash": f"#/files/{f['id']}"}
    if kind == "project":
        if not sc.can_see_project(ref_id):
            return None
        p = db.one("SELECT id, code, name FROM projects WHERE id = ?", (ref_id,))
        if not p:
            return None
        return {"label": p["code"], "sub": p["name"], "state": "", "hash": f"#/project/{p['id']}"}
    if kind == "change":
        if sc.vendor:
            return None
        c = db.one("SELECT id, number, title, state FROM change_orders WHERE id = ?", (ref_id,))
        if not c:
            return None
        return {"label": c["number"], "sub": c["title"], "state": c["state"], "hash": f"#/change/{c['id']}"}
    return None


def favourites(user_id: int) -> list[dict]:
    out = []
    for r in db.rows("SELECT * FROM user_favourites WHERE user_id = ? ORDER BY id DESC", (user_id,)):
        info = _resolve(r["kind"], r["ref_id"])
        if info:
            out.append({"id": r["id"], "kind": r["kind"], "ref_id": r["ref_id"], **info})
    return out


def add_favourite(user_id: int, kind: str, ref_id) -> dict:
    if kind not in KINDS:
        raise bad("Only files, parts, BOMs, folders, projects and changes can be favourites.")
    try:
        ref_id = int(ref_id)
    except (TypeError, ValueError):
        raise bad("Say which record to add.") from None
    if not _resolve(kind, ref_id):
        raise not_found("This record does not exist, or you cannot see it.")
    if db.scalar("SELECT COUNT(*) FROM user_favourites WHERE user_id = ?", (user_id,)) >= MAX_FAVOURITES:
        raise bad(f"You have {MAX_FAVOURITES} favourites already. Remove some you no longer use first.")
    with db.tx():
        db.execute("INSERT INTO user_favourites(user_id, kind, ref_id, created_at) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(user_id, kind, ref_id) DO NOTHING", (user_id, kind, ref_id, db.now()))
    return next(f for f in favourites(user_id) if f["kind"] == kind and f["ref_id"] == ref_id)


def remove_favourite(user_id: int, kind: str, ref_id: int) -> None:
    with db.tx():
        db.execute("DELETE FROM user_favourites WHERE user_id = ? AND kind = ? AND ref_id = ?", (user_id, kind, ref_id))


def views(user_id: int) -> list[dict]:
    return db.rows("SELECT id, name, hash, created_at FROM saved_views WHERE user_id = ? ORDER BY name", (user_id,))


def save_view(user_id: int, name: str, hash_: str) -> dict:
    name = " ".join(str(name or "").split())[:80]
    hash_ = str(hash_ or "").strip()
    if not name:
        raise bad("Give the view a name, for example Released pump drawings.")
    if not HASH.match(hash_):
        raise bad("Only Kosha's own screens can be saved as views.")
    if db.scalar("SELECT COUNT(*) FROM saved_views WHERE user_id = ?", (user_id,)) >= MAX_VIEWS:
        raise bad(f"You have {MAX_VIEWS} saved views already. Delete some you no longer use first.")
    with db.tx():       # the same name again replaces that view: "save" twice is not an error
        db.execute("INSERT INTO saved_views(user_id, name, hash, created_at) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(user_id, name) DO UPDATE SET hash = excluded.hash", (user_id, name, hash_, db.now()))
    return db.one("SELECT id, name, hash, created_at FROM saved_views WHERE user_id = ? AND name = ?", (user_id, name))


def delete_view(user_id: int, view_id: int) -> None:
    if not db.one("SELECT 1 FROM saved_views WHERE id = ? AND user_id = ?", (view_id, user_id)):
        raise not_found("This view was deleted already.")
    with db.tx():
        db.execute("DELETE FROM saved_views WHERE id = ? AND user_id = ?", (view_id, user_id))


DEFAULT_PREFS = {"accent": "blue", "text_size": 100, "pins": []}     # "language" only once the person chooses one


def prefs(user_id: int) -> dict:
    raw = db.scalar("SELECT prefs_json FROM user_prefs WHERE user_id = ?", (user_id,))
    out = dict(DEFAULT_PREFS)
    out.update(json.loads(raw) if raw else {})
    return out


def save_prefs(user_id: int, body: dict) -> dict:
    cur = prefs(user_id)
    if "accent" in body:
        if body["accent"] not in ACCENTS:
            raise bad(f"Choose one of the accent colours: {', '.join(ACCENTS)}.")
        cur["accent"] = body["accent"]
    if "text_size" in body:
        try:
            size = int(body["text_size"])
        except (TypeError, ValueError):
            size = 0
        if size not in TEXT_SIZES:
            raise bad("Choose a text size of 90, 100, 115 or 130 %.")
        cur["text_size"] = size
    if "language" in body:
        from .locales import languages
        if body["language"] not in {lang["code"] for lang in languages()}:
            raise bad("Choose one of the languages Kosha has a translation for.")
        cur["language"] = body["language"]
    if "tour_done" in body:
        cur["tour_done"] = bool(body["tour_done"])
    if "pins" in body:
        pins = body["pins"]
        if not isinstance(pins, list):
            raise bad("Pinned pages must be a list.")
        clean = []
        for p in pins:
            if not isinstance(p, dict) or not HASH.match(str(p.get("hash") or "")):
                raise bad("Only Kosha's own screens can be pinned.")
            title = " ".join(str(p.get("title") or "").split())[:60] or p["hash"]
            if all(c["hash"] != p["hash"] for c in clean):
                clean.append({"hash": p["hash"], "title": title})
        if len(clean) > MAX_PINS:
            raise bad(f"Pin at most {MAX_PINS} pages. Unpin one first.")
        cur["pins"] = clean
    with db.tx():
        db.execute("INSERT INTO user_prefs(user_id, prefs_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(user_id) "
                   "DO UPDATE SET prefs_json = excluded.prefs_json, updated_at = excluded.updated_at",
                   (user_id, json.dumps(cur), db.now()))
    return cur


def everything(user_id: int) -> dict:
    return {"favourites": favourites(user_id), "views": views(user_id), "prefs": prefs(user_id)}


def forget(user_id: int) -> None:
    """Personal data erase: the person's favourites, views and preferences go (inside the caller's transaction)."""
    for table in ("user_favourites", "saved_views", "user_prefs"):
        db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
