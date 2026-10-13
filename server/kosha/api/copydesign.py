# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy design (spec 6.8): preview, copy (idempotent, all-or-nothing) and the stored result of a copy."""
from __future__ import annotations

from .. import copydesign
from ..web import Req, api, not_found


@api("POST", "/api/copy-design/preview", role="engineer")
def preview(req: Req):
    """The review table for a copy: rows with action, proposed number, description and files, plus problems."""
    return copydesign.plan(req.user, req.body if isinstance(req.body, dict) else {})


@api("POST", "/api/copy-design", role="engineer")
def run(req: Req):
    """Copy. Send the same ``key`` again to get the stored result instead of a second copy."""
    return copydesign.execute(req.user, req.body if isinstance(req.body, dict) else {})


@api("GET", "/api/copy-design/jobs/{key}", role="engineer")
def job(req: Req):
    j = copydesign.job(req.params.get("key", ""))
    if not j:
        raise not_found("No copy with this key has finished. Nothing was copied; it is safe to start the copy again.")
    return j
