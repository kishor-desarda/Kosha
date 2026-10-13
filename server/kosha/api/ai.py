# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI assistance (optional): which features are on, suggestions for parts and change logs, and "Ask Kosha".

Every request returns a suggestion record. When the provider answers quickly it is already ``ready``; otherwise
it is ``pending`` and the screen asks again with ``GET /api/ai/suggestions/{id}``. Nothing here changes a part,
file or change log except ``accept``, which the person presses after reviewing (and possibly editing) it.
"""
from __future__ import annotations

from .. import ai, ai_jobs
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


def _id(req: Req, what: str) -> int:
    try:
        return int(req.b("id"))
    except (TypeError, ValueError):
        raise bad(f"Say which {what}: id is missing.")


@api("GET", "/api/documents/{id:int}/drawing-check")
def drawing_check(req: Req):
    """Kosha's own checks of a drawing before approval (title block, material, revision, notes); no AI."""
    out = ai_jobs.drawing_check(req.int_param("id"))
    out.pop("_texts", None)
    return out


@api("POST", "/api/ai/drawing-check", role="engineer")
def ai_drawing_check(req: Req):
    """After the rules: what a checker would still question, as a suggestion."""
    ai.require("drawing_check")
    return ai.submit(req.user, ai_jobs.prepare_drawing_check(req.user, _id(req, "file")), _wait(req))


@api("GET", "/api/common-parts/groups/{id:int}/why")
def duplicate_why(req: Req):
    """Kosha's own line on why the parts of a group look alike and what differs (no AI)."""
    _, _, line = ai_jobs.duplicate_facts(req.int_param("id"))
    return {"line": line}


@api("POST", "/api/ai/duplicates", role="engineer")
def ai_duplicates(req: Req):
    """One sentence on why the parts of a group look alike and what differs."""
    ai.require("duplicates")
    return ai.submit(req.user, ai_jobs.prepare_duplicate(req.user, _id(req, "group")), _wait(req))


@api("POST", "/api/ai/ocr", role="engineer")
def ai_ocr(req: Req):
    """Read the title block of a scanned drawing (only its corner is sent); accepting fills in the part."""
    ai.require("ocr")
    return ai.submit(req.user, ai_jobs.prepare_ocr(req.user, _id(req, "file")), _wait(req))


@api("POST", "/api/ai/transcribe")
def transcribe(req: Req):
    """Speech to text for a spoken question: the recording in "file", the language hint in "language"."""
    files = req.files.get("file") or []
    if not files:
        raise bad("Send the recording as the file \"file\".")
    f = files[0]
    data = f.file.read(ai_jobs.MAX_AUDIO + 1)
    return ai_jobs.transcribe(req.user, data, getattr(f, "content_type", "") or "audio/webm",
                              str(req.b("language") or ""))


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
            "send_options": ai.SEND_OPTIONS, "status": ai.status(), "requests": ai.recent(30), "usage": ai.usage()}


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


@api("GET", "/api/ai/usage", role="admin")
def usage(req: Req):
    """Calls, tokens and cost per job for a month (?month=2026-10), the budget, and the acceptance rate per job."""
    month = str(req.q("month") or "")
    if month and not (len(month) == 7 and month[4] == "-" and month.replace("-", "").isdigit()):
        raise bad("Give the month as YYYY-MM, for example 2026-10.")
    return ai.usage(month or None)
