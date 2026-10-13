# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""In-memory stand-in for a Teams webhook (both the Workflows kind and the older incoming-webhook kind)."""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

TOKEN = "good-token"
STATE: dict = {"posts": [], "mode": "workflow"}   # workflow | legacy | legacy_error | busy | broken


def reset() -> None:
    STATE.update({"posts": [], "mode": "workflow"})


def _card_problem(j) -> str:
    try:
        att = j["attachments"][0]
        card = att["content"]
    except (KeyError, IndexError, TypeError):
        return "no adaptive card attachment"
    if j.get("type") != "message" or att.get("contentType") != "application/vnd.microsoft.card.adaptive":
        return "wrong message or content type"
    if card.get("type") != "AdaptiveCard" or not card.get("body"):
        return "the card has no body"
    for b in card["body"]:
        if b.get("type") != "TextBlock" or not isinstance(b.get("text"), str):
            return "unknown element"
    for a in card.get("actions", []):
        if a.get("type") != "Action.OpenUrl" or not str(a.get("url", "")).startswith(("http://", "https://")):
            return "bad action"
    return ""


async def hook(request: Request):
    if request.path_params["token"] != TOKEN:
        return PlainTextResponse("Not found", status_code=404)
    mode = STATE["mode"]
    if mode == "busy":
        return PlainTextResponse("Too many requests", status_code=429)
    if mode == "broken":
        return PlainTextResponse("Internal error", status_code=500)
    j = await request.json()
    problem = _card_problem(j)
    if problem:
        return JSONResponse({"error": {"code": "BadRequest", "message": problem}}, status_code=400)
    STATE["posts"].append(j)
    if mode == "legacy_error":
        return PlainTextResponse("Webhook message delivery failed with error: Microsoft Teams endpoint returned HTTP error 403")
    if mode == "legacy":
        return PlainTextResponse("1")
    return Response(status_code=202)


app = Starlette(routes=[Route("/workflows/{token}/run", hook, methods=["POST"])])
