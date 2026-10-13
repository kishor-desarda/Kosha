# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""AI provider for any server with the OpenAI-compatible HTTP API (``/chat/completions``, ``/models``,
``/embeddings``): hosted services and self-hosted runners alike.

Only the messages Kosha hands over are sent, only to the address in the settings. Messages and answers are never
logged; the API key is never shown or put into an error message.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Sequence

from kosha_sdk import AddinContext, Health
from kosha_sdk.ai import AiError, AiNotSupported, AiProvider, Completion, Message
from kosha_sdk.http import HttpError, multipart_body

DEFAULT_BASE_URL = "http://localhost:11434/v1"


def _detail(e: HttpError) -> str:
    """The server's own error message, short, from {"error": {"message": ...}} or {"error": "..."} or plain text."""
    raw = e.detail or ""
    msg: Any = raw
    try:
        j = json.loads(raw)
        err = j.get("error", j) if isinstance(j, dict) else j
        msg = (err.get("message") or err.get("detail") or err) if isinstance(err, dict) else err
    except ValueError:
        pass
    return " ".join(str(msg).split())[:200]


class OpenAICompatibleProvider(AiProvider):
    supports_model_choice = True        # Kosha may name a model per AI job; the settings' model is the default

    def __init__(self, ctx: AddinContext):
        super().__init__(ctx)
        s = ctx.settings
        self.base = (s.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
        self.model = (s.get("model") or "").strip()
        self.embedding_model = (s.get("embedding_model") or "").strip()
        self.speech_model = (s.get("speech_model") or "").strip()
        self.json_mode = bool(s.get("json_mode", True))
        key = (s.get("api_key") or "").strip()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        parts = urllib.parse.urlsplit(self.base)
        self._host = parts.netloc or self.base
        self._secure = parts.scheme == "https"
        self._ok = parts.scheme in ("http", "https") and bool(parts.netloc)
        # one quick repeat for a dropped connection or a 5xx; Kosha's guard and the person's "Try again" do the rest
        self._http = ctx.http(self.base, headers=headers, timeout_s=float(s.get("timeout_s") or 60),
                              verify_tls=bool(s.get("verify_tls", True)), retries=1) if self._ok else None
        self._no_format = False      # the server refused response_format once: do not send it again

    # ------------------------------------------------------------------------------------------ errors --
    def _plain(self, e: HttpError, what: str = "request", model: str = "") -> AiError:
        st = e.status
        if st is None:
            return AiError(f"Cannot reach the AI server at {self._host}. Check that it is running and that the address "
                           "is right.")
        if st in (401, 403):
            return AiError(f"The AI server refused the API key (HTTP {st}). Check the key in the settings.", status=st)
        if st == 404:
            return AiError(f"The AI server does not know the model '{model or self.model}', or the address is wrong (HTTP 404). "
                           "The address usually ends in /v1; check the model name as the server lists it.", status=st)
        if st == 429:
            return AiError("The AI server is busy or its usage limit is reached (HTTP 429). Try again later.", status=st)
        if st >= 500:
            return AiError(f"The AI server had a problem (HTTP {st}). Try again later.", status=st)
        d = _detail(e)
        return AiError(f"The AI server refused the {what} (HTTP {st})" + (f": {d}" if d else "."), status=st)

    # ----------------------------------------------------------------------------------------- contract --
    def health(self) -> Health:
        if self._http is None:
            return Health.down("Enter the address of the AI server first (it starts with http:// or https://).")
        if not self.model:
            return Health.down("Enter the model name first.")
        try:
            r = self._http.get("/models", allow_404=True)
        except HttpError as e:
            return Health.down(str(self._plain(e, "list of models")))
        plain = "" if self._secure or self._host.split(":")[0] in ("localhost", "127.0.0.1") else \
            " The address is not HTTPS, so requests travel unencrypted; use it only inside your own network."
        if r is None:
            return Health.up(f"Connected to {self._host}. The server does not list its models; press Test to send a "
                             f"short request to {self.model}.{plain}")
        try:
            ids = [str(m.get("id")) for m in (r.json().get("data") or []) if isinstance(m, dict)]
        except (ValueError, AttributeError):
            ids = []
        if ids and self.model not in ids:
            offered = ", ".join(ids[:8]) + (" …" if len(ids) > 8 else "")
            return Health.down(f"The AI server at {self._host} does not offer the model '{self.model}'. It offers: "
                               f"{offered}.")
        return Health.up(f"Connected to {self._host}; model {self.model} is available.{plain}")

    def complete(self, messages: Sequence[Message], *, max_tokens: int = 512, temperature: float = 0.2,
                 json_mode: bool = False, model: str | None = None) -> Completion:
        if self._http is None:
            raise AiError("The address of the AI server is not set.")
        model = (model or self.model or "").strip()
        if not model:
            raise AiError("The model name is not set.")
        body: dict[str, Any] = {"model": model, "messages": [m.to_dict() for m in messages],
                                "max_tokens": int(max_tokens), "temperature": float(temperature), "stream": False}
        if json_mode and self.json_mode and not self._no_format:
            body["response_format"] = {"type": "json_object"}
        try:
            r = self._http.post("/chat/completions", body)
        except HttpError as e:
            if e.status in (400, 422) and "response_format" in body:
                self._no_format = True          # an older server: the instructions already ask for JSON
                body.pop("response_format")
                try:
                    r = self._http.post("/chat/completions", body)
                except HttpError as e2:
                    raise self._plain(e2, model=model) from None
            else:
                raise self._plain(e, model=model) from None
        try:
            j = r.json()
            choice = (j.get("choices") or [])[0]
        except (ValueError, IndexError, AttributeError, TypeError):
            raise AiError("The AI server answered in a form Kosha does not understand (no choices).") from None
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, list):     # some servers answer with parts: [{"type": "text", "text": "..."}]
            content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        usage = {k: int(v) for k, v in (j.get("usage") or {}).items()
                 if k in ("prompt_tokens", "completion_tokens", "total_tokens") and isinstance(v, (int, float))}
        return Completion(text=str(content or ""), model=str(j.get("model") or model),
                          finish_reason=str(choice.get("finish_reason") or "stop"), usage=usage)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.embedding_model:
            raise AiNotSupported("No embedding model is set for this AI provider.")
        if self._http is None:
            raise AiError("The address of the AI server is not set.")
        try:
            r = self._http.post("/embeddings", {"model": self.embedding_model, "input": list(texts)})
        except HttpError as e:
            raise self._plain(e, "embedding request") from None
        try:
            data = sorted(r.json().get("data") or [], key=lambda d: d.get("index", 0))
            out = [[float(x) for x in d["embedding"]] for d in data]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AiError("The AI server answered the embedding request in a form Kosha does not understand.") from None
        if len(out) != len(texts):
            raise AiError("The AI server returned a different number of embeddings than texts.")
        return out

    def transcribe(self, audio: bytes, *, mime: str = "audio/webm", language: str = "",
                   model: str | None = None) -> str:
        """Speech to text through ``/audio/transcriptions`` (Whisper-type models)."""
        model = (model or self.speech_model or "").strip()
        if not model:
            raise AiNotSupported("No speech model is set for this AI provider.")
        if self._http is None:
            raise AiError("The address of the AI server is not set.")
        ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3",
               "audio/mp4": "m4a"}.get(mime.split(";")[0].strip(), "webm")
        fields = {"model": model, "response_format": "json"}
        if language:
            fields["language"] = language
        try:
            r = self._http.request("POST", "/audio/transcriptions",
                                   raw=multipart_body(fields, {"file": (f"speech.{ext}", audio, mime)}))
        except HttpError as e:
            raise self._plain(e, "speech request", model=model) from None
        try:
            return str(r.json().get("text") or "").strip()
        except (ValueError, AttributeError):
            raise AiError("The AI server answered the speech request in a form Kosha does not understand.") from None
