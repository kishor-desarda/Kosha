# SPDX-License-Identifier: AGPL-3.0-or-later
"""The installation's locale and the languages people can choose (specification 5.1)."""
from __future__ import annotations

from .. import db, locales
from ..web import Req, api


@api("GET", "/api/locale")
def get_locale(req: Req):
    return {"locale": locales.current(), "locales": [{"code": k, "name": v["name"]} for k, v in locales.LOCALES.items()],
            "languages": locales.languages()}


@api("PUT", "/api/locale", role="admin")
def put_locale(req: Req):
    code = locales.check(str(req.b("locale") or ""))
    db.set_setting("locale", code)
    db.audit(req.uid, "locale", "settings", None, code)
    return get_locale(req)
