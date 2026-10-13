# SPDX-License-Identifier: AGPL-3.0-or-later
"""Notifications: each user's channel choices, and the admin's mail server, channels and delivery log."""
from __future__ import annotations

import uuid

from .. import db, mailer, notify_events
from .. import notify_outbox as outbox
from ..addins import registry
from ..web import Req, api, bad, not_found

from kosha_sdk import AddinError  # noqa: E402
from kosha_sdk.notify import Notice, Recipient  # noqa: E402

notify_events.register()     # approvals, failed ERP transfers and releases become notices


def _kinds() -> list[dict]:
    return [{"id": k, "label": v[0], "audience": v[1]} for k, v in outbox.KINDS.items()]


@api("GET", "/api/me/notifications")
def my_notifications(req: Req):
    me = db.one("SELECT email FROM users WHERE id = ?", (req.uid,)) or {}
    return {"kinds": _kinds(), "channels": [c.to_dict() for c in outbox.channels()], "prefs": outbox.prefs(req.uid),
            "email": me.get("email") or ""}


@api("PUT", "/api/me/notifications")
def save_my_notifications(req: Req):
    body = req.body if isinstance(req.body, dict) else {}
    prefs = body.get("prefs")
    if not isinstance(prefs, dict):
        raise bad("Send your choices as prefs: {kind: [channel, ...]}.")
    try:
        outbox.save_prefs(req.uid, prefs)
    except ValueError as e:
        raise bad(str(e))
    return my_notifications(req)


@api("GET", "/api/notify/settings", role="admin")
def notify_settings(req: Req):
    return {"smtp": mailer.masked(), "security": mailer.SECURITY, "base_url": outbox.base_url(),
            "channels": [c.to_dict() for c in outbox.channels()], "kinds": _kinds()}


@api("PUT", "/api/notify/settings", role="admin")
def put_notify_settings(req: Req):
    body = req.body if isinstance(req.body, dict) else {}
    try:
        if isinstance(body.get("smtp"), dict):
            mailer.save(body["smtp"])
        if "base_url" in body:
            outbox.save_settings({"base_url": body["base_url"]})
    except ValueError as e:
        raise bad(str(e))
    db.audit(req.uid, "notify_settings", "settings", None, "")
    return notify_settings(req)


def _test_notice(req: Req, email: str) -> Notice:
    return Notice(title="Test message from Kosha",
                  body="If you can read this, Kosha can reach you here.\nNotices about change orders waiting for "
                       "approval, failed ERP transfers and releases arrive the same way.",
                  link=outbox.absolute_link("#/home"), kind="test",
                  recipients=(Recipient(req.user["full_name"], email, req.uid),), key=f"test:{uuid.uuid4().hex}")


@api("POST", "/api/notify/email/check", role="admin")
def email_check(req: Req):
    h = mailer.EmailNotifier(cfg=mailer.settings()).health()
    return {"ok": h.ok, "message": h.message}


@api("POST", "/api/notify/email/test", role="admin")
def email_test(req: Req):
    to = (req.b("to") or "").strip() or (db.scalar("SELECT email FROM users WHERE id = ?", (req.uid,)) or "")
    if not to:
        raise bad("Give an e-mail address to send the test message to.")
    try:
        mailer.send(_test_notice(req, to))
    except AddinError as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": f"Sent to {to}. If it does not arrive within a few minutes, look in the spam folder."}


@api("POST", "/api/notify/channels/{channel}/test", role="admin")
def channel_test(req: Req):
    cid = req.params["channel"]
    if cid == "email":
        return email_test(req)
    known = {c.id for c in outbox.channels()}
    if cid not in known:
        raise not_found("There is no notification channel with that name.")
    notice = _test_notice(req, db.scalar("SELECT email FROM users WHERE id = ?", (req.uid,)) or "")
    try:
        if cid in outbox._custom:
            outbox._custom[cid].send(notice)
        else:
            registry.call(cid, "send", notice, timeout=outbox.SEND_TIMEOUT_SECONDS)
    except AddinError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:  # a custom channel's own bug: say so, never crash the admin page
        return {"ok": False, "message": f"The channel failed: {e.__class__.__name__}: {e}"}
    return {"ok": True, "message": "Sent. Look for the test message in the channel."}


@api("GET", "/api/notify/outbox", role="admin")
def outbox_log(req: Req):
    return outbox.log_rows(req.q("status") or None, int(req.q("limit", 200)))


@api("POST", "/api/notify/outbox/{id:int}/retry", role="admin")
def outbox_retry(req: Req):
    row = outbox.retry(req.int_param("id"))
    if not row:
        raise not_found("That notice is not in the outbox.")
    return {"row": row}


@api("POST", "/api/notify/process", role="admin")
def outbox_process(req: Req):
    return {"processed": outbox.process_due()}
