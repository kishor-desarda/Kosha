# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin API for the health checks: file fingerprints, the nightly consistency check and current alerts."""
from __future__ import annotations

from .. import db, health
from ..web import Req, api


@api("GET", "/api/admin/health", role="admin")
def status(req: Req):
    return health.status()


@api("POST", "/api/admin/health/fingerprints", role="admin")
def check_fingerprints(req: Req):
    """Read back the files due for a check now (``{"everything": true}``: every stored file)."""
    r = health.check_fingerprints(everything=bool(req.b("everything")))
    if not r.get("busy"):
        db.audit(req.uid, "health_fingerprints", "system", None, f"{r['checked']} file(s) checked, {r['damaged']} damaged")
    return {**r, "status": health.status()}


@api("POST", "/api/admin/health/consistency", role="admin")
def check_consistency(req: Req):
    r = health.check_consistency()
    db.audit(req.uid, "health_consistency", "system", None, f"{r['count']} problem(s)")
    return {**r, "status": health.status()}
