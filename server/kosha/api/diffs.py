# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measured change list and change logs of documents and change orders (spec 7.7)."""
from __future__ import annotations

from .. import ai, changes, db, lifecycle
from ..diff import changelog, service
from ..web import Req, api, bad, forbidden


@api("GET", "/api/documents/{id:int}/changes")
def document_changes(req: Req):
    """Measured differences between two versions. from/to: 'v3' (version), 'B' (latest version of revision B).
    Default: the latest version against the previous released revision."""
    return service.compare_document(req.int_param("id"), req.q("from"), req.q("to"))


# -------------------------------------------------------------- documents --

def _doc_revision(req: Req) -> tuple[dict, str]:
    d = lifecycle.doc_or_404(req.int_param("id"))
    rev = (req.q("revision") or req.b("revision") or d["revision"] or "").strip()
    return d, rev


@api("GET", "/api/documents/{id:int}/changelog")
def document_changelog(req: Req):
    d, rev = _doc_revision(req)
    inp = changelog.document_input(d["id"], rev)
    out = changelog.payload("document", d["id"], rev, inp, changelog.can_edit_document(req.user, d, rev))
    out["revisions"] = _revisions(d)
    return out


@api("POST", "/api/documents/{id:int}/changelog/draft")
def document_changelog_draft(req: Req):
    d, rev = _doc_revision(req)
    return {"draft": changelog.draft(changelog.document_input(d["id"], rev), req.b("note") or "")}


@api("PUT", "/api/documents/{id:int}/changelog", role="engineer")
def save_document_changelog(req: Req):
    d, rev = _doc_revision(req)
    perm = changelog.can_edit_document(req.user, d, rev)
    if not perm["ok"]:
        raise forbidden(perm["why"])
    inp = changelog.document_input(d["id"], rev)
    with db.tx():     # the save and, when the text started from an AI draft, its audit line: together or not at all
        changelog.save(req.user, "document", d["id"], rev, req.b("note") or "", req.b("sections"), inp)
        if req.b("ai_suggestion_id"):
            ai.record_changelog_accept(req.user, req.b("ai_suggestion_id"), "document", d["id"], rev, req.b("sections"))
    return document_changelog(req)


def _revisions(d: dict) -> list[str]:
    revs = [v["revision"] for v in service.versions(d["id"])] + [d["revision"]]
    return list(dict.fromkeys(reversed(revs)))


# ---------------------------------------------------------- change orders --

@api("GET", "/api/changes/{id:int}/changelog")
def change_changelog(req: Req):
    co = changes.co_or_404(req.int_param("id"))
    inp = changelog.change_input(co["id"])
    return changelog.payload("change_order", co["id"], "", inp, changelog.can_edit_change(req.user, co))


@api("POST", "/api/changes/{id:int}/changelog/draft")
def change_changelog_draft(req: Req):
    co = changes.co_or_404(req.int_param("id"))
    return {"draft": changelog.draft(changelog.change_input(co["id"]), req.b("note") or "")}


@api("PUT", "/api/changes/{id:int}/changelog", role="engineer")
def save_change_changelog(req: Req):
    co = changes.co_or_404(req.int_param("id"))
    perm = changelog.can_edit_change(req.user, co)
    if not perm["ok"]:
        raise forbidden(perm["why"])
    if req.b("sections") is None:
        raise bad("sections are required")
    inp = changelog.change_input(co["id"])
    with db.tx():
        changelog.save(req.user, "change_order", co["id"], "", req.b("note") or "", req.b("sections"), inp)
        if req.b("ai_suggestion_id"):
            ai.record_changelog_accept(req.user, req.b("ai_suggestion_id"), "change_order", co["id"], "",
                                       req.b("sections"))
    return change_changelog(req)
