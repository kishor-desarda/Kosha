# SPDX-License-Identifier: AGPL-3.0-or-later
"""Change requests (ECR), approval routes, stand-ins for leave, and approval reminders (specification 7.4 to 7.6)."""
from __future__ import annotations

from .. import approval, change_requests, changes
from ..web import Req, api, bad


# ---------------------------------------------------------- change requests --

@api("GET", "/api/change-requests")
def list_requests(req: Req):
    return {"requests": change_requests.listing(req.q("state") or "", req.q("mine") == "1", req.user),
            "states": change_requests.STATES}


@api("POST", "/api/change-requests", role="viewer")
def raise_request(req: Req):
    cr_id = change_requests.create(req.user, req.b("title") or "", req.b("reason") or "", req.b("document_id"),
                                   req.b("item_id"))
    return change_requests.detail(cr_id, req.user)


@api("GET", "/api/change-requests/{id:int}")
def get_request(req: Req):
    return change_requests.detail(req.int_param("id"), req.user)


@api("POST", "/api/change-requests/{id:int}/decline", role="approver")
def decline_request(req: Req):
    change_requests.decline(req.user, req.int_param("id"), req.b("note") or "")
    return change_requests.detail(req.int_param("id"), req.user)


@api("POST", "/api/change-requests/{id:int}/withdraw", role="viewer")
def withdraw_request(req: Req):
    change_requests.withdraw(req.user, req.int_param("id"))
    return change_requests.detail(req.int_param("id"), req.user)


@api("POST", "/api/change-requests/{id:int}/convert", role="engineer")
def convert_request(req: Req):
    co_id = change_requests.convert(req.user, req.int_param("id"), req.b("change_id"), req.b("approver_ids") or [])
    return {**change_requests.detail(req.int_param("id"), req.user), "change_id": co_id}


# ------------------------------------------------------------------ routes --

@api("GET", "/api/approval-routes")
def list_routes(req: Req):
    return {"routes": approval.routes(), "modes": approval.MODES, "scopes": approval.SCOPES,
            "remind_after_days": approval.REMIND_AFTER_DAYS, "escalate_after_days": approval.ESCALATE_AFTER_DAYS}


@api("POST", "/api/approval-routes", role="admin")
def add_route(req: Req):
    return {"route": approval.add_route(req.uid, req.body if isinstance(req.body, dict) else {})}


@api("PUT", "/api/approval-routes/{id:int}", role="admin")
def update_route(req: Req):
    return {"route": approval.update_route(req.uid, req.int_param("id"), req.body if isinstance(req.body, dict) else {})}


@api("DELETE", "/api/approval-routes/{id:int}", role="admin")
def remove_route(req: Req):
    approval.remove_route(req.uid, req.int_param("id"))
    return {"ok": True}


@api("GET", "/api/changes/route-preview/{id:int}", role="engineer")
def route_preview(req: Req):
    changes.co_or_404(req.int_param("id"))
    return approval.route_for(req.int_param("id"))


# --------------------------------------------------------------- stand-ins --

@api("GET", "/api/me/delegate")
def my_delegate(req: Req):
    return {**approval.delegate_of(req.uid), "standing_in_for": approval.standing_in_for(req.uid)}


@api("PUT", "/api/me/delegate", role="viewer")
def set_my_delegate(req: Req):
    if not approval.can_approve(req.user):
        raise bad("Only approvers name a stand-in.")
    return approval.set_delegate(req.user, req.b("user_id"), req.b("until"))


# --------------------------------------------------------------- reminders --

@api("POST", "/api/changes/reminders", role="admin")
def run_reminders(req: Req):
    """Send the due reminders now (they are also sent by the background worker)."""
    return approval.send_reminders()
