# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin API for add-ins: list, enable (approving permissions), disable, settings, health."""
from __future__ import annotations

from .. import addins
from ..web import Req, api, bad, not_found

from kosha_sdk import AddinError  # noqa: E402


def _get(req: Req):
    a = addins.registry.get(req.params["addin_id"])
    if not a or not a.manifest:
        raise not_found("No add-in with that name is installed.")
    return a


@api("GET", "/api/addins", role="admin")
def list_addins(req: Req):
    return {"addins": [a.to_dict() for a in addins.registry.all()]}


@api("POST", "/api/addins/rescan", role="admin")
def rescan(req: Req):
    addins.registry.discover()
    return list_addins(req)


@api("GET", "/api/addins/{addin_id}", role="admin")
def detail(req: Req):
    a = _get(req)
    d = a.to_dict()
    d["help"] = a.manifest.help
    d["settings_schema"] = [{"key": s.key, "type": s.type, "label": s.label, "hint": s.hint, "required": s.required,
                             "choices": list(s.choices)} for s in a.manifest.settings]
    d["settings"] = addins.registry.masked_settings(a.id) if a.manifest.settings else {}
    return d


@api("POST", "/api/addins/{addin_id}/enable", role="admin")
def enable(req: Req):
    a = _get(req)
    try:
        addins.registry.enable(a.id, req.uid)
    except AddinError as e:
        raise bad(str(e))
    return detail(req)


@api("POST", "/api/addins/{addin_id}/disable", role="admin")
def disable(req: Req):
    a = _get(req)
    addins.registry.disable(a.id, req.uid)
    return detail(req)


@api("PUT", "/api/addins/{addin_id}/settings", role="admin")
def save_settings(req: Req):
    a = _get(req)
    if not isinstance(req.body, dict):
        raise bad("Send the settings as an object.")
    try:
        addins.registry.save_settings(a.id, req.body, req.uid)
    except AddinError as e:
        raise bad(str(e))
    return detail(req)


@api("POST", "/api/addins/{addin_id}/health", role="admin")
def health(req: Req):
    a = _get(req)
    try:
        h = addins.registry.call(a.id, "health", timeout=30)
        return {"ok": h.ok, "message": h.message}
    except AddinError as e:
        return {"ok": False, "message": str(e)}
