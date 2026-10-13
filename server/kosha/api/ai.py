# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI assistance (optional): which features are on, suggestions for parts and change logs, and "Ask Kosha".

Every request returns a suggestion record. When the provider answers quickly it is already ``ready``; otherwise
it is ``pending`` and the screen asks again with ``GET /api/ai/suggestions/{id}``. Nothing here changes a part,
file or change log except ``accept``, which the person presses after reviewing (and possibly editing) it.
"""
from __future__ import annotations

from .. import ai
from ..web import Req, api, as_bool, bad


def _wait(req: Req) -> float:
    try:
        return float(req.q("wait", req.b("wait", 2)) or 0)
    except (TypeError, ValueError):
        return 2.0


@api("GET", "/api/ai/status")
def status(req: Req):
    """Which AI features the screens may offer now. All false when AI is off (Kosha works fully without it)."""
    return ai.status()


@api("POST", "/api/ai/classify", role="engineer")
def classify(req: Req):
    """Suggest a category, material, matching standard part and a cleaner description for an item or a file."""
    ai.require("classify")
    subject = req.b("subject") or "item"
    try:
        sid = int(req.b("id"))
    except (TypeError, ValueError):
        raise bad("Say which item or file: id is missing.")
    return ai.submit(req.user, ai.prepare_classification(req.user, subject, sid), _wait(req))


@api("POST", "/api/ai/changelog", role="engineer")
def changelog_draft(req: Req):
    """Draft the change log of a file revision or a change order from the measured changes and the note."""
    ai.require("changelog")
    subject = req.b("subject") or "document"
    try:
        sid = int(req.b("id"))
    except (TypeError, ValueError):
        raise bad("Say which file or change order: id is missing.")
    p = ai.prepare_changelog(req.user, subject, sid, req.b("revision") or "", req.b("note") or "")
    return ai.submit(req.user, p, _wait(req))


@api("POST", "/api/ai/ask")
def ask(req: Req):
    """Answer a question from the records the asking person may open, with links to the records cited."""
    ai.require("ask")
    return ai.submit(req.user, ai.prepare_question(req.user, req.b("question") or ""), _wait(req))


@api("GET", "/api/ai/suggestions/{id:int}")
def suggestion(req: Req):
    return ai.get(req.int_param("id"), req.user)


@api("POST", "/api/ai/suggestions/{id:int}/accept", role="engineer")
def accept(req: Req):
    """Apply the ticked suggestions (with the person's edits) to the part; recorded in the audit log."""
    return ai.accept_classification(req.user, req.int_param("id"), req.b("fields") or {},
                                    as_bool(req.b("mark_standard")))


@api("POST", "/api/ai/suggestions/{id:int}/dismiss")
def dismiss(req: Req):
    return ai.dismiss(req.int_param("id"), req.user)


# ------------------------------------------------------------------------------------------------ admin --

def _admin_view() -> dict:
    return {"settings": ai.settings(), "providers": ai.providers(), "features": ai.FEATURES,
            "send_options": ai.SEND_OPTIONS, "status": ai.status(), "requests": ai.recent(30)}


@api("GET", "/api/ai/settings", role="admin")
def get_settings(req: Req):
    return _admin_view()


@api("PUT", "/api/ai/settings", role="admin")
def put_settings(req: Req):
    try:
        ai.save_settings(req.body if isinstance(req.body, dict) else {}, req.uid)
    except ValueError as e:
        raise bad(str(e))
    return _admin_view()


@api("POST", "/api/ai/test", role="admin")
def test(req: Req):
    """Health of the chosen (or named) provider and one tiny request through the guard."""
    return ai.test_provider(req.b("provider") or None)


@api("GET", "/api/ai/requests/{id:int}/sent", role="admin")
def sent(req: Req):
    """Exactly what was sent to the AI provider for one request."""
    return ai.sent(req.int_param("id"))
