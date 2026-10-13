# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""AI provider contract: turn a short conversation into text, and (optionally) texts into vectors.

Kosha works fully without AI. When an admin chooses a provider, Kosha decides what may be sent, builds
the prompt from its own records, runs every call through the add-in guard with a time-out, checks the
answer and shows it as a suggestion that a person accepts or edits. A provider only talks to one AI
service:

    class MyProvider(kosha_sdk.ai.AiProvider):
        def health(self): ...
        def complete(self, messages, *, max_tokens=512, temperature=0.2, json_mode=False): ...
        def embed(self, texts): ...          # optional; the default raises AiNotSupported

Rules for providers:

* send nothing but the messages Kosha hands over, to the service in the add-in's settings;
* never log the messages or the answer (they may hold part descriptions and change reasons);
* raise ``AiError`` with a plain message when the service refuses or cannot be reached (Kosha shows
  "The AI assistant could not be reached; your work is safe" to the user and the message to the admin);
* keep no state between calls: Kosha may call ``complete`` again with the same messages after a retry.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

from .context import AddinContext, Health
from .errors import AddinError

ROLES = ("system", "user", "assistant")


class AiError(AddinError):
    """The AI service refused the request or could not be reached. Kosha keeps the user's work and says so."""


class AiNotSupported(AiError):
    """The provider does not offer this (optional) part of the contract, for example embeddings."""


@dataclass(frozen=True)
class Message:
    """One turn of the conversation: `system` (instructions), `user` (Kosha's request) or `assistant`.

    `images` are pictures for a vision model, as data URLs (``data:image/png;base64,...``): for example the
    title-block crop of a scanned drawing. A provider whose service cannot see pictures raises AiNotSupported."""
    role: str
    content: str
    images: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}, not {self.role!r}")
        if not isinstance(self.content, str):
            raise ValueError("content must be text")
        if any(not (isinstance(i, str) and i.startswith("data:image/")) for i in self.images):
            raise ValueError("images must be data URLs of pictures (data:image/...)")

    def to_dict(self) -> dict[str, Any]:
        """The OpenAI-style message: plain text, or text and pictures as content parts."""
        if not self.images:
            return {"role": self.role, "content": self.content}
        return {"role": self.role, "content": [{"type": "text", "text": self.content}] +
                [{"type": "image_url", "image_url": {"url": i}} for i in self.images]}


@dataclass(frozen=True)
class Completion:
    """What the service answered.

    `finish_reason` is "stop" when the answer is complete and "length" when it was cut at `max_tokens`
    (Kosha then says the answer may be incomplete). `usage` holds token counts when the service reports
    them ({"prompt_tokens": 120, "completion_tokens": 40}).
    """
    text: str
    model: str = ""
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


def conversation(system: str, user: str) -> list[Message]:
    """The usual two-message conversation: instructions, then the request."""
    return [Message("system", system), Message("user", user)]


_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """The JSON object or list in a model's answer, also when it is wrapped in ```json fences or a sentence.

    Raises ValueError when the answer holds no JSON. Kosha checks every field it reads afterwards;
    this only finds the JSON."""
    s = (text or "").strip()
    candidates = [s] + [m.group(1).strip() for m in _FENCE.finditer(s)]
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError:
            pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(s[i:])
                return value
            except ValueError:
                continue
    raise ValueError("The answer holds no JSON.")


class AiProvider(ABC):
    """Base class for AI providers. Subclasses get their settings and HTTP from the context.

    Kosha gives each AI job its own model when the admin names one (Admin › AI › Jobs). A provider that can use a
    model other than the one in its settings sets ``supports_model_choice = True`` and accepts ``model=`` in
    ``complete`` and ``transcribe``; Kosha passes it only then."""

    supports_model_choice = False

    def __init__(self, ctx: AddinContext):
        self.ctx = ctx

    @abstractmethod
    def health(self) -> Health:
        """Check the settings and, when possible without a paid call, that the service and model are there."""

    @abstractmethod
    def complete(self, messages: Sequence[Message], *, max_tokens: int = 512, temperature: float = 0.2,
                 json_mode: bool = False) -> Completion:
        """Answer the conversation. `json_mode` asks the service for a JSON answer when it can do that;
        Kosha also says so in the instructions, so a service without such a switch still works.
        Raise AiError with a plain message when it fails."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the same order. Optional."""
        raise AiNotSupported("This AI provider does not offer embeddings.")

    def transcribe(self, audio: bytes, *, mime: str = "audio/webm", language: str = "") -> str:
        """Speech to text (voice commands and notes in English, Hindi or a mix). Optional. `language` is a hint
        such as "hi" or "en", or empty to let the service find out."""
        raise AiNotSupported("This AI provider does not offer speech to text.")
