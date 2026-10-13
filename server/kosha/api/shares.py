# SPDX-License-Identifier: AGPL-3.0-or-later
"""Share links: made by people in Kosha, opened by people outside it without an account."""
from __future__ import annotations

import urllib.parse

from starlette.responses import Response

from .. import shares
from ..signin import client_ip
from ..web import Req, api

MIME = {"pdf": "application/pdf", "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg"}
# a shared file never runs as part of Kosha's pages
SAFE = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"}


@api("GET", "/api/shares", role="engineer")
def list_shares(req: Req):
    """The links this person made (an admin sees every link)."""
    return {"shares": shares.listing(req.user)}


@api("POST", "/api/shares", role="engineer")
def create_share(req: Req):
    """Share released files: ``document_ids``, ``recipient``, ``days`` (1-90), optional ``password``, ``view_only``
    and ``note``. The answer's ``token`` is shown once; the link is ``<server>/#/share/<token>``."""
    return shares.create(req.user, req.body if isinstance(req.body, dict) else {})


@api("POST", "/api/shares/{id:int}/withdraw", role="engineer")
def withdraw_share(req: Req):
    return shares.withdraw(req.user, req.int_param("id"))


# ------------------------------------------------- for the outside recipient --

@api("POST", "/api/share/{token}/unlock", role=None, auth=False)
def unlock(req: Req):
    """The link's password, for a link that has one: answers a ``key`` to send with the other calls."""
    return shares.unlock(req.params["token"], req.b("password"), client_ip(req))


@api("GET", "/api/share/{token}", role=None, auth=False)
def view(req: Req):
    return shares.view(req.params["token"], req.q("key"))


def _send(data: bytes, ext: str, name: str, inline: bool) -> Response:
    quoted = urllib.parse.quote(name)
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "")
    disp = f'{"inline" if inline else "attachment"}; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
    return Response(data, media_type=MIME.get(ext, "application/octet-stream"),
                    headers={"Content-Disposition": disp, **SAFE})


@api("GET", "/api/share/{token}/files/{doc:int}/preview", role=None, auth=False)
def preview(req: Req):
    data, ext, name = shares.content(req.params["token"], req.q("key"), req.int_param("doc"), "preview")
    return _send(data, ext, name, True)


@api("GET", "/api/share/{token}/files/{doc:int}/download", role=None, auth=False)
def download(req: Req):
    data, ext, name = shares.content(req.params["token"], req.q("key"), req.int_param("doc"), "file")
    return _send(data, ext, name, False)
