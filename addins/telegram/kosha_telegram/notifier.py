# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Telegram notifier: a bot posts each notice to one chat (a group, or a topic in a group) with sendMessage.

The bot token is part of the address of every call, so it is never shown or logged: error messages
name the Telegram server only.
"""
from __future__ import annotations

import html
import json

from kosha_sdk import AddinContext, Health
from kosha_sdk.http import HttpError
from kosha_sdk.notify import Notice, Notifier, NotifyError

TEXT_LIMIT = 4096


def telegram_html(n: Notice) -> str:
    """The message in Telegram's HTML subset (b, i, a), everything else escaped, at most 4096 characters."""
    head = f"<b>{html.escape(n.title.strip()[:300], quote=False)}</b>"
    tail = []
    if n.for_line():
        tail.append(f"<i>{html.escape(n.for_line(), quote=False)}</i>")
    if n.link.startswith(("https://", "http://")):
        tail.append(f'<a href="{html.escape(n.link, quote=True)}">Open in Kosha</a>')
    room = TEXT_LIMIT - len(head) - sum(len(t) + 2 for t in tail) - 2
    parts = [head]
    if n.body.strip() and room > 20:
        body = n.short_body(room)
        escaped = html.escape(body, quote=False)
        while len(escaped) > room:           # escaping makes text longer; cut until it fits
            body = body[: max(0, len(body) - (len(escaped) - room) - 1)].rstrip() + "…"
            escaped = html.escape(body, quote=False)
        parts.append(escaped)
    return "\n\n".join(parts + tail)


def _description(e: HttpError) -> str:
    try:
        return str(json.loads(e.detail or "{}").get("description") or "")
    except ValueError:
        return ""


def plain_error(e: HttpError) -> str:
    what = _description(e)
    if e.status is None:
        return f"{e} Kosha will try again later."
    if e.status in (401, 404):
        return f"Telegram does not accept the bot token (HTTP {e.status}). Copy the token again and paste it in the settings."
    if e.status == 400 and "chat not found" in what.lower():
        return "Telegram cannot find the chat. Add the bot to the group, then check the chat id in the settings."
    if e.status == 403:
        return f"The bot may not post in this chat (Telegram: {what or 'forbidden'}). Add it to the group again."
    if e.status == 429:
        return "Telegram asks Kosha to send fewer messages (HTTP 429). Kosha will try again later."
    if e.status >= 500:
        return f"Telegram had a problem (HTTP {e.status}). Kosha will try again later."
    return f"Telegram refused the message (HTTP {e.status}): {what or 'no reason given'}"


class TelegramNotifier(Notifier):
    def __init__(self, ctx: AddinContext):
        super().__init__(ctx)
        s = ctx.settings
        self._token = (s.get("bot_token") or "").strip()
        self._chat = str(s.get("chat_id") or "").strip()
        thread = str(s.get("topic_id") or "").strip()
        self._thread = int(thread) if thread.lstrip("-").isdigit() else None
        base = (s.get("api_base") or "https://api.telegram.org").strip().rstrip("/")
        # no automatic repeat inside one call: Kosha's outbox retries later, so a slow answer never posts twice at once
        self._http = ctx.http(base, timeout_s=float(s.get("timeout_s") or 15), verify_tls=bool(s.get("verify_tls", True)),
                              retries=0)

    def _api(self, method: str, body: dict | None = None):
        if not self._token:
            raise NotifyError("The Telegram bot token is not set.")
        try:
            r = self._http.post(f"/bot{self._token}/{method}", body or {})
        except HttpError as e:
            raise NotifyError(plain_error(e), status=e.status) from None
        try:
            j = r.json()
        except ValueError:
            raise NotifyError("Telegram's answer could not be read. Check the API address in the settings.") from None
        if not isinstance(j, dict) or not j.get("ok"):
            raise NotifyError(f"Telegram refused the call: {(j or {}).get('description', 'no reason given')}")
        return j.get("result") or {}

    def health(self) -> Health:
        try:
            me = self._api("getMe")
        except NotifyError as e:
            return Health.down(str(e))
        name = f"@{me.get('username')}" if me.get("username") else "the bot"
        if not self._chat:
            return Health.down(f"Connected as {name}, but the chat id is not set.")
        return Health.up(f"Connected as {name}. Messages go to chat {self._chat}. Send a test message to check the chat.")

    def send(self, notice: Notice) -> None:
        if not self._chat:
            raise NotifyError("The Telegram chat id is not set.")
        body = {"chat_id": self._chat, "text": telegram_html(notice), "parse_mode": "HTML",
                "disable_web_page_preview": True}
        if self._thread:
            body["message_thread_id"] = self._thread
        self._api("sendMessage", body)
