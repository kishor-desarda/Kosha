# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release outputs of a file: list, download (stamped for printing), make again."""
from __future__ import annotations

from starlette.responses import Response

from .. import outputs
from ..web import Req, api
from .files import SAFE_HEADERS, _disposition


@api("GET", "/api/documents/{id:int}/outputs")
def listing(req: Req):
    return {"outputs": outputs.listing(req.int_param("id"))}


@api("POST", "/api/documents/{id:int}/outputs", role="approver")
def make_again(req: Req):
    """Make the stamped PDF and the release sheet of the released revision again (after a failure)."""
    return {"outputs": outputs.make(req.int_param("id"), req.user)}


@api("GET", "/api/outputs/{id:int}/file")
def download(req: Req):
    data, filename, _ = outputs.content(req.int_param("id"), req.user)
    kind = "inline" if req.q("inline") else "attachment"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": _disposition(kind, filename),
                                                                 **SAFE_HEADERS})
