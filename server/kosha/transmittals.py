# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transmittals and vendor packages (specification 6.9 and 15.3), and Pack and Go (6.8).

A **vendor package** sends the released files of a part to a supplier for one order: the files themselves, the PDF
stamped at release, and, when asked, the part's other released files of the same revision (DXF of plates, STEP
model). It goes out through a share link that ends on a date, carries the recipient's name as a watermark and can be
withdrawn. A **transmittal** is the same for a customer or consultant, usually for a project.

Each one gets a number (TR-26-0001) and a transmittal sheet (PDF), and Kosha keeps the record of which revision went
to whom, when, why and for which order. When a newer revision of a sent file is released while the transmittal is
still open, its sender is told and the recipient's page says *Superseded — ask for the new drawing*. Closing a
transmittal (the order is done) ends its link.

**Pack and Go** exports a BOM's whole tree as one ZIP (every file of every part, or released files only) with a
transmittal list, for a consultant or vendor.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date

from . import access, db, lifecycle, notify_outbox, shares, store, watermark
from .web import bad, conflict, forbidden, not_found

KINDS = {"vendor_package": "Vendor package", "transmittal": "Transmittal"}


def _files(doc_ids: list[int], include_related: bool) -> list[dict]:
    """The documents to send: the chosen ones, plus their part's other released files of the same revision."""
    seen: dict[int, dict] = {}
    for i in doc_ids:
        d = lifecycle.doc_or_404(i)
        if d["state"] not in lifecycle.LIVE_STATES:
            raise conflict(f"{d['filename']} is not released. Only released files are sent outside the company.",
                           "not_released")
        seen.setdefault(d["id"], d)
        if include_related and d["item_id"]:
            for r in db.rows("SELECT * FROM documents WHERE item_id = ? AND revision = ? AND deleted = 0 AND state IN "
                             "('released', 'spares_only') ORDER BY filename", (d["item_id"], d["revision"])):
                if access.scope().can_see_document(r):
                    seen.setdefault(r["id"], r)
    return list(seen.values())


def _sheet(t: dict, sender: str, files: list[dict]) -> bytes | None:
    company = (db.get_setting("company_name") or "").strip()
    rows = [("Number", t["number"]), ("Kind", KINDS[t["kind"]]), ("To", t["recipient"]),
            ("Purpose", t["purpose"] or "—"), ("Order", t["order_ref"] or "—"), ("Sent by", sender),
            ("Date", date.today().strftime("%d-%b-%Y")), ("Link works until", t.get("expires_on") or "—")]
    table = [[f["filename"], f"v{f['version']}", f["revision"], f["sha"][:16]] for f in files]
    return watermark.sheet(f"{KINDS[t['kind']]} {t['number']}", rows, table,
                           ["File", "Version", "Rev", "Fingerprint (SHA-256)"],
                           f"{company + ' · ' if company else ''}Each file is identified by its fingerprint. "
                           "Use only the revisions listed here.")


def create(user: dict, body: dict) -> dict:
    from .numbering import NumberingService
    kind = body.get("kind") or "vendor_package"
    if kind not in KINDS:
        raise bad("Choose a vendor package or a transmittal.")
    ids = body.get("document_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids) or len(ids) > 200:
        raise bad("Choose the released files to send (at most 200).")
    project_id = body.get("project_id")
    if project_id:
        from .projects import project_or_404
        project_or_404(int(project_id))
    docs = _files(ids, bool(body.get("include_related", True)))
    share = shares.create(user, {"document_ids": [d["id"] for d in docs], "recipient": body.get("recipient"),
                                 "days": body.get("days"), "password": body.get("password"),
                                 "view_only": body.get("view_only"), "note": body.get("note")})
    files = []
    for d in docs:
        v = db.one("SELECT version, blob_sha FROM versions WHERE document_id = ? AND version = ?",
                   (d["id"], d["latest_version"]))
        files.append({"document_id": d["id"], "filename": d["filename"], "revision": d["revision"],
                      "version": v["version"], "sha": v["blob_sha"]})
    number = NumberingService().next_in_series(f"TR-{date.today():%y}-", 4, user["id"])
    t = {"number": number, "kind": kind, "recipient": share["recipient"],
         "purpose": " ".join(str(body.get("purpose") or "").split())[:300],
         "order_ref": " ".join(str(body.get("order_ref") or "").split())[:60], "expires_on": share["expires_on"]}
    sheet = _sheet(t, user.get("full_name") or user.get("username", ""), files)
    sheet_sha = store.put_bytes(sheet)[0] if sheet else None
    with db.tx():
        tid = db.insert("transmittals", {**{k: v for k, v in t.items() if k != "expires_on"},
                                         "project_id": int(project_id) if project_id else None, "share_id": share["id"],
                                         "files_json": json.dumps(files), "sheet_sha": sheet_sha, "status": "open",
                                         "created_by": user["id"], "created_at": db.now()})
        db.audit(user["id"], "transmittal_create", "transmittal", tid,
                 f"{number}: {len(files)} file(s) to {t['recipient']}" + (f" for {t['order_ref']}" if t["order_ref"] else ""))
    return {**get(user, tid), "token": share["token"]}


def _summary(r: dict, names: dict) -> dict:
    files = json.loads(r["files_json"])
    share = db.one("SELECT expires_on, withdrawn_at, opened_count, download_count FROM share_links WHERE id = ?",
                   (r["share_id"],)) if r["share_id"] else None
    superseded = json.loads(r["superseded_json"] or "[]")
    link = "none" if not share else "withdrawn" if share["withdrawn_at"] else \
        "ended" if share["expires_on"] < date.today().isoformat() else "active"
    project = db.one("SELECT code FROM projects WHERE id = ?", (r["project_id"],)) if r["project_id"] else None
    return {k: r[k] for k in ("id", "number", "kind", "recipient", "purpose", "order_ref", "project_id", "status",
                              "created_at", "closed_at")} | {
        "kind_label": KINDS.get(r["kind"], r["kind"]), "files": files, "superseded": superseded,
        "created_by_name": names.get(r["created_by"]), "project_code": project["code"] if project else "",
        "link": link, "expires_on": share["expires_on"] if share else None,
        "opened": share["opened_count"] if share else 0, "downloads": share["download_count"] if share else 0}


def _visible(user: dict, r: dict) -> bool:
    if r["created_by"] == user["id"] or access.has_role(user, "approver"):
        return True
    return False


def get(user: dict, tid: int) -> dict:
    r = db.one("SELECT * FROM transmittals WHERE id = ?", (tid,))
    if not r or not _visible(user, r):
        raise not_found("Transmittal not found")
    return _summary(r, lifecycle.user_names())


def listing(user: dict, document_id: int | None = None, project_id: int | None = None, q: str = "") -> list[dict]:
    names = lifecycle.user_names()
    rows = db.rows("SELECT * FROM transmittals ORDER BY id DESC LIMIT 500")
    out = []
    for r in rows:
        if not _visible(user, r):
            continue
        if project_id and r["project_id"] != project_id:
            continue
        if document_id and not any(f["document_id"] == document_id for f in json.loads(r["files_json"])):
            continue
        if q and q.lower() not in f"{r['number']} {r['recipient']} {r['order_ref']} {r['purpose']}".lower():
            continue
        out.append(_summary(r, names))
    return out


def sheet(user: dict, tid: int) -> tuple[bytes, str]:
    t = get(user, tid)
    sha = db.scalar("SELECT sheet_sha FROM transmittals WHERE id = ?", (tid,))
    if not sha or not store.exists(sha):
        raise not_found("The transmittal sheet was not made (pdfium is not installed).")
    return store.read_bytes(sha), f"{t['number']} transmittal sheet.pdf"


def close(user: dict, tid: int) -> dict:
    """The order is done: the link ends and no more superseded warnings are sent."""
    t = get(user, tid)
    r = db.one("SELECT * FROM transmittals WHERE id = ?", (tid,))
    if r["created_by"] != user["id"] and not access.has_role(user, "approver"):
        raise forbidden("Only the sender or an approver can close a transmittal.")
    if t["status"] == "open":
        if r["share_id"] and t["link"] == "active":
            shares.withdraw(user, r["share_id"])
        with db.tx():
            db.update("transmittals", tid, {"status": "closed", "closed_at": db.now(), "closed_by": user["id"]})
            db.audit(user["id"], "transmittal_close", "transmittal", tid, t["number"])
    return get(user, tid)


def on_release(doc_id: int) -> int:
    """A newer revision of ``doc_id`` was released: warn the senders of open transmittals that carry an older one.
    Never raises; returns how many were warned."""
    try:
        d = db.one("SELECT id, filename, revision FROM documents WHERE id = ?", (doc_id,))
        n = 0
        for r in db.rows("SELECT * FROM transmittals WHERE status = 'open'"):
            sent = [f for f in json.loads(r["files_json"]) if f["document_id"] == doc_id and f["revision"] != d["revision"]]
            if not sent:
                continue
            old = json.loads(r["superseded_json"] or "[]")
            if any(x["document_id"] == doc_id and x["new_revision"] == d["revision"] for x in old):
                continue
            old.append({"document_id": doc_id, "filename": d["filename"], "sent_revision": sent[0]["revision"],
                        "new_revision": d["revision"], "at": db.now()})
            with db.tx():
                db.update("transmittals", r["id"], {"superseded_json": json.dumps(old)})
            notify_outbox.enqueue(
                [r["created_by"]], f"{r['number']}: {d['filename']} has a newer revision",
                f"You sent revision {sent[0]['revision']} to {r['recipient']}"
                + (f" for {r['order_ref']}" if r["order_ref"] else "")
                + f". Revision {d['revision']} is now released. Their link says it is superseded; send them the new "
                  "revision, or close the transmittal if the order is done.", "#/transmittals", "general",
                severity="warning", key=f"transmittal-superseded:{r['id']}:{doc_id}:{d['revision']}")
            n += 1
        return n
    except Exception:
        import logging
        logging.getLogger("kosha.transmittals").exception("Could not check open transmittals")
        return 0


# --------------------------------------------------------------- Pack and Go --

def pack_and_go(user: dict, bom_id: int, released_only: bool = False) -> tuple[io.BytesIO, str]:
    """A ZIP with every file of every part in the BOM's tree (or only released ones) and a transmittal list."""
    from . import boms
    bom = boms.bom_or_404(bom_id)
    access.check_item(bom["item_id"])
    item_ids = [bom["item_id"]] + [r["item_id"] for r in boms.explode(bom_id)]
    sc = access.scope()
    docs = []
    for iid in dict.fromkeys(item_ids):
        for d in db.rows("SELECT * FROM documents WHERE item_id = ? AND deleted = 0 AND purged_at IS NULL "
                         "ORDER BY filename", (iid,)):
            if not sc.can_see_document(d):
                continue
            if released_only and d["state"] not in lifecycle.LIVE_STATES:
                continue
            docs.append(d)
    if not docs:
        raise conflict("No files to pack" + (" (none of them is released yet)." if released_only else "."), "empty")
    if len(docs) > 2000:
        raise conflict("More than 2000 files: pack a smaller assembly.", "too_many")
    buf = io.BytesIO()
    listing_rows = []
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in docs:
            v = db.one("SELECT * FROM versions WHERE document_id = ? AND version = ?", (d["id"], d["latest_version"]))
            if not v or not store.exists(v["blob_sha"]):
                listing_rows.append([d["filename"], d["revision"], "", d["state"], "missing on the server"])
                continue
            access.check_download()          # each file counts towards the bulk-download alarm
            name = d["filename"]
            stem, dot, ext = name.rpartition(".")
            k = 2
            while name.lower() in used:
                name = f"{stem} ({k}).{ext}" if dot else f"{d['filename']} ({k})"
                k += 1
            used.add(name.lower())
            z.writestr(name, store.read_bytes(v["blob_sha"]))
            listing_rows.append([name, d["revision"], v["version"], lifecycle.STATE_LABEL.get(d["state"], d["state"]),
                                 v["blob_sha"]])
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow([f"Pack and Go of {bom['part_number']} {bom['description']} BOM rev {bom['revision']}",
                    date.today().isoformat(), user.get("full_name", "")])
        w.writerow(["File", "Revision", "Version", "State", "Fingerprint (SHA-256)"])
        w.writerows(listing_rows)
        z.writestr("Transmittal list.csv", out.getvalue().encode("utf-8-sig"))
    db.audit(user["id"], "pack_and_go", "bom", bom_id,
             f"{bom['part_number']} rev {bom['revision']}: {len(docs)} file(s)" + (", released only" if released_only else ""))
    buf.seek(0)
    return buf, f"{bom['part_number']} rev {bom['revision']} Pack and Go.zip"
