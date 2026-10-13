# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Slack notifier: posts each notice to one channel through an incoming webhook.

The webhook address is a secret (anyone who has it can post to the channel), so it is never shown
or logged in full.
"""
from __future__ import annotations

import urllib.parse

from kosha_sdk import AddinContext, Health
from kosha_sdk.http import HttpError
from kosha_sdk.notify import Notice, Notifier, NotifyError

MARK = {"error": ":red_circle:", "warning": ":warning:", "success": ":white_check_mark:", "info": ":information_source:"}
SECTION_LIMIT = 3000
SLACK_ERRORS = {
    "invalid_token": "Slack no longer accepts this webhook address. Create a new one and paste it in the settings.",
    "no_service": "This webhook address does not exist in Slack (any more). Create a new one and paste it in the settings.",
    "no_team": "The Slack workspace of this webhook address no longer exists.",
    "team_disabled": "The Slack workspace of this webhook address is switched off.",
    "channel_not_found": "The Slack channel of this webhook no longer exists.",
    "channel_is_archived": "The Slack channel of this webhook is archived.",
    "action_prohibited": "A Slack admin has blocked posting to this channel.",
    "posting_to_general_channel_denied": "Only Slack admins may post to this channel.",
    "invalid_payload": "Slack could not read the message (invalid_payload).",
}


def _esc(text: str) -> str:
    """Slack's own escaping for message text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_payload(n: Notice) -> dict:
    title = _esc(n.title.strip()[:300])
    head = f"{MARK.get(n.severity, MARK['info'])} *{title}*"
    blocks = [{"type": "section", "text": {"type": "mrkdwn",
                                           "text": head + ("\n" + _esc(n.short_body(SECTION_LIMIT - len(head) - 40))
                                                           if n.body.strip() else "")}}]
    if n.for_line():
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _esc(n.for_line())}]})
    if n.link.startswith(("https://", "http://")):
        blocks.append({"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Open in Kosha"},
                                                        "url": n.link}]})
    return {"text": n.title.strip()[:300], "blocks": blocks, "unfurl_links": False}


def plain_error(e: HttpError) -> str:
    code = " ".join((e.detail or "").split())[:100]
    if e.status is None:
        return f"{e} Kosha will try again later."
    if code in SLACK_ERRORS:
        return f"{SLACK_ERRORS[code]} (HTTP {e.status})"
    if e.status == 429:
        return "Slack asks Kosha to send fewer messages (HTTP 429). Kosha will try again later."
    if e.status >= 500:
        return f"Slack had a problem (HTTP {e.status}). Kosha will try again later."
    return f"Slack refused the message (HTTP {e.status}): {code or 'no reason given'}"


class SlackNotifier(Notifier):
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
            return Health.down("Paste the incoming webhook address from Slack first (it starts with https://).")
        where = f"Webhook address on {urllib.parse.urlsplit(self._host).hostname} is set."
        if not self._secure:
            return Health.up(f"{where} It is not HTTPS, so messages travel unencrypted. Send a test message to check it.")
        return Health.up(f"{where} Send a test message to check that Slack accepts it.")

    def send(self, notice: Notice) -> None:
        if self._http is None:
            raise NotifyError("The Slack webhook address is not set.")
        try:
            r = self._http.post(self._path, slack_payload(notice))
        except HttpError as e:
            raise NotifyError(plain_error(e), status=e.status) from None
        if (r.text or "").strip() not in ("", "ok"):
            raise NotifyError(f"Slack did not post the message: {' '.join(r.text.split())[:200]}")
