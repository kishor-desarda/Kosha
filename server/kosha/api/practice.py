# SPDX-License-Identifier: AGPL-3.0-or-later
"""The practice folder (specification 5.6): where it is, for the first-login tour."""
from __future__ import annotations

from .. import access, db, practice
from ..web import Req, api


@api("GET", "/api/practice")
def where(req: Req):
    fid = practice.folder_id()
    if not fid or not access.scope().can_see_folder(fid):
        return {"folder_id": None, "name": None}
    return {"folder_id": fid, "name": db.scalar("SELECT name FROM folders WHERE id = ?", (fid,))}
