# SPDX-License-Identifier: AGPL-3.0-or-later
"""Programs and events: API keys for service accounts, webhooks, and the live event stream for open screens."""
from __future__ import annotations

from starlette.responses import StreamingResponse

from .. import apikeys, events
from ..web import Req, api

events.register()       # every event published by Kosha reaches webhooks and open screens


# ---------------------------------------------------------------- API keys --

@api("GET", "/api/api-scopes", role="admin")
def scopes(req: Req):
    return {"scopes": apikeys.SCOPES, "areas": apikeys.AREAS}


@api("GET", "/api/users/{id:int}/api-keys", role="admin")
def list_keys(req: Req):
    return {"keys": apikeys.listing(req.int_param("id"))}


@api("POST", "/api/users/{id:int}/api-keys", role="admin")
def create_key(req: Req):
    """Make a key for a service account. The answer's ``key`` is shown this once; Kosha keeps only a fingerprint."""
    return {"key": apikeys.create(req.uid, req.int_param("id"), req.body if isinstance(req.body, dict) else {})}


@api("POST", "/api/api-keys/{id:int}/revoke", role="admin")
def revoke_key(req: Req):
    return {"key": apikeys.revoke(req.uid, req.int_param("id"))}


# ---------------------------------------------------------------- webhooks --

@api("GET", "/api/webhooks", role="admin")
def list_hooks(req: Req):
    return {"webhooks": events.hooks(), "events": events.EVENTS, "deliveries": events.deliveries(limit=100)}


@api("POST", "/api/webhooks", role="admin")
def add_hook(req: Req):
    """Register an address for some events. The answer's ``secret`` (shown once) signs every call."""
    return {"webhook": events.add_hook(req.uid, req.body if isinstance(req.body, dict) else {})}


@api("PATCH", "/api/webhooks/{id:int}", role="admin")
def update_hook(req: Req):
    return {"webhook": events.update_hook(req.uid, req.int_param("id"), req.body if isinstance(req.body, dict) else {})}


@api("DELETE", "/api/webhooks/{id:int}", role="admin")
def remove_hook(req: Req):
    events.remove_hook(req.uid, req.int_param("id"))
    return {"ok": True}


@api("GET", "/api/webhooks/{id:int}/deliveries", role="admin")
def hook_deliveries(req: Req):
    return {"deliveries": events.deliveries(req.int_param("id"), 200)}


@api("POST", "/api/webhooks/deliveries/{id:int}/replay", role="admin")
def replay(req: Req):
    return {"delivery": events.replay(req.uid, req.int_param("id"))}


@api("POST", "/api/webhooks/process", role="admin")
def process(req: Req):
    """Send the deliveries that are due now (the background worker does this too)."""
    return {"processed": events.process_due()}


# ------------------------------------------------------------ live screens --

@api("GET", "/api/events")
def catalogue(req: Req):
    return {"events": events.EVENTS}


@api("GET", "/api/events/stream")
async def live_stream(req: Req):
    """Server-sent events for an open screen: the event name and record ids, as they happen."""
    return StreamingResponse(events.stream(req.uid), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Content-Encoding": "identity"})
