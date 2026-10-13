# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core PLM rules: check-in/out, versions, revisions and lifecycle states."""
from __future__ import annotations

import json
import re
from typing import BinaryIO

from . import db, store, uploads
from .cad import process as cadproc
from .web import bad, conflict, forbidden, not_found

STATES = ("in_work", "in_review", "released", "obsolete")
STATE_LABEL = {
    "in_work": "In work",
    "in_review": "In review",
    "released": "Released",
    "obsolete": "Obsolete",
}
MODEL_TYPES = ("part", "assembly", "model")


# ------------------------------------------------------------- revisions --

def _letters() -> list[str]:
    skip = {s.strip().upper() for s in str(db.get_setting("revision_skip_letters") or "").split(",") if s.strip()}
    return [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in skip]


def next_revision(rev: str) -> str:
    rev = (rev or "").strip().upper()
    if rev.isdigit():
        return str(int(rev) + 1).zfill(len(rev))
    letters = _letters()
    if not rev or rev in ("-", "0"):
        return letters[0]
    digits = []
    for ch in rev:
        if ch not in letters:
            # unknown scheme (e.g. legacy "R3"): append a suffix letter
            return rev + letters[0]
        digits.append(letters.index(ch))
    i = len(digits) - 1
    while i >= 0:
        if digits[i] < len(letters) - 1:
            digits[i] += 1
            return "".join(letters[d] for d in digits)
        digits[i] = 0
        i -= 1
    return letters[0] + "".join(letters[d] for d in digits)


# ----------------------------------------------------------------- names --

def parse_name(filename: str) -> tuple[str, str]:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    pattern = db.get_setting("part_number_regex") or ""
    try:
        m = re.match(pattern, stem.strip(), re.I)
    except re.error:
        m = None
    if m:
        pn = (m.group(1) or "").upper()
        desc = (m.group(2) or "").strip() if m.lastindex and m.lastindex >= 2 else ""
        return pn, desc
    return "", stem.strip()


def clean_filename(name: str) -> str:
    """Drop any folder part and refuse names that are unsafe or impossible on Windows (see uploads.clean_filename)."""
    try:
        return uploads.clean_filename(name)
    except ValueError as e:
        raise bad(str(e))


# --------------------------------------------------------------- folders --

def clean_revision(rev) -> str:
    """Legacy revisions come as 'Rev. 3', 'R-2', '0', 'B'. Keep the short code."""
    r = str(rev or "").strip().upper()
    r = re.sub(r"^(REV(ISION)?|R)[\s.:\-]*(?=[A-Z0-9])", "", r)
    r = re.sub(r"[^A-Z0-9\-]", "", r)
    return r[:6]


def ensure_subfolder(parent_id: int, rel_path: str, user_id: int | None) -> int:
    """Create (if needed) a relative folder path under parent_id; returns the last folder id."""
    parent = parent_id
    for name in [p.strip() for p in re.split(r"[\\/]+", rel_path or "") if p.strip() and p.strip() != "."]:
        if name == ".." or any(c in name for c in ':*?"<>|'):
            raise bad(f"Invalid folder name: {name}")
        row = db.one("SELECT id FROM folders WHERE IFNULL(parent_id,0) = ? AND name = ? COLLATE NOCASE", (parent, name))
        if row:
            parent = row["id"]
        else:
            parent = db.insert("folders", {"parent_id": parent, "name": name, "created_by": user_id,
                                           "created_at": db.now()})
    return parent


def folder_or_404(folder_id: int) -> dict:
    f = db.one("SELECT * FROM folders WHERE id = ?", (folder_id,))
    if not f:
        raise not_found("Folder not found")
    return f


def folder_path(folder_id: int | None) -> list[dict]:
    chain = []
    seen = set()
    while folder_id and folder_id not in seen:
        seen.add(folder_id)
        f = db.one("SELECT id, parent_id, name FROM folders WHERE id = ?", (folder_id,))
        if not f:
            break
        chain.append(f)
        folder_id = f["parent_id"]
    return list(reversed(chain))


def root_folder_id() -> int:
    r = db.scalar("SELECT id FROM folders WHERE parent_id IS NULL ORDER BY id LIMIT 1")
    if r:
        return r
    return db.insert("folders", {"parent_id": None, "name": "Kosha", "created_at": db.now()})


def ensure_folder_path(path: str, user_id: int | None) -> int:
    parts = [p.strip() for p in re.split(r"[\\/]+", path or "") if p.strip()]
    if parts and parts[0].lower() == "kosha":
        parts = parts[1:]
    if not parts:
        raise bad("A Kosha folder is required")
    parent = root_folder_id()
    with db.tx():
        for name in parts:
            row = db.one("SELECT id FROM folders WHERE IFNULL(parent_id,0) = ? AND name = ? COLLATE NOCASE",
                         (parent or 0, name))
            if row:
                parent = row["id"]
            else:
                parent = db.insert("folders", {"parent_id": parent, "name": name, "created_by": user_id,
                                               "created_at": db.now()})
    return parent


# ------------------------------------------------------------- documents --

def doc_or_404(doc_id: int) -> dict:
    d = db.one("SELECT * FROM documents WHERE id = ? AND deleted = 0", (doc_id,))
    if not d:
        raise not_found("File not found")
    return d


def _event(doc: dict, to_state: str, user_id, comment="", co_id=None, from_state=None, revision=None):
    db.insert("lifecycle_events", {
        "document_id": doc["id"], "revision": revision or doc["revision"],
        "from_state": from_state if from_state is not None else doc["state"],
        "to_state": to_state, "user_id": user_id, "change_order_id": co_id,
        "comment": comment or "", "at": db.now(),
    })


def _link_item(part_number: str, description: str, dtype: str, user_id, material: str = "",
               extra: dict | None = None) -> int | None:
    if not part_number:
        return None
    item = db.one("SELECT * FROM items WHERE part_number = ? COLLATE NOCASE", (part_number,))
    now = db.now()
    if item:
        upd = {}
        if description and not item["description"]:
            upd["description"] = description
        if material and not item["material"]:
            upd["material"] = material
        if dtype == "assembly" and item["item_type"] == "part":
            upd["item_type"] = "assembly"
        if extra:
            props = json.loads(item["props_json"] or "{}")
            props.update({k: v for k, v in extra.items() if v not in (None, "")})
            upd["props_json"] = json.dumps(props)
            if extra.get("weight_kg") not in (None, ""):
                try:
                    upd["weight_kg"] = float(extra["weight_kg"])
                except (TypeError, ValueError):
                    pass
        if upd:
            upd["updated_at"] = now
            db.update("items", item["id"], upd)
        return item["id"]
    first = db.get_setting("first_revision") or "A"
    weight = None
    if extra and extra.get("weight_kg") not in (None, ""):
        try:
            weight = float(extra["weight_kg"])
        except (TypeError, ValueError):
            weight = None
    return db.insert("items", {
        "part_number": part_number, "description": description or "",
        "item_type": "assembly" if dtype == "assembly" else "part",
        "material": material or "", "revision": first, "state": "in_work",
        "weight_kg": weight,
        "props_json": json.dumps({k: v for k, v in (extra or {}).items() if v not in (None, "")}),
        "created_by": user_id, "created_at": now, "updated_at": now,
    })


def checkin(user: dict, folder_id: int, filename: str, stream: BinaryIO, comment: str = "",
            keep_checked_out: bool = False, props: dict | None = None,
            extra_preview: tuple[str, bytes] | None = None, extra_viewer: tuple[str, bytes] | None = None,
            extra_meta: dict | None = None, document_id: int | None = None) -> dict:
    """Add a new file or a new version of an existing one. Returns the document row."""
    props = {k: v for k, v in (props or {}).items() if v not in (None, "")}
    filename = clean_filename(filename)
    folder_or_404(folder_id)
    ext = cadproc.ext_of(filename)
    dtype = cadproc.doc_type(filename)

    if document_id:
        existing = doc_or_404(document_id)
        if cadproc.ext_of(existing["filename"]) != ext:
            raise bad(f"Choose a .{cadproc.ext_of(existing['filename'])} file to check in over {existing['filename']}.")
        filename = existing["filename"]
        folder_id = existing["folder_id"]
    else:
        existing = db.one("SELECT * FROM documents WHERE folder_id = ? AND filename = ? COLLATE NOCASE AND deleted = 0",
                          (folder_id, filename))

    if existing:
        if existing["checked_out_by"] and existing["checked_out_by"] != user["id"]:
            who = db.scalar("SELECT full_name FROM users WHERE id = ?", (existing["checked_out_by"],))
            raise conflict(f"{filename} is checked out by {who}.", "checked_out_by_other")
        if existing["state"] != "in_work":
            raise conflict(f"{filename} is {STATE_LABEL[existing['state']].lower()} at revision {existing['revision']}. "
                           "Revise it before checking in changes.", "not_in_work")
        if existing["checked_out_by"] != user["id"]:
            raise conflict(f"Check out {filename} before checking in a new version.", "not_checked_out")

    sha, size = store.put_stream(stream)

    if existing and existing["latest_version"]:
        last = db.one("SELECT * FROM versions WHERE document_id = ? AND version = ?",
                      (existing["id"], existing["latest_version"]))
        if last and last["blob_sha"] == sha and not props and not extra_preview and not extra_viewer:
            with db.tx():
                if not keep_checked_out:
                    db.update("documents", existing["id"], {"checked_out_by": None, "checked_out_at": None,
                                                             "updated_at": db.now()})
                db.audit(user["id"], "checkin_unchanged", "document", existing["id"], filename)
            d = doc_or_404(existing["id"])
            d["unchanged"] = True
            return d

    data = store.path_for(sha).read_bytes()
    result = cadproc.process(filename, data)
    meta = dict(result["meta"])
    if extra_meta:
        meta.update(extra_meta)
    preview = extra_preview or result["preview"]
    viewer = extra_viewer or result["viewer"]
    signature = result["signature"]
    if extra_viewer and extra_viewer[0] == "stl":
        from .cad import mesh
        st = mesh.stats(extra_viewer[1])
        if st:
            meta["mesh"] = st
            signature = cadproc.mesh_signature(st)
            if not extra_preview:
                svg = cadproc.mesh_to_svg(extra_viewer[1])
                if svg:
                    preview = ("svg", svg.encode())

    tb = meta.get("title_block") or {}
    pn_name, desc_name = parse_name(filename)
    part_number = (props.get("part_number") or tb.get("part_number") or
                   (existing["part_number"] if existing else "") or pn_name).strip().upper()
    description = (props.get("description") or tb.get("description") or
                   (existing["description"] if existing else "") or desc_name).strip()
    material = (props.get("material") or tb.get("material") or "").strip()

    prev_sha = prev_kind = view_sha = view_kind = None
    if preview:
        prev_kind = preview[0]
        prev_sha, _ = store.put_bytes(preview[1])
    if viewer:
        view_kind = viewer[0]
        view_sha, _ = store.put_bytes(viewer[1]) if viewer[1] != data else (sha, size)

    now = db.now()
    extra_item = {k: props.get(k) for k in ("weight_kg", "finish", "vendor", "standard") if props.get(k)}
    with db.tx():
        item_id = _link_item(part_number, description, dtype, user["id"], material, extra_item)
        if existing:
            doc_id = existing["id"]
            version = existing["latest_version"] + 1
            revision = existing["revision"]
            db.update("documents", doc_id, {
                "latest_version": version, "part_number": part_number, "description": description,
                "item_id": item_id or existing["item_id"],
                "checked_out_by": user["id"] if keep_checked_out else None,
                "checked_out_at": existing["checked_out_at"] if keep_checked_out else None,
                "updated_at": now,
            })
        else:
            revision = clean_revision(props.get("revision") or
                                      (tb.get("revision") if props.get("use_title_block_revision") else ""))
            revision = revision or db.get_setting("first_revision") or "A"
            version = 1
            doc_id = db.insert("documents", {
                "folder_id": folder_id, "filename": filename, "ext": ext, "doc_type": dtype,
                "item_id": item_id, "part_number": part_number, "description": description,
                "revision": revision, "state": "in_work",
                "checked_out_by": user["id"] if keep_checked_out else None,
                "checked_out_at": now if keep_checked_out else None,
                "latest_version": 1, "created_by": user["id"], "created_at": now, "updated_at": now,
            })
            db.insert("lifecycle_events", {
                "document_id": doc_id, "revision": revision, "from_state": None, "to_state": "in_work",
                "user_id": user["id"], "comment": "Created", "at": now,
            })
        db.insert("versions", {
            "document_id": doc_id, "version": version, "revision": revision, "blob_sha": sha, "size": size,
            "comment": comment or "", "created_by": user["id"], "created_at": now,
            "preview_kind": prev_kind, "preview_sha": prev_sha, "viewer_kind": view_kind, "viewer_sha": view_sha,
            "meta_json": json.dumps(meta), "signature_json": json.dumps(signature) if signature else None,
        })
        db.audit(user["id"], "checkin", "document", doc_id, f"{filename} v{version} rev {revision}")
    return doc_or_404(doc_id)


def checkout(user: dict, doc_id: int) -> dict:
    with db.tx():
        d = doc_or_404(doc_id)
        if d["checked_out_by"] == user["id"]:
            return d
        if d["checked_out_by"]:
            who = db.scalar("SELECT full_name FROM users WHERE id = ?", (d["checked_out_by"],))
            raise conflict(f"Already checked out by {who}.", "checked_out_by_other")
        if d["state"] == "released":
            raise conflict(f"Revision {d['revision']} is released and locked. Use Revise to start revision "
                           f"{next_revision(d['revision'])}.", "released_locked")
        if d["state"] == "in_review":
            raise conflict("This file is waiting for review. Ask the reviewer to send it back to In work first.",
                           "in_review")
        if d["state"] == "obsolete":
            raise conflict("Obsolete files cannot be changed.", "obsolete")
        db.update("documents", doc_id, {"checked_out_by": user["id"], "checked_out_at": db.now(), "updated_at": db.now()})
        db.audit(user["id"], "checkout", "document", doc_id, d["filename"])
    return doc_or_404(doc_id)


def undo_checkout(user: dict, doc_id: int, is_admin: bool = False) -> dict:
    with db.tx():
        d = doc_or_404(doc_id)
        if not d["checked_out_by"]:
            return d
        if d["checked_out_by"] != user["id"] and not is_admin:
            raise forbidden("Only the person who checked it out (or an admin) can undo the check-out.")
        db.update("documents", doc_id, {"checked_out_by": None, "checked_out_at": None, "updated_at": db.now()})
        db.audit(user["id"], "undo_checkout", "document", doc_id, d["filename"])
    return doc_or_404(doc_id)


ALLOWED = {
    ("in_work", "in_review"): "engineer",
    ("in_review", "in_work"): "engineer",   # submitter can pull back; approvers reject
    ("in_review", "released"): "approver",
    ("in_work", "released"): "approver",
    ("released", "obsolete"): "approver",
}


def set_state(user: dict, doc_id: int, to: str, comment: str = "", co_id: int | None = None,
              via_change: bool = False) -> dict:
    from .web import ROLE_LEVEL
    if to not in STATES:
        raise bad("Unknown state")
    released_doc = None
    with db.tx():
        d = doc_or_404(doc_id)
        frm = d["state"]
        if frm == to:
            return d
        need = ALLOWED.get((frm, to))
        if not need:
            raise conflict(f"Cannot move from {STATE_LABEL[frm]} to {STATE_LABEL[to]}.", "bad_transition")
        if ROLE_LEVEL[user["role"]] < ROLE_LEVEL[need]:
            raise forbidden(f"Moving to {STATE_LABEL[to]} needs the {need} role.")
        if d["checked_out_by"]:
            raise conflict("Check the file in before changing its state.", "checked_out")
        if to == "released" and not via_change and db.get_setting("require_eco_for_release"):
            raise conflict("Your settings require a change order to release files.", "eco_required")
        if frm == "in_review" and to == "in_work" and ROLE_LEVEL[user["role"]] < ROLE_LEVEL["approver"]:
            submitter = db.scalar("SELECT user_id FROM lifecycle_events WHERE document_id = ? AND to_state = 'in_review' "
                                  "ORDER BY id DESC LIMIT 1", (doc_id,))
            if submitter != user["id"]:
                raise forbidden("Only the submitter or an approver can send this back to In work.")
        db.update("documents", doc_id, {"state": to, "updated_at": db.now()})
        _event(d, to, user["id"], comment, co_id, from_state=frm)
        db.audit(user["id"], f"state_{to}", "document", doc_id, f"{d['filename']} rev {d['revision']}")
        if to == "released":
            released_doc = d
            _sync_item_on_release(d)
            from .diff import snapshots
            snapshots.on_release(d)   # item properties as released, for the measured change list
        if to == "obsolete" and d["item_id"]:
            others = db.scalar("SELECT COUNT(*) FROM documents WHERE item_id = ? AND deleted = 0 AND state != 'obsolete' "
                               "AND id != ?", (d["item_id"], doc_id))
            if not others:
                db.update("items", d["item_id"], {"state": "obsolete", "updated_at": db.now()})
    if released_doc and released_doc["item_id"]:
        from . import erp
        ref = db.scalar("SELECT number FROM change_orders WHERE id = ?", (co_id,)) if co_id else ""
        erp.on_release(released_doc["item_id"], user["id"], ref or f"{released_doc['filename']} rev {released_doc['revision']}")
        from .addins import registry
        registry.emit("revision.released", {"document_id": doc_id, "item_id": released_doc["item_id"],
                                            "filename": released_doc["filename"], "revision": released_doc["revision"],
                                            "part_number": released_doc.get("part_number"), "user_id": user["id"]})
    return doc_or_404(doc_id)


def _sync_item_on_release(d: dict) -> None:
    if not d["item_id"]:
        return
    item = db.one("SELECT * FROM items WHERE id = ?", (d["item_id"],))
    if not item:
        return
    has_model = db.scalar("SELECT COUNT(*) FROM documents WHERE item_id = ? AND deleted = 0 AND doc_type IN "
                          "('part','assembly','model')", (d["item_id"],))
    if d["doc_type"] in MODEL_TYPES or not has_model:
        db.update("items", item["id"], {"revision": d["revision"], "state": "released", "updated_at": db.now()})
        bom = db.one("SELECT * FROM boms WHERE item_id = ? AND revision = ?", (item["id"], d["revision"]))
        if bom and bom["state"] != "released":
            db.update("boms", bom["id"], {"state": "released", "updated_at": db.now()})


def revise(user: dict, doc_id: int, comment: str = "", co_id: int | None = None) -> dict:
    with db.tx():
        d = doc_or_404(doc_id)
        if d["state"] != "released":
            raise conflict("Only released files can be revised. This one is still "
                           f"{STATE_LABEL[d['state']].lower()} and can be edited directly.", "not_released")
        new_rev = next_revision(d["revision"])
        db.update("documents", doc_id, {"revision": new_rev, "state": "in_work", "updated_at": db.now()})
        _event(d, "in_work", user["id"], comment or f"Revised from {d['revision']}", co_id, from_state="released",
               revision=new_rev)
        db.audit(user["id"], "revise", "document", doc_id, f"{d['filename']} {d['revision']} -> {new_rev}")
        # carry the BOM forward so engineers edit rather than rebuild it
        if d["item_id"] and d["doc_type"] == "assembly":
            old = db.one("SELECT * FROM boms WHERE item_id = ? AND revision = ?", (d["item_id"], d["revision"]))
            exists = db.scalar("SELECT id FROM boms WHERE item_id = ? AND revision = ?", (d["item_id"], new_rev))
            if old and not exists:
                now = db.now()
                nb = db.insert("boms", {"item_id": d["item_id"], "revision": new_rev, "source": old["source"],
                                        "state": "draft", "note": f"Copied from revision {d['revision']}",
                                        "created_by": user["id"], "created_at": now, "updated_at": now})
                db.execute("INSERT INTO bom_lines (bom_id, line_no, child_item_id, qty, uom, find_number, note) "
                           "SELECT ?, line_no, child_item_id, qty, uom, find_number, note FROM bom_lines WHERE bom_id = ?",
                           (nb, old["id"]))
    return doc_or_404(doc_id)


def delete(user: dict, doc_id: int, is_approver: bool) -> None:
    with db.tx():
        d = doc_or_404(doc_id)
        ever_released = db.scalar("SELECT COUNT(*) FROM lifecycle_events WHERE document_id = ? AND to_state = 'released'",
                                  (doc_id,))
        if ever_released:
            raise conflict("This file has been released before, so it can't be deleted. Make it obsolete instead.",
                           "was_released")
        if d["checked_out_by"] and d["checked_out_by"] != user["id"]:
            raise conflict("Someone else has this file checked out.", "checked_out_by_other")
        if d["created_by"] != user["id"] and not is_approver:
            raise forbidden("Only the creator or an approver can delete this file.")
        db.update("documents", doc_id, {"deleted": 1, "updated_at": db.now(), "checked_out_by": None})
        db.audit(user["id"], "delete", "document", doc_id, d["filename"])


# ------------------------------------------------------------ serialise --

def doc_summary(d: dict, users: dict | None = None) -> dict:
    users = users if users is not None else {}
    out = {
        "id": d["id"], "folder_id": d["folder_id"], "filename": d["filename"], "ext": d["ext"],
        "doc_type": d["doc_type"], "item_id": d["item_id"], "part_number": d["part_number"],
        "description": d["description"], "revision": d["revision"], "state": d["state"],
        "state_label": STATE_LABEL.get(d["state"], d["state"]),
        "checked_out_by": d["checked_out_by"], "checked_out_at": d["checked_out_at"],
        "checked_out_name": users.get(d["checked_out_by"]) if d["checked_out_by"] else None,
        "version": d["latest_version"], "updated_at": d["updated_at"], "created_at": d["created_at"],
        "created_by": d["created_by"],
        "type_label": cadproc.CAD_LABEL.get(d["ext"], d["ext"].upper() + " file" if d["ext"] else "File"),
    }
    return out


def user_names() -> dict:
    return {r["id"]: r["full_name"] for r in db.rows("SELECT id, full_name FROM users")}
