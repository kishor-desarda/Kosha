# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each person's workbench: favourites, saved views and screen preferences (specification 9.1)."""
from __future__ import annotations

from .. import workbench
from ..web import Req, api


@api("GET", "/api/me/workbench")
def my_workbench(req: Req):
    return workbench.everything(req.uid)


@api("POST", "/api/me/favourites")
def add_favourite(req: Req):
    return {"favourite": workbench.add_favourite(req.uid, req.b("kind"), req.b("ref_id"))}


@api("DELETE", "/api/me/favourites/{kind}/{ref_id:int}")
def remove_favourite(req: Req):
    workbench.remove_favourite(req.uid, req.request.path_params["kind"], req.int_param("ref_id"))
    return {"ok": True}


@api("POST", "/api/me/views")
def save_view(req: Req):
    return {"view": workbench.save_view(req.uid, req.b("name"), req.b("hash"))}


@api("DELETE", "/api/me/views/{id:int}")
def delete_view(req: Req):
    workbench.delete_view(req.uid, req.int_param("id"))
    return {"ok": True}


@api("PUT", "/api/me/prefs")
def save_prefs(req: Req):
    return {"prefs": workbench.save_prefs(req.uid, req.body if isinstance(req.body, dict) else {})}
