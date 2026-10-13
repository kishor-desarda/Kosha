# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""In-memory stand-in for a Slack incoming webhook. Answers like Slack does: "ok", or an error code as text."""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

TEAM, HOOK, SECRET = "T000", "B000", "good-secret"
STATE: dict = {"posts": [], "mode": "ok"}      # ok | archived | busy | broken


def reset() -> None:
    STATE.update({"posts": [], "mode": "ok"})


async def hook(request: Request):
    p = request.path_params
    if (p["team"], p["hook"]) != (TEAM, HOOK):
        return PlainTextResponse("no_service", status_code=404)
    if p["secret"] != SECRET:
        return PlainTextResponse("invalid_token", status_code=403)
    mode = STATE["mode"]
    if mode == "archived":
        return PlainTextResponse("channel_is_archived", status_code=410)
    if mode == "busy":
        return PlainTextResponse("rate_limited", status_code=429)
    if mode == "broken":
        return PlainTextResponse("internal_error", status_code=500)
    try:
        j = await request.json()
    except ValueError:
        return PlainTextResponse("invalid_payload", status_code=400)
    if not isinstance(j.get("text"), str) or not j["text"]:
        return PlainTextResponse("invalid_payload", status_code=400)
    for b in j.get("blocks", []):
        if b.get("type") == "section" and len(b["text"]["text"]) > 3000:
            return PlainTextResponse("invalid_blocks", status_code=400)
        if b.get("type") == "actions" and not b["elements"][0]["url"].startswith(("http://", "https://")):
            return PlainTextResponse("invalid_blocks", status_code=400)
    STATE["posts"].append(j)
    return PlainTextResponse("ok")


app = Starlette(routes=[Route("/services/{team}/{hook}/{secret}", hook, methods=["POST"])])
