# SPDX-License-Identifier: AGPL-3.0-or-later
"""Notices for Kosha's own events, queued through the outbox:

    change.submitted   -> the approvers named on the change order          (kind: approvals)
    erp.job_failed     -> approvers and admins                              (kind: erp_failures)
    revision.released  -> everyone who chose to hear about releases          (kind: releases, opt-in)

The listeners run in the background after the change is committed (see `Registry.listen`), and the
outbox never raises, so a notice can never block or undo the action behind it.
"""
from __future__ import annotations

import hashlib
import logging

from . import db, lifecycle
from . import notify_outbox as outbox
from .addins import registry

log = logging.getLogger("kosha.notify")


def _erp_name() -> str:
    from . import erp
    try:
        a = registry.get(erp.connector_id())
    except Exception:
        return "The ERP"
    return a.manifest.name.replace(" connector", "") if a and a.manifest else "The ERP"


def on_change_submitted(event: dict) -> None:
    co = db.one("SELECT * FROM change_orders WHERE id = ?", (event.get("change_id"),))
    if not co or co["state"] != "submitted":
        return
    approvers = [r["user_id"] for r in db.rows("SELECT user_id FROM co_approvers WHERE co_id = ? AND decision = 'pending'",
                                               (co["id"],))]
    if not approvers:
        return
    names = lifecycle.user_names()
    files = db.scalar("SELECT COUNT(*) FROM co_items WHERE co_id = ?", (co["id"],))
    body = (f"{names.get(co['requested_by'], 'Someone')} asks you to approve {co['number']}: {co['title']}.\n"
            f"Priority: {co['priority']}. Files affected: {files}.")
    if co["reason"]:
        body += f"\nReason: {co['reason']}"
    outbox.enqueue(approvers, f"{co['number']} needs your approval: {co['title']}", body, f"#/change/{co['id']}",
                   "approvals", key=f"change.submitted:{co['id']}:{co['submitted_at']}", source=f"change:{co['id']}")


def _job_label(job: dict) -> str:
    if job["kind"] in ("item", "refresh"):
        it = db.one("SELECT part_number, description FROM items WHERE id = ?", (job["entity_id"],))
        what = {"item": "item", "refresh": "stock and cost of"}[job["kind"]]
        return f"{what} {it['part_number']} {it['description']}".strip() if it else f"{what} #{job['entity_id']}"
    b = db.one("SELECT i.part_number, b.revision FROM boms b JOIN items i ON i.id = b.item_id WHERE b.id = ?",
               (job["entity_id"],))
    return f"BOM of {b['part_number']} rev {b['revision']}" if b else f"BOM #{job['entity_id']}"


def on_erp_job_failed(event: dict) -> None:
    job = db.one("SELECT * FROM erp_jobs WHERE id = ?", (event.get("job_id"),))
    if not job or job["status"] != "failed":
        return
    people = [r["id"] for r in db.rows("SELECT id FROM users WHERE active = 1 AND role IN ('approver', 'admin')")]
    error = job["error"] or event.get("error") or "no reason given"
    label = _job_label(job)
    erp_name = _erp_name()
    body = (f"Kosha could not send the {label} to {erp_name}.\nReason: {error}\n"
            "Nothing was lost in Kosha. The transfer is kept on the ERP sync page, where it can be retried once the "
            "cause is fixed.")
    # the same error on the same job is told once a day, however often the job is retried
    digest = hashlib.sha256(error.encode()).hexdigest()[:10]
    outbox.enqueue(people, f"ERP transfer failed: {label}", body, "#/erp", "erp_failures", severity="error",
                   key=f"erp.job_failed:{job['id']}:{digest}:{db.now()[:10]}", source=f"erp_job:{job['id']}")


def on_revision_released(event: dict) -> None:
    who = event.get("user_id")
    people = [u for u in outbox.users_wanting("releases") if u != who]
    if not people:
        return
    name = lifecycle.user_names().get(who, "someone")
    pn = event.get("part_number") or ""
    title = f"Released: {pn or event.get('filename')} rev {event.get('revision')}"
    body = f"{event.get('filename')} revision {event.get('revision')} was released by {name}."
    outbox.enqueue(people, title, body, f"#/doc/{event.get('document_id')}", "releases", severity="success",
                   key=f"revision.released:{event.get('document_id')}:{event.get('revision')}",
                   source=f"document:{event.get('document_id')}")


def register() -> None:
    registry.listen("change.submitted", on_change_submitted)
    registry.listen("erp.job_failed", on_erp_job_failed)
    registry.listen("revision.released", on_revision_released)
