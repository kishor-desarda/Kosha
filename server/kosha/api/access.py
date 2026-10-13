# SPDX-License-Identifier: AGPL-3.0-or-later
"""Roles and access rules (Admin › Access) and the quarterly access review."""
from __future__ import annotations

from starlette.responses import Response

from .. import access, db
from ..web import ROLE_LEVEL, Req, api


@api("GET", "/api/access")
def overview(req: Req):
    """The roles, kinds of account and kinds of rule, with the rules (admins only see the rules)."""
    return {"roles": [{"id": r, "name": access.ROLE_NAMES[r], "help": access.ROLE_HELP[r], "main": True}
                      for r in ROLE_LEVEL] +
                     [{"id": r, "name": n, "help": access.ROLE_HELP[r], "main": False} for r, n in access.EXTRA_ROLES.items()],
            "kinds": access.KINDS, "scopes": access.SCOPES,
            "rules": access.rules() if req.has("admin") else []}


@api("POST", "/api/access/rules", role="admin")
def add_rule(req: Req):
    return {"rule": access.add_rule(req.uid, req.body if isinstance(req.body, dict) else {})}


@api("PUT", "/api/access/rules/{id:int}", role="admin")
def change_rule(req: Req):
    return {"rule": access.update_rule(req.uid, req.int_param("id"), req.body if isinstance(req.body, dict) else {})}


@api("DELETE", "/api/access/rules/{id:int}", role="admin")
def remove_rule(req: Req):
    access.remove_rule(req.uid, req.int_param("id"))
    return {"ok": True}


@api("GET", "/api/access/review", role="approver")
def review(req: Req):
    """Who can see what: every account with its roles, projects, limited areas and last sign-in."""
    if req.q("format") == "csv":
        db.audit(req.uid, "access_review_export", "user", None, "")
        return Response(access.review_csv(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="kosha-access-review-{db.now()[:10]}.csv"'})
    return access.review()
