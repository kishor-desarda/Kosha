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


KIND_INFO = {
    "erp-connector": ("ERP connector", "Sends released parts and BOMs to an ERP and reads stock, cost and orders back."),
    "file-type": ("File type", "Previews and facts for file types Kosha does not read itself."),
    "pack": ("Industry pack", "Categories, number formats, materials and standard parts for an industry."),
    "ai-provider": ("AI provider", "Answers Kosha's AI requests (a hosted service or a model server inside the company)."),
    "sign-in": ("Sign-in", "Lets people sign in with the company directory or single sign-on."),
    "notifier": ("Notifications", "Sends Kosha's notices to a chat or messaging service."),
    "storage": ("Storage copies", "Keeps a second copy of every stored file elsewhere and brings files back."),
    "importer": ("Importer", "Brings files and part data from another system into a Kosha import."),
    "cad-addin": ("CAD add-in", "Works inside a CAD program on the design PCs."),
    "office-addin": ("Office add-in", "Works inside Word, Excel or Outlook."),
    "web-extension": ("Screen extension", "Adds a panel or a page to Kosha's screens."),
}
RUNS_ON = {"server": "Kosha server", "web": "Kosha screens", "cad": "design PCs", "office": "Office",
           "frappe": "ERP server"}


@api("GET", "/api/addins/catalogue", role="admin")
def catalogue(req: Req):
    """Every add-in Kosha knows (shipped with Kosha or installed here), grouped by kind, with where it runs and
    whether it is on; and for each kind, what it is for and how to start a new one."""
    from kosha_sdk.cli import TEMPLATES
    from kosha_sdk.manifest import KINDS
    groups = []
    found = [a for a in addins.registry.all() if a.manifest]
    for kind in sorted(KINDS, key=lambda k: KIND_INFO.get(k, (k,))[0]):
        label, text = KIND_INFO.get(kind, (kind, ""))
        items = [{**a.to_dict(), "where": [RUNS_ON.get(e, e) for e in sorted(a.manifest.entry_points)]}
                 for a in found if kind in a.manifest.kinds]
        groups.append({"kind": kind, "label": label, "description": text, "addins": items,
                       "template": f"kosha-sdk new {kind} my-addin" if kind in TEMPLATES else ""})
    broken = [a.to_dict() for a in addins.registry.all() if not a.manifest]
    return {"groups": groups, "broken": broken}


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
