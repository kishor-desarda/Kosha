# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Notifier contract: deliver a short notice (e-mail, a chat channel, a phone app ...).

Kosha keeps the outbox: it decides who gets what, retries with growing waits, and never lets a
slow or broken notifier hold up the action that caused the notice. A notifier only delivers
one `Notice` to its own channel:

    class Notifier(kosha_sdk.notify.Notifier):
        def health(self): ...
        def send(self, notice): ...        # raise NotifyError with a plain message when it fails

`send` may be called again for the same notice after a crash or a retry; use `notice.key`
when the other system can drop repeats.
"""
from __future__ import annotations

import html
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .context import AddinContext, Health
from .errors import AddinError

SEVERITIES = ("info", "success", "warning", "error")


class NotifyError(AddinError):
    """The channel refused the notice or could not be reached. Kosha keeps the notice and tries again later."""


@dataclass(frozen=True)
class Recipient:
    """A person the notice is for. Chat notifiers usually only name them; e-mail needs the address."""
    name: str
    email: str = ""
    user_id: int | None = None


@dataclass(frozen=True)
class Notice:
    title: str
    body: str = ""                           # plain text; lines are kept
    link: str = ""                           # full address of the page in Kosha, or empty
    severity: str = "info"                   # info | success | warning | error
    kind: str = ""                           # approvals, mentions, erp_failures, releases, test ...
    recipients: tuple[Recipient, ...] = ()
    key: str = ""                            # the same key means the same notice (for dropping repeats)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            object.__setattr__(self, "severity", "info")

    def for_line(self) -> str:
        """'For: Daniel Okafor, Sofia Lindqvist' or '' when the notice names nobody."""
        names = [r.name for r in self.recipients if r.name]
        return f"For: {', '.join(names)}" if names else ""

    def short_body(self, limit: int) -> str:
        """The body cut to `limit` characters, ending with an ellipsis when it was cut."""
        b = self.body.strip()
        return b if len(b) <= limit else b[: max(0, limit - 1)].rstrip() + "…"

    def text(self) -> str:
        """Plain-text version: title, body, who it is for and the link."""
        parts = [self.title.strip()]
        if self.body.strip():
            parts.append(self.body.strip())
        if self.for_line():
            parts.append(self.for_line())
        if self.link:
            parts.append(f"Open in Kosha: {self.link}")
        return "\n\n".join(parts) + "\n"

    def html(self) -> str:
        """A small, safe HTML version (everything escaped) for e-mail and chat programs that show HTML."""
        colour = {"error": "#8B1D16", "warning": "#6B4E00", "success": "#14532D"}.get(self.severity, "#16191D")
        body = "<br>".join(html.escape(line) for line in self.body.strip().splitlines())
        out = [f'<h2 style="margin:0 0 12px;font-size:18px;color:{colour}">{html.escape(self.title.strip())}</h2>']
        if body:
            out.append(f'<p style="margin:0 0 12px;line-height:1.5">{body}</p>')
        if self.for_line():
            out.append(f'<p style="margin:0 0 12px;color:#5A6167;font-size:13px">{html.escape(self.for_line())}</p>')
        if self.link:
            out.append(f'<p style="margin:0"><a href="{html.escape(self.link, quote=True)}">Open in Kosha</a></p>')
        return "\n".join(out)


class Notifier(ABC):
    """Base class for notifiers. Subclasses get their settings and HTTP from the context."""

    def __init__(self, ctx: AddinContext):
        self.ctx = ctx

    @abstractmethod
    def health(self) -> Health:
        """Check the settings (and the connection when that is possible without posting a message)."""

    @abstractmethod
    def send(self, notice: Notice) -> None:
        """Deliver one notice. Raise NotifyError with a plain message when it cannot be delivered."""
