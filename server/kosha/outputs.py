# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release outputs (specification 7.3 rules 6 and 7, and 8.3).

At every release Kosha makes, for the released version of the file:

* **Stamped PDF** of a drawing or document (the file itself when it is a PDF, otherwise the PDF the add-in or the
  converter made at check-in): a green stamp *Released — Rev B — 10-Oct-2026 — <approver>* on every page.
* **Release sheet:** one page that records the part, the revision, who released it, when and through which change
  order, and the files released with it (version and fingerprint).

Outputs are stored as files linked to the revision and version they came from, so a PDF can never be mistaken for
another revision. Making them never blocks or undoes a release: a failure is recorded and an approver can make them
again from the file's page.

When an output is downloaded it also carries *Uncontrolled when printed — <date>* and, like any released PDF, the
watermark line. An output of an older revision carries a red *Superseded — use Rev C* band.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import PurePath

from . import db, store, watermark
from .web import not_found

log = logging.getLogger("kosha.outputs")
KINDS = {"released_pdf": "Stamped PDF", "release_sheet": "Release sheet"}


def _day(at: str | None = None) -> str:
    d = datetime.fromisoformat(at).date() if at else date.today()
    return d.strftime("%d-%b-%Y")


def superseded_by(d: dict, revision: str) -> str | None:
    """The revision to use instead of ``revision``, when a later one was released; else None."""
    from .lifecycle import LIVE_STATES
    released = [r["revision"] for r in db.rows("SELECT revision FROM lifecycle_events WHERE document_id = ? AND "
                                               "to_state = 'released' ORDER BY id", (d["id"],))]
    current = d["revision"] if d["state"] in LIVE_STATES else (released[-1] if released else None)
    return current if current and revision != current and revision in released else None


def _source_pdf(d: dict, v: dict) -> bytes | None:
    if d["ext"] == "pdf" and store.exists(v["blob_sha"]):
        return store.read_bytes(v["blob_sha"])
    if v["preview_kind"] == "pdf" and v["preview_sha"] and store.exists(v["preview_sha"]):
        return store.read_bytes(v["preview_sha"])
    return None


def _save(d: dict, v: dict, kind: str, filename: str, data: bytes | None, user_id, note: str = "") -> None:
    sha, size = store.put_bytes(data) if data else (None, 0)
    with db.tx():       # made again: the row is updated; the earlier content stays in the file store
        db.execute("INSERT INTO release_outputs(document_id, revision, version, kind, filename, blob_sha, size, status, "
                   "note, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT(document_id, revision, version, kind) DO UPDATE SET filename = excluded.filename, "
                   "blob_sha = excluded.blob_sha, size = excluded.size, status = excluded.status, note = excluded.note, "
                   "created_by = excluded.created_by, created_at = excluded.created_at",
                   (d["id"], d["revision"], v["version"], kind, filename, sha, size, "ok" if data else "failed", note,
                    user_id, db.now()))


def make(doc_id: int, user: dict) -> list[dict]:
    """Make the outputs of the file's released revision (again). Returns them."""
    from .lifecycle import LIVE_STATES, doc_or_404, user_names
    d = doc_or_404(doc_id)
    if d["state"] not in LIVE_STATES:
        return listing(doc_id)
    v = db.one("SELECT * FROM versions WHERE document_id = ? AND version = ?", (doc_id, d["latest_version"]))
    if not v:
        return listing(doc_id)
    ev = db.one("SELECT e.*, c.number AS change_number FROM lifecycle_events e LEFT JOIN change_orders c "
                "ON c.id = e.change_order_id WHERE e.document_id = ? AND e.to_state = 'released' AND e.revision = ? "
                "ORDER BY e.id DESC LIMIT 1", (doc_id, d["revision"]))
    names = user_names()
    by = names.get(ev["user_id"], "") if ev else user.get("full_name", "")
    when = _day(ev["at"]) if ev else _day()
    stem = PurePath(d["filename"]).stem

    src = _source_pdf(d, v)
    if src is None:
        if d["ext"] == "pdf" or v["preview_kind"] == "pdf":
            _save(d, v, "released_pdf", f"{stem} Rev {d['revision']}.pdf", None, user["id"],
                  "The PDF is not available on the server.")
    else:
        stamped = watermark.release_stamp(src, f"Released — Rev {d['revision']} — {when}" + (f" — {by}" if by else ""))
        _save(d, v, "released_pdf", f"{stem} Rev {d['revision']}.pdf", stamped, user["id"],
              "" if stamped else "The PDF could not be stamped (damaged, protected, or pdfium is not installed).")

    item = db.one("SELECT * FROM items WHERE id = ?", (d["item_id"],)) if d["item_id"] else None
    files = db.rows("SELECT d.filename, d.revision, d.latest_version, v.blob_sha FROM documents d JOIN versions v "
                    "ON v.document_id = d.id AND v.version = d.latest_version WHERE d.deleted = 0 AND "
                    + ("d.item_id = ? AND d.revision = ? AND d.state IN ('released', 'spares_only')"
                       if d["item_id"] else "d.id = ?") + " ORDER BY d.filename",
                    (d["item_id"], d["revision"]) if d["item_id"] else (doc_id,))
    company = (db.get_setting("company_name") or "").strip()
    rows = [("Part number", d["part_number"] or (item or {}).get("part_number") or "—"),
            ("Description", d["description"] or (item or {}).get("description") or "—"),
            ("Revision", d["revision"]), ("Released on", when), ("Released by", by or "—"),
            ("Change order", (ev or {}).get("change_number") or "—"),
            ("Comment", ((ev or {}).get("comment") or "—")[:90])]
    if item:
        rows += [("Material", item["material"] or "—"), ("Make or buy", item["make_buy"]),
                 ("Weight", f"{item['weight_kg']} kg" if item["weight_kg"] else "—")]
    table = [[f["filename"], str(f["latest_version"]), f["revision"], f["blob_sha"][:16]] for f in files]
    sheet = watermark.sheet(f"Release sheet — {d['part_number'] or stem} Rev {d['revision']}", rows, table,
                            ["File", "Version", "Rev", "Fingerprint (SHA-256)"],
                            f"{company + ' · ' if company else ''}Made by Kosha on {_day()}. "
                            "The fingerprints identify the exact files released.")
    _save(d, v, "release_sheet", f"{stem} Rev {d['revision']} release sheet.pdf", sheet, user["id"],
          "" if sheet else "The release sheet could not be written (pdfium is not installed).")
    db.audit(user["id"], "release_outputs", "document", doc_id, f"{d['filename']} rev {d['revision']}")
    return listing(doc_id)


def on_release(doc_id: int, user: dict) -> None:
    """Called after a release is committed. Never raises: the release stands whatever happens here."""
    try:
        make(doc_id, user)
    except Exception:
        log.exception("Could not make the release outputs of file %s", doc_id)


def listing(doc_id: int) -> list[dict]:
    from .lifecycle import doc_or_404, user_names
    d = doc_or_404(doc_id)
    names = user_names()
    out = []
    for r in db.rows("SELECT * FROM release_outputs WHERE document_id = ? ORDER BY id DESC", (doc_id,)):
        out.append({**{k: r[k] for k in ("id", "revision", "version", "kind", "filename", "size", "status", "note",
                                         "created_at")},
                    "label": KINDS.get(r["kind"], r["kind"]), "created_by_name": names.get(r["created_by"]),
                    "superseded_by": superseded_by(d, r["revision"])})
    return out


def content(output_id: int, user: dict) -> tuple[bytes, str, dict]:
    """(PDF bytes, file name, row) of an output as it is downloaded: printed line, superseded band, watermark."""
    from . import access
    from .lifecycle import doc_or_404
    r = db.one("SELECT * FROM release_outputs WHERE id = ?", (output_id,))
    if not r:
        raise not_found("Output not found")
    d = doc_or_404(r["document_id"])
    if r["status"] != "ok" or not r["blob_sha"] or not store.exists(r["blob_sha"]):
        raise not_found("This output was not made. An approver can make it again from the file's page.")
    access.check_download()
    data = store.read_bytes(r["blob_sha"])
    marked = watermark.printed_line(data, f"Uncontrolled when printed — {_day()}") or data
    newer = superseded_by(d, r["revision"])
    if newer:
        marked = watermark.superseded_band(marked, f"Superseded — use Rev {newer}") or marked
    if watermark.enabled():
        marked = watermark.stamp_pdf(marked, watermark.line_for(user["full_name"])) or marked
    db.audit(user["id"], "download", "document", d["id"],
             f"{KINDS.get(r['kind'], r['kind'])} rev {r['revision']} v{r['version']}" + (", superseded" if newer else ""))
    return marked, r["filename"], r
