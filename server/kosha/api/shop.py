# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shop view and QR labels (specification 5.7): released drawings by part number, and a QR code that opens them."""
from __future__ import annotations

from urllib.parse import quote

from starlette.responses import Response

from .. import access, db, qr, shop
from ..web import Req, api, bad, not_found
from .directory import _base
from .files import SAFE_HEADERS, _disposition


@api("GET", "/api/shop")
def shop_view(req: Req):
    return shop.lookup(req.q("pn") or "")


@api("GET", "/api/items/{id:int}/qr.svg")
def item_qr(req: Req):
    """A printable QR label: scanning it opens the shop view of this part."""
    iid = req.int_param("id")
    it = db.one("SELECT part_number, description FROM items WHERE id = ?", (iid,))
    if not it:
        raise not_found("Item not found")
    access.check_item(iid)
    link = f"{_base(req)}/#/shop?pn={quote(it['part_number'], safe='')}"
    label = it["part_number"] + (" " + it["description"] if it["description"] else "")
    try:
        picture = qr.svg(link, label=label[:48])
    except qr.QrTooLong:
        raise bad("Kosha's address is too long for a QR code. Shorten it under Admin › Notifications.") from None
    return Response(picture, media_type="image/svg+xml",
                    headers={**SAFE_HEADERS, "Cache-Control": "no-cache",
                             "Content-Disposition": _disposition("inline", f"QR {it['part_number']}.svg")})
