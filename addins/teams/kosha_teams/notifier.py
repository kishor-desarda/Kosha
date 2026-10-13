# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Teams notifier: posts each notice to one channel as an adaptive card.

Works with both kinds of address Teams hands out: a Workflows address ("Post to a channel when a
webhook request is received", answers 202) and an older incoming-webhook address (answers "1").
The address is a secret: anyone who has it can post to the channel, so it is never shown or logged
in full.
"""
from __future__ import annotations

import urllib.parse

from kosha_sdk import AddinContext, Health
from kosha_sdk.http import HttpError
from kosha_sdk.notify import Notice, Notifier, NotifyError

COLOUR = {"error": "Attention", "warning": "Warning", "success": "Good", "info": "Default"}
BODY_LIMIT = 3000


def adaptive_card(n: Notice) -> dict:
    """The message Teams expects: one adaptive card as an attachment."""
    body = [{"type": "TextBlock", "text": n.title.strip()[:300], "weight": "Bolder", "size": "Medium", "wrap": True,
             "color": COLOUR.get(n.severity, "Default")}]
    if n.body.strip():
        body.append({"type": "TextBlock", "text": n.short_body(BODY_LIMIT), "wrap": True})
    if n.for_line():
        body.append({"type": "TextBlock", "text": n.for_line(), "isSubtle": True, "size": "Small", "wrap": True})
    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
            "body": body, "msteams": {"width": "Full"}}
    if n.link.startswith(("https://", "http://")):
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in Kosha", "url": n.link}]
    return {"type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def plain_error(e: HttpError) -> str:
    if e.status is None:
        return f"{e} Kosha will try again later."
    if e.status in (401, 403):
        return f"Teams refused the webhook address (HTTP {e.status}). Create a new one in Teams and paste it in the settings."
    if e.status in (404, 410):
        return f"The webhook address no longer exists in Teams (HTTP {e.status}). Create a new one and paste it in the settings."
    if e.status == 413:
        return "Teams refused the message because it is too large (HTTP 413)."
    if e.status == 429:
        return "Teams asks Kosha to send fewer messages (HTTP 429). Kosha will try again later."
    if e.status >= 500:
        return f"Teams had a problem (HTTP {e.status}). Kosha will try again later."
    return f"Teams refused the message (HTTP {e.status}): {' '.join((e.detail or '').split())[:200]}"


class TeamsNotifier(Notifier):
    def __init__(self, ctx: AddinContext):
        super().__init__(ctx)
        s = ctx.settings
        parts = urllib.parse.urlsplit((s.get("webhook_url") or "").strip())
        ok = parts.scheme in ("http", "https") and bool(parts.netloc)
        self._host = f"{parts.scheme}://{parts.netloc}" if ok else ""
        self._path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self._secure = parts.scheme == "https"
        # no automatic repeat inside one call: Kosha's outbox retries later, so a slow answer never posts twice at once
        self._http = ctx.http(self._host, timeout_s=float(s.get("timeout_s") or 15),
                              verify_tls=bool(s.get("verify_tls", True)), retries=0) if ok else None

    def health(self) -> Health:
        if self._http is None:
            return Health.down("Paste the webhook address from Teams first (it starts with https://).")
        where = f"Webhook address on {urllib.parse.urlsplit(self._host).hostname} is set."
        if not self._secure:
            return Health.up(f"{where} It is not HTTPS, so messages travel unencrypted. Send a test message to check it.")
        return Health.up(f"{where} Send a test message to check that Teams accepts it.")

    def send(self, notice: Notice) -> None:
        if self._http is None:
            raise NotifyError("The Teams webhook address is not set.")
        try:
            r = self._http.post(self._path, adaptive_card(notice))
        except HttpError as e:
            raise NotifyError(plain_error(e), status=e.status) from None
        text = (r.text or "").strip()
        # older incoming webhooks answer 200 with an error text instead of an error status
        if r.status == 200 and text not in ("", "1") and any(w in text.lower() for w in ("fail", "error", "invalid")):
            raise NotifyError(f"Teams did not post the message: {' '.join(text.split())[:200]}")
