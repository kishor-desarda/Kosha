# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""In-memory stand-in for a server with the OpenAI-compatible API (models, chat completions, embeddings).

Nothing leaves the test process. ``STATE["mode"]`` makes it misbehave the way real servers do.
"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

KEY = "test-key-123"
MODELS = ["kosha-test-model", "kosha-embed", "kosha-vision", "kosha-whisper"]
# modes: ok | open (no key needed) | busy | broken | no_format (refuses response_format) | no_models | parts | cut
STATE: dict = {"requests": [], "mode": "ok", "reply": None}


def reset() -> None:
    STATE.update({"requests": [], "mode": "ok", "reply": None})


def _auth(request: Request):
    if STATE["mode"] == "open":
        return None
    if request.headers.get("authorization") != f"Bearer {KEY}":
        return JSONResponse({"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error"}},
                            status_code=401)
    return None


async def models(request: Request):
    bad = _auth(request)
    if bad:
        return bad
    if STATE["mode"] == "no_models":
        return PlainTextResponse("404 page not found", status_code=404)
    return JSONResponse({"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]})


async def chat(request: Request):
    bad = _auth(request)
    if bad:
        return bad
    mode = STATE["mode"]
    if mode == "busy":
        return JSONResponse({"error": {"message": "Rate limit reached"}}, status_code=429)
    if mode == "broken":
        return PlainTextResponse("upstream crashed", status_code=500)
    j = await request.json()
    STATE["requests"].append({"body": j, "auth": request.headers.get("authorization")})
    if j.get("model") not in MODELS:
        return JSONResponse({"error": {"message": f"model '{j.get('model')}' not found"}}, status_code=404)
    if mode == "no_format" and "response_format" in j:
        return JSONResponse({"error": {"message": "response_format is not supported"}}, status_code=400)
    if not isinstance(j.get("messages"), list) or not j["messages"]:
        return JSONResponse({"error": {"message": "messages must be a non-empty list"}}, status_code=400)
    wants_json = (j.get("response_format") or {}).get("type") == "json_object" or "JSON" in j["messages"][0]["content"]
    text = STATE["reply"] if STATE["reply"] is not None else ('{"ok": true}' if wants_json else "OK")
    content = [{"type": "text", "text": text[:3]}, {"type": "text", "text": text[3:]}] if mode == "parts" else text
    return JSONResponse({"id": "chatcmpl-1", "object": "chat.completion", "model": j["model"],
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                      "finish_reason": "length" if mode == "cut" else "stop"}],
                         "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}})


async def embeddings(request: Request):
    bad = _auth(request)
    if bad:
        return bad
    j = await request.json()
    STATE["requests"].append({"body": j, "auth": request.headers.get("authorization")})
    texts = j.get("input") if isinstance(j.get("input"), list) else [j.get("input")]
    data = [{"object": "embedding", "index": i, "embedding": [float(len(t)), 1.0, 0.5]} for i, t in enumerate(texts)]
    return JSONResponse({"object": "list", "data": list(reversed(data)), "model": j.get("model")})


async def transcriptions(request: Request):
    bad = _auth(request)
    if bad:
        return bad
    form = await request.form()
    f = form.get("file")
    data = await f.read()
    STATE["requests"].append({"form": {k: v for k, v in form.items() if k != "file"}, "file": f.filename,
                              "size": len(data), "type": f.content_type})
    if form.get("model") not in MODELS:
        return JSONResponse({"error": {"message": "model not found"}}, status_code=404)
    return JSONResponse({"text": " 32 teeth wala gear ka drawing dikhao "})


app = Starlette(routes=[Route("/v1/models", models), Route("/v1/chat/completions", chat, methods=["POST"]),
                        Route("/v1/embeddings", embeddings, methods=["POST"]),
                        Route("/v1/audio/transcriptions", transcriptions, methods=["POST"])])
