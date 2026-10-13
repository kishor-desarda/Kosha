# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-app notifications for the signed-in user: the bell in the top bar."""
from __future__ import annotations

from .. import notifications
from ..web import Req, api, as_bool, bad


@api("GET", "/api/notifications")
def list_notifications(req: Req):
    try:
        limit = int(req.q("limit", 30))
    except ValueError:
        raise bad("limit must be a number")
    return {"notifications": notifications.for_user(req.uid, as_bool(req.q("unread")), limit),
            "unread": notifications.unread_count(req.uid)}


@api("GET", "/api/notifications/count")
def count(req: Req):
    return {"unread": notifications.unread_count(req.uid)}


@api("POST", "/api/notifications/{id:int}/read")
def read_one(req: Req):
    notifications.mark_read(req.uid, [req.int_param("id")])
    return {"unread": notifications.unread_count(req.uid)}


@api("POST", "/api/notifications/read-all")
def read_all(req: Req):
    n = notifications.mark_read(req.uid)
    return {"marked": n, "unread": notifications.unread_count(req.uid)}
