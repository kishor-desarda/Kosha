# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin API for bulk legacy imports."""
from __future__ import annotations

import json

from starlette.responses import Response

from .. import db, importer, store
from ..web import Req, api, as_bool, bad, conflict, not_found


@api("POST", "/api/import/preview", role="admin")
def preview(req: Req):
    source = (req.b("source") or "").strip()
    if not source:
        raise bad("Enter the folder to import")
    try:
        return importer.preview(source, req.b("groups") or None)
    except ValueError as e:
        raise bad(str(e))


@api("POST", "/api/import/start", role="admin")
def start(req: Req):
    if db.scalar("SELECT COUNT(*) FROM import_jobs WHERE status = 'running'"):
        raise conflict("An import is already running. Wait for it to finish or cancel it.")
    source = (req.b("source") or "").strip()
    target = (req.b("target") or "").strip().strip("/\\")
    if not source or not target:
        raise bad("Source folder and Kosha folder are both required")
    groups = req.b("groups")
    if isinstance(groups, str):
        groups = json.loads(groups) if groups.startswith("[") else [g for g in groups.split(",") if g]
    options = {
        "state": req.b("state") or "released",
        "keep_revision": as_bool(req.b("keep_revision", True)),
        "skip_identical": as_bool(req.b("skip_identical", True)),
        "dry_run": as_bool(req.b("dry_run", False)),
        "unpack_archives": as_bool(req.b("unpack_archives", True)),
        "groups": groups or importer.DEFAULT_GROUPS,
        "extensions": [e.strip().lower().lstrip(".") for e in str(req.b("extensions") or "").split(",") if e.strip()],
    }
    metadata = None
    files = req.files.get("metadata") or []
    if files:
        f = files[0]
        try:
            metadata = importer.read_metadata(f.file.read(), f.filename)
        except Exception as e:
            raise bad(f"Could not read the part master spreadsheet: {e}")
        options["metadata_file"] = f.filename
        options["metadata_rows"] = len(metadata[1])
    try:
        importer.preview(source, options["groups"], limit=1)
    except ValueError as e:
        raise bad(str(e))
    job_id = importer.start_job(req.user, source, target, options, metadata)
    return {"job": db.one("SELECT * FROM import_jobs WHERE id = ?", (job_id,))}


@api("GET", "/api/import/jobs", role="admin")
def jobs(req: Req):
    rows = db.rows("SELECT * FROM import_jobs ORDER BY id DESC LIMIT 50")
    for r in rows:
        r["options"] = json.loads(r.pop("options_json") or "{}")
    return {"jobs": rows}


@api("POST", "/api/import/jobs/{id:int}/cancel", role="admin")
def cancel(req: Req):
    db.execute("UPDATE import_jobs SET cancel = 1 WHERE id = ? AND status = 'running'", (req.int_param("id"),))
    return {"ok": True}


@api("GET", "/api/import/jobs/{id:int}/report", role="admin")
def report(req: Req):
    j = db.one("SELECT * FROM import_jobs WHERE id = ?", (req.int_param("id"),))
    if not j or not j["report_sha"] or not store.exists(j["report_sha"]):
        raise not_found("No report yet")
    data = store.read_bytes(j["report_sha"])
    is_xlsx = data[:2] == b"PK"
    name = f"kosha-import-{j['id']}.{'xlsx' if is_xlsx else 'csv'}"
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if is_xlsx else "text/csv"
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})
