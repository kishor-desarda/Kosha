# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""In-memory stand-in for the Telegram Bot API: getMe and sendMessage, with Telegram's error answers."""
from __future__ import annotations

from html.parser import HTMLParser

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

TOKEN, CHAT, BOT = "123456:TEST-token_x", "-1001234567890", "kosha_test_bot"
STATE: dict = {"messages": [], "kicked": False}
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "s", "a", "code", "pre"}


def reset() -> None:
    STATE.update({"messages": [], "kicked": False})


def _fail(code: int, description: str):
    return JSONResponse({"ok": False, "error_code": code, "description": description}, status_code=code)


class _Check(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bad: list[str] = []
        self.text = ""

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.bad.append(tag)

    def handle_data(self, data):
        self.text += data


async def api(request: Request):
    token, method = request.path_params["token"], request.path_params["method"]
    if token != TOKEN:
        return _fail(401, "Unauthorized")
    if method == "getMe":
        return JSONResponse({"ok": True, "result": {"id": 123456, "is_bot": True, "username": BOT}})
    if method != "sendMessage":
        return _fail(404, "Not Found")
    j = await request.json()
    if str(j.get("chat_id")) != CHAT:
        return _fail(400, "Bad Request: chat not found")
    if STATE["kicked"]:
        return _fail(403, "Forbidden: bot was kicked from the group chat")
    text = j.get("text") or ""
    if j.get("parse_mode") == "HTML":
        chk = _Check()
        chk.feed(text)
        if chk.bad:
            return _fail(400, f"Bad Request: can't parse entities: Unsupported start tag \"{chk.bad[0]}\"")
        plain = chk.text
    else:
        plain = text
    if not plain.strip():
        return _fail(400, "Bad Request: message text is empty")
    if len(plain) > 4096:
        return _fail(400, "Bad Request: message is too long")
    STATE["messages"].append(j)
    return JSONResponse({"ok": True, "result": {"message_id": len(STATE["messages"]), "text": plain}})


app = Starlette(routes=[Route("/bot{token}/{method}", api, methods=["POST"])])
