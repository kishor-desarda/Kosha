# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy design (spec 6.8): start a new machine from an existing one.

An assembly (with its sub-assemblies and drawings, if wanted) is copied to a folder under new part
numbers. Each item gets an action:

  copy     a new item with a new number, its files and its BOM (lines pointing at the new items)
  reuse    the existing item is used as is (default for standard and bought-out parts)
  replace  every BOM line pointing at it points at another existing item instead

Rules that keep data safe:

* All records are written in **one transaction**, together with the ``copy_jobs`` row; a crash leaves
  either the whole copy or nothing.
* The client sends an **idempotency key**. Running the same copy again (after a time-out or a crash)
  returns the stored result and creates nothing new; the same key with different contents is refused.
* **No file bytes are duplicated**: a copied version points at the same stored content (SHA-256) as the
  original. SolidWorks files keep their internal references to the original files; only the SolidWorks
  add-in can rewrite those, so the preview says so.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import boms, db, lifecycle
from .cad import process as cadproc
from .web import Req, bad, conflict, not_found

ACTIONS = ("copy", "reuse", "replace")
SOLIDWORKS = {"sldprt", "sldasm", "slddrw"}
CAD_DRAWINGS = {"dxf", "dwg"}
PREFIX_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/]{0,20}$")
PN_RE = re.compile(r"^[A-Z0-9][A-Z0-9 .\-_/]{0,63}$")
MAX_DEPTH = 12
SW_NOTE = ("SolidWorks files keep their internal references to the original part files: the server cannot "
           "rewrite them. Open the copied assembly in SolidWorks with the Kosha add-in to update references.")
TB_NOTE = ("The title block inside copied DXF/DWG drawings still shows the old part number. Check each drawing out, "
           "update its title block and check it in.")
STORE_NOTE = "No file content is duplicated: the copies point to the same stored file versions as the originals."


# --------------------------------------------------------------- numbering --

def allocate_number(user: dict, prefix: str, width: int = 3) -> str:
    """The one place copy design gets a new part number: today the /api/numbering/next logic.
    Swap the body for the numbering service when it lands."""
    from .api.core import next_number
    return next_number(Req(None, user, {}, {}, {"prefix": prefix, "width": width}))["part_number"]


def peek_numbers(prefix: str, width: int, count: int, reserved: set[str]) -> list[str]:
    """The numbers ``allocate_number`` would hand out next, without using them up."""
    row = db.one("SELECT next_value, width FROM numbering WHERE prefix = ?", (prefix,))
    n = row["next_value"] if row else 1
    width = row["width"] if row else width
    out: list[str] = []
    while len(out) < count:
        pn = f"{prefix}{n:0{width}d}"
        if pn.upper() not in reserved and not db.scalar("SELECT 1 FROM items WHERE part_number = ?", (pn,)):
            out.append(pn)
        n += 1
    return out


# --------------------------------------------------------------- structure --

def _item(item_id: int) -> dict:
    it = db.one("SELECT * FROM items WHERE id = ?", (item_id,))
    if not it:
        raise not_found("Part not found")
    return it


def _files(item_id: int) -> list[dict]:
    rows = db.rows("SELECT d.*, v.revision AS v_revision, v.version AS v_version FROM documents d JOIN versions v "
                   "ON v.document_id = d.id AND v.version = d.latest_version WHERE d.item_id = ? AND d.deleted = 0 "
                   "ORDER BY d.doc_type, d.filename", (item_id,))
    out = []
    for d in rows:
        kind = "drawing" if d["doc_type"] in ("drawing", "pdf") else "model" if d["doc_type"] in lifecycle.MODEL_TYPES \
            else "other"
        out.append({"document_id": d["id"], "filename": d["filename"], "ext": d["ext"], "doc_type": d["doc_type"],
                    "kind": kind, "revision": d["v_revision"], "version": d["v_version"], "state": d["state"],
                    "folder_id": d["folder_id"]})
    return out


def _in_standard_folder(files: list[dict]) -> bool:
    for f in files:
        if any("standard" in x["name"].lower() for x in lifecycle.folder_path(f["folder_id"])):
            return True
    return False


def structure(root_item_id: int, include_subassemblies: bool) -> list[dict]:
    """Every item of the assembly once, in BOM order (root first)."""
    rows: dict[int, dict] = {}

    def visit(item_id: int, level: int, parent: str | None, qty: float, path: tuple):
        if item_id in rows:
            r = rows[item_id]
            if parent and parent not in r["parents"]:
                r["parents"].append(parent)
            r["qty"] += qty
            return
        it = _item(item_id)
        bom = boms.bom_for_item(item_id)
        files = _files(item_id)
        r = {"item_id": item_id, "part_number": it["part_number"], "description": it["description"],
             "item_type": it["item_type"], "make_buy": it["make_buy"], "state": it["state"], "revision": it["revision"],
             "item_group": it["item_group"], "standard_item_id": it["standard_item_id"], "level": level,
             "parents": [parent] if parent else [], "qty": qty, "bom_id": bom["id"] if bom else None,
             "files": files, "standard_folder": _in_standard_folder(files), "expanded": False}
        rows[item_id] = r
        if bom and level < MAX_DEPTH and item_id not in path and (level == 0 or include_subassemblies):
            r["expanded"] = True
            for ln in boms.lines(bom["id"]):
                visit(ln["item_id"], level + 1, it["part_number"], float(ln["qty"] or 0), path + (item_id,))

    visit(root_item_id, 0, None, 1.0, ())
    return list(rows.values())


def default_action(r: dict, include_subassemblies: bool) -> tuple[str, str]:
    if r["level"] == 0:
        return "copy", "The assembly you chose"
    if r["standard_item_id"]:
        return "replace", "Duplicate of a standard part: use the standard part"
    pn = (r["part_number"] or "").upper()
    if pn.startswith("STD") or (r["item_group"] or "").lower() == "standard parts" or r["standard_folder"]:
        return "reuse", "Standard part"
    if r["make_buy"] == "buy":
        return "reuse", "Bought-out part"
    if r["bom_id"] and not include_subassemblies:
        return "reuse", "Sub-assembly kept as is (sub-assemblies are not copied)"
    return "copy", "Made part"


def new_filename(filename: str, old_pn: str, new_pn: str) -> str:
    low, old = filename.lower(), (old_pn or "").lower()
    if old and old in low:
        i = low.index(old)
        return filename[:i] + new_pn + filename[i + len(old):]
    return f"{new_pn} {filename}"


def new_description(desc: str, opts: dict) -> str:
    d = desc or ""
    if opts["replace_from"]:
        d = re.sub(re.escape(opts["replace_from"]), opts["replace_to"], d, flags=re.I)
    return f"{opts['desc_prefix']}{d}{opts['desc_suffix']}".strip()[:200]


# ------------------------------------------------------------------- plan --

def _options(body: dict) -> dict:
    b = body if isinstance(body, dict) else {}
    try:
        width = int(b.get("width") or 3)
    except (TypeError, ValueError):
        raise bad("Number width must be a whole number.")
    return {
        "document_id": b.get("document_id"), "item_id": b.get("item_id"),
        "include_drawings": b.get("include_drawings", True) not in (False, "0", "false", 0),
        "include_subassemblies": b.get("include_subassemblies", True) not in (False, "0", "false", 0),
        "prefix": str(b.get("prefix") or "").strip().upper(), "width": max(1, min(width, 8)),
        "desc_prefix": str(b.get("desc_prefix") or ""), "desc_suffix": str(b.get("desc_suffix") or ""),
        "replace_from": str(b.get("replace_from") or ""), "replace_to": str(b.get("replace_to") or ""),
        "folder_id": b.get("folder_id"), "new_folder": str(b.get("new_folder") or "").strip(),
        "rows": {int(r["item_id"]): r for r in (b.get("rows") or []) if isinstance(r, dict) and r.get("item_id")},
    }


def _root_item(opts: dict) -> int:
    if opts["item_id"]:
        return _item(int(opts["item_id"]))["id"]
    if opts["document_id"]:
        d = lifecycle.doc_or_404(int(opts["document_id"]))
        if not d["item_id"]:
            raise bad(f"{d['filename']} has no part number, so Kosha cannot find what belongs to it. "
                      "Give it a part number first.")
        return d["item_id"]
    raise bad("Choose the assembly to copy.")


def plan(user: dict, body: dict) -> dict:
    """The review table: every item with its action, proposed number, description and files; plus problems."""
    opts = _options(body)
    root = _root_item(opts)
    rows = structure(root, opts["include_subassemblies"])
    problems: list[str] = []
    folder = None
    if opts["folder_id"]:
        folder = db.one("SELECT id, name FROM folders WHERE id = ?", (int(opts["folder_id"]),))
        if not folder:
            problems.append("The target folder does not exist any more. Choose another one.")
    else:
        problems.append("Choose the folder the copy goes into.")
    if opts["new_folder"] and any(c in opts["new_folder"] for c in '\\/:*?"<>|'):
        problems.append("The new folder name can't use \\ / : * ? \" < > |")

    reserved: set[str] = set()
    for r in rows:
        choice = opts["rows"].get(r["item_id"], {})
        action, reason = default_action(r, opts["include_subassemblies"])
        r["default_action"], r["reason"] = action, reason
        r["replace_item_id"] = r["standard_item_id"] if action == "replace" else None
        if choice.get("action"):
            if choice["action"] not in ACTIONS:
                problems.append(f"{r['part_number']}: unknown action {choice['action']!r}.")
            else:
                action = choice["action"]
                if action == "replace":
                    r["replace_item_id"] = choice.get("replace_item_id") or r["replace_item_id"]
        r["action"] = action
        if r["level"] == 0 and action != "copy":
            problems.append(f"{r['part_number']} is the assembly you chose, so it is always copied.")
            r["action"] = action = "copy"
        if action == "replace":
            rep = db.one("SELECT id, part_number, description FROM items WHERE id = ?", (int(r["replace_item_id"] or 0),))
            if not rep:
                problems.append(f"{r['part_number']}: choose the part that replaces it.")
            elif rep["id"] == r["item_id"]:
                problems.append(f"{r['part_number']} cannot replace itself.")
            r["replace_part_number"] = rep["part_number"] if rep else ""
            r["replace_description"] = rep["description"] if rep else ""
        explicit = str(choice.get("new_part_number") or "").strip().upper()
        r["new_part_number"] = explicit if action == "copy" and explicit else ""
        r["number_given"] = bool(r["new_part_number"])
        if r["new_part_number"]:
            if not PN_RE.match(r["new_part_number"]):
                problems.append(f"{r['new_part_number']} is not a valid part number (letters, digits, - . _ /).")
            elif db.scalar("SELECT 1 FROM items WHERE part_number = ?", (r["new_part_number"],)):
                problems.append(f"{r['new_part_number']} already exists. Choose another number for {r['part_number']}.")
            elif r["new_part_number"] in reserved:
                problems.append(f"{r['new_part_number']} is used for two parts in this copy.")
            reserved.add(r["new_part_number"])
        r["new_description"] = (str(choice["new_description"]).strip()[:200] if choice.get("new_description")
                                else new_description(r["description"], opts)) if action == "copy" else ""
    auto = [r for r in rows if r["action"] == "copy" and not r["new_part_number"]]
    if auto:
        if not opts["prefix"]:
            problems.append("Type the prefix for the new part numbers, for example MX-200-.")
        elif not PREFIX_RE.match(opts["prefix"]):
            problems.append("The prefix may use letters, digits and dashes, for example MX-200-.")
        else:
            for r, pn in zip(auto, peek_numbers(opts["prefix"], opts["width"], len(auto), reserved)):
                r["new_part_number"] = pn
    # files and the names they get
    names_taken: set[str] = set()
    if folder and not opts["new_folder"]:
        names_taken = {x["filename"].lower() for x in db.rows("SELECT filename FROM documents WHERE folder_id = ? "
                                                               "AND deleted = 0", (folder["id"],))}
    elif folder and opts["new_folder"]:
        sub = db.one("SELECT id FROM folders WHERE parent_id = ? AND LOWER(name) = LOWER(?)", (folder["id"], opts["new_folder"]))
        if sub:
            names_taken = {x["filename"].lower() for x in db.rows("SELECT filename FROM documents WHERE folder_id = ? "
                                                                   "AND deleted = 0", (sub["id"],))}
    planned: set[str] = set()
    notes: list[str] = []
    for r in rows:
        for f in r["files"]:
            f["copy"] = r["action"] == "copy" and (f["kind"] != "drawing" or opts["include_drawings"])
            f["new_filename"] = new_filename(f["filename"], r["part_number"], r["new_part_number"]) if f["copy"] else ""
            if not f["copy"]:
                continue
            key = f["new_filename"].lower()
            if key in names_taken:
                problems.append(f"{f['new_filename']} already exists in the target folder.")
            elif key in planned:
                problems.append(f"Two copied files would both be called {f['new_filename']}.")
            planned.add(key)
            if f["ext"] in SOLIDWORKS and SW_NOTE not in notes:
                notes.append(SW_NOTE)
            if f["ext"] in CAD_DRAWINGS and TB_NOTE not in notes:
                notes.append(TB_NOTE)
    if any(f["copy"] for r in rows for f in r["files"]):
        notes.append(STORE_NOTE)
    counts = {a: sum(1 for r in rows if r["action"] == a) for a in ACTIONS}
    counts["files"] = sum(1 for r in rows for f in r["files"] if f["copy"])
    return {"root_item_id": root, "rows": rows, "problems": list(dict.fromkeys(problems)), "notes": notes,
            "counts": counts, "folder": folder, "options": {k: v for k, v in opts.items() if k != "rows"}}


# ---------------------------------------------------------------- execute --

def request_hash(body: dict) -> str:
    clean = {k: v for k, v in (body or {}).items() if k != "key"}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()


def job(key: str) -> dict | None:
    row = db.one("SELECT * FROM copy_jobs WHERE idem_key = ?", (key,))
    if not row:
        return None
    return {"key": row["idem_key"], "status": row["status"], "created_at": row["created_at"],
            "result": json.loads(row["result_json"] or "{}")}


def execute(user: dict, body: dict) -> dict:
    key = str((body or {}).get("key") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9\-_:.]{8,100}", key):
        raise bad("A copy key (8 to 100 letters, digits or dashes) is required so a repeated request cannot copy twice.")
    rh = request_hash(body)
    done = db.one("SELECT request_hash, result_json FROM copy_jobs WHERE idem_key = ?", (key,))
    if done:
        return _replay(done, rh)
    p = plan(user, body)
    if p["problems"]:
        raise conflict("Nothing was copied: " + " ".join(p["problems"]), "copy_problems", problems=p["problems"])
    with db.tx():
        done = db.one("SELECT request_hash, result_json FROM copy_jobs WHERE idem_key = ?", (key,))
        if done:   # the same copy finished in another request while this one waited
            return _replay(done, rh)
        return _write(user, key, rh, body, p)


def _replay(row: dict, rh: str) -> dict:
    if row["request_hash"] != rh:
        raise conflict("This copy key was already used for a different copy. Start the copy again from the beginning.",
                       "copy_key_reused")
    out = json.loads(row["result_json"])
    out["replayed"] = True
    return out


def _write(user: dict, key: str, rh: str, body: dict, p: dict) -> dict:
    now = db.now()
    opts = p["options"]
    first = db.get_setting("first_revision") or "A"
    folder_id = p["folder"]["id"]
    if opts["new_folder"]:
        folder_id = lifecycle.ensure_subfolder(folder_id, opts["new_folder"], user["id"])
    job_id = db.insert("copy_jobs", {"idem_key": key, "request_hash": rh, "request_json": json.dumps(body, default=str),
                                     "result_json": "{}", "status": "done", "root_item_id": p["root_item_id"],
                                     "folder_id": folder_id, "created_by": user["id"], "created_at": now})

    def link(kind, src, rev, dst):
        db.insert("copy_links", {"job_id": job_id, "kind": kind, "source_id": src, "source_revision": rev or "",
                                 "copy_id": dst})

    rows = p["rows"]
    copies = [r for r in rows if r["action"] == "copy"]
    # numbers: the ones typed in are kept; the others come from the numbering, in table order
    reserved = {r["new_part_number"] for r in copies if r["number_given"]}
    for r in copies:
        if r["number_given"]:
            continue
        pn = allocate_number(user, opts["prefix"], opts["width"])
        while pn.upper() in reserved:
            pn = allocate_number(user, opts["prefix"], opts["width"])
        r["new_part_number"] = pn
        reserved.add(pn.upper())
    # file names follow the numbers actually given (they match the preview unless someone took numbers meanwhile)
    taken = {x["filename"].lower() for x in db.rows("SELECT filename FROM documents WHERE folder_id = ? AND deleted = 0",
                                                     (folder_id,))}
    for r in copies:
        for f in r["files"]:
            if f["copy"]:
                f["new_filename"] = new_filename(f["filename"], r["part_number"], r["new_part_number"])
                if f["new_filename"].lower() in taken:
                    raise conflict(f"Nothing was copied: {f['new_filename']} already exists in the target folder.",
                                   "copy_problems", problems=[f"{f['new_filename']} already exists in the target folder."])
                taken.add(f["new_filename"].lower())
    item_map: dict[int, int] = {}
    pn_map: dict[str, str] = {}
    for r in rows:
        if r["action"] == "replace":
            item_map[r["item_id"]] = int(r["replace_item_id"])
            pn_map[r["part_number"].upper()] = r["replace_part_number"]
    created = []
    for r in copies:
        src = _item(r["item_id"])
        props = json.loads(src["props_json"] or "{}")
        props["copied_from"] = f"{src['part_number']} rev {src['revision']}"
        new_id = db.insert("items", {
            "part_number": r["new_part_number"], "description": r["new_description"], "item_type": src["item_type"],
            "material": src["material"], "uom": src["uom"], "make_buy": src["make_buy"], "weight_kg": src["weight_kg"],
            "item_group": src["item_group"], "revision": first, "state": "in_work", "props_json": json.dumps(props),
            "created_by": user["id"], "created_at": now, "updated_at": now})
        item_map[r["item_id"]] = new_id
        pn_map[src["part_number"].upper()] = r["new_part_number"]
        link("item", src["id"], src["revision"], new_id)
        db.audit(user["id"], "copy_design", "item", new_id, f"{r['new_part_number']} copied from {src['part_number']} "
                                                             f"rev {src['revision']}")
        created.append({"source": {"item_id": src["id"], "part_number": src["part_number"], "revision": src["revision"]},
                        "item": {"id": new_id, "part_number": r["new_part_number"], "description": r["new_description"]},
                        "documents": [], "bom_id": None, "row": r})
    name_map: dict[str, str] = {f["filename"].lower(): f["new_filename"] for r in copies for f in r["files"] if f["copy"]}
    for c in created:
        r = c.pop("row")
        for f in r["files"]:
            if not f["copy"]:
                continue
            c["documents"].append(_copy_file(user, f, r, c["item"], folder_id, first, pn_map, name_map, now, link))
        if r["bom_id"]:
            c["bom_id"] = _copy_bom(user, r, c["item"], first, item_map, now, link)
    result = {
        "key": key, "job_id": job_id,
        "folder": {"id": folder_id, "path": " › ".join(f["name"] for f in lifecycle.folder_path(folder_id))},
        "created": created,
        "reused": [{"item_id": r["item_id"], "part_number": r["part_number"], "description": r["description"]}
                   for r in rows if r["action"] == "reuse"],
        "replaced": [{"item_id": r["item_id"], "part_number": r["part_number"], "by_item_id": item_map[r["item_id"]],
                      "by_part_number": r["replace_part_number"]} for r in rows if r["action"] == "replace"],
        "notes": p["notes"], "replayed": False,
    }
    db.update("copy_jobs", job_id, {"result_json": json.dumps(result, default=str)})
    db.audit(user["id"], "copy_design", "copy_job", job_id, f"{len(created)} parts copied to folder {folder_id}")
    return result


def _copy_file(user, f, r, new_item, folder_id, first, pn_map, name_map, now, link) -> dict:
    src = db.one("SELECT * FROM documents WHERE id = ?", (f["document_id"],))
    ver = db.one("SELECT * FROM versions WHERE document_id = ? AND version = ?", (src["id"], src["latest_version"]))
    meta = json.loads(ver["meta_json"] or "{}")
    meta["copied_from"] = {"document_id": src["id"], "filename": src["filename"], "version": ver["version"],
                           "revision": ver["revision"], "part_number": src["part_number"]}
    if isinstance(meta.get("structure"), list):
        for comp in meta["structure"]:
            if not isinstance(comp, dict):
                continue
            pn = str(comp.get("part_number") or "").upper()
            if pn in pn_map:
                comp["part_number"] = pn_map[pn]
            fn = str(comp.get("file") or "").lower()
            if fn in name_map:
                comp["file"] = name_map[fn]
    if isinstance(meta.get("references"), list):
        meta["references"] = [name_map.get(str(x).lower(), x) for x in meta["references"]]
    doc_id = db.insert("documents", {
        "folder_id": folder_id, "filename": f["new_filename"], "ext": src["ext"],
        "doc_type": cadproc.doc_type(f["new_filename"]), "item_id": new_item["id"], "part_number": new_item["part_number"],
        "description": new_item["description"], "revision": first, "state": "in_work", "latest_version": 1,
        "created_by": user["id"], "created_at": now, "updated_at": now})
    origin = f"{src['part_number'] or src['filename']} rev {ver['revision']}"
    db.insert("versions", {
        "document_id": doc_id, "version": 1, "revision": first, "blob_sha": ver["blob_sha"], "size": ver["size"],
        "comment": f"Copied from {src['filename']} rev {ver['revision']}", "created_by": user["id"], "created_at": now,
        "preview_kind": ver["preview_kind"], "preview_sha": ver["preview_sha"], "viewer_kind": ver["viewer_kind"],
        "viewer_sha": ver["viewer_sha"], "meta_json": json.dumps(meta), "signature_json": ver["signature_json"]})
    db.insert("lifecycle_events", {"document_id": doc_id, "revision": first, "from_state": None, "to_state": "in_work",
                                   "user_id": user["id"], "comment": f"Copied from {origin}", "at": now})
    db.audit(user["id"], "copy_design", "document", doc_id, f"{f['new_filename']} copied from {src['filename']} "
                                                            f"rev {ver['revision']}")
    link("document", src["id"], ver["revision"], doc_id)
    return {"id": doc_id, "filename": f["new_filename"], "source_id": src["id"], "source_filename": src["filename"],
            "source_revision": ver["revision"]}


def _copy_bom(user, r, new_item, first, item_map, now, link) -> int:
    src = db.one("SELECT * FROM boms WHERE id = ?", (r["bom_id"],))
    bom_id = db.insert("boms", {"item_id": new_item["id"], "revision": first, "source": "copy", "state": "draft",
                                "note": f"Copied from {r['part_number']} rev {src['revision']}", "created_by": user["id"],
                                "created_at": now, "updated_at": now})
    for ln in db.rows("SELECT * FROM bom_lines WHERE bom_id = ? ORDER BY line_no", (src["id"],)):
        db.insert("bom_lines", {"bom_id": bom_id, "line_no": ln["line_no"],
                                "child_item_id": item_map.get(ln["child_item_id"], ln["child_item_id"]), "qty": ln["qty"],
                                "uom": ln["uom"], "find_number": ln["find_number"], "note": ln["note"]})
    if db.one("SELECT item_type FROM items WHERE id = ?", (new_item["id"],))["item_type"] != "assembly":
        db.update("items", new_item["id"], {"item_type": "assembly"})
    link("bom", src["id"], src["revision"], bom_id)
    return bom_id
