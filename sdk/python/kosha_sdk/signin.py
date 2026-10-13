# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Sign-in provider contract: let people sign in to Kosha with an account they already have.

Kosha keeps everything around the check: throttling of failed sign-ins, the sessions, linking a
directory account to a Kosha user, turning directory groups into Kosha roles, the audit log, and
the local ``admin`` account that always works when the directory is down. A provider only answers
one question: who is this person?

Two styles:

* **Password providers** (LDAP, Active Directory ...) check a user name and password typed into
  Kosha's own sign-in form::

      class MyDirectory(kosha_sdk.signin.PasswordProvider):
          def health(self): ...
          def authenticate(self, username, password):   # an Identity, or None for "wrong name or password"
              ...

* **Redirect providers** (OpenID Connect, SAML gateways ...) send the browser to another site and
  read the answer when it comes back::

      class MySso(kosha_sdk.signin.RedirectProvider):
          def health(self): ...
          def start(self, redirect_uri, state):          # a RedirectStart: where to send the browser
              ...
          def finish(self, redirect_uri, params, saved): # the Identity, or raise SignInError
              ...

Kosha creates ``state`` (and checks it on the way back, once), keeps ``RedirectStart.saved`` on the
server between ``start`` and ``finish``, and binds the round trip to the browser that started it.

Raise ``SignInError`` with plain words when the provider cannot check the sign-in (the directory
cannot be reached, the answer is not genuine). Kosha then tries the next way of signing in; a
SignInError never counts as a crash and never switches the add-in off.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from .context import AddinContext, Health
from .errors import AddinError

STYLES = ("password", "redirect")


class SignInError(AddinError):
    """The provider could not check this sign-in. The message says why, in words an admin can act on."""


@dataclass(frozen=True)
class Identity:
    """The person the provider vouches for.

    ``external_id`` must be stable and never reused for another person: a directory's object GUID or
    entry UUID, or an OpenID Connect issuer and subject. Kosha links accounts by it, never by name.
    """
    external_id: str
    username: str
    full_name: str = ""
    email: str = ""
    groups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.external_id, str) or not self.external_id.strip():
            raise ValueError("an Identity needs a non-empty external_id")
        if not isinstance(self.username, str) or not self.username.strip():
            raise ValueError("an Identity needs a non-empty username")
        object.__setattr__(self, "groups", tuple(str(g) for g in (self.groups or ()) if str(g).strip()))


@dataclass(frozen=True)
class RedirectStart:
    """Where to send the browser, and what ``finish`` needs to know afterwards (a nonce, a PKCE verifier).

    ``saved`` stays on the Kosha server; it never travels through the browser."""
    url: str
    saved: dict[str, Any] = field(default_factory=dict)


class SignInProvider(ABC):
    """Common base. Use ``PasswordProvider`` or ``RedirectProvider``."""

    style: str = ""

    def __init__(self, ctx: AddinContext):
        self.ctx = ctx

    @abstractmethod
    def health(self) -> Health:
        """Check the settings and, when possible, that the directory or provider can be reached."""

    def label(self) -> str:
        """The name people see: "Sign in with <label>". Defaults to the `label` setting."""
        return str(self.ctx.settings.get("label") or "").strip() or "single sign-on"

    def describe(self) -> dict[str, str]:
        """What Kosha needs to show this provider. Usually inherited as it is."""
        return {"style": self.style, "label": self.label()}


class PasswordProvider(SignInProvider):
    style = "password"

    @abstractmethod
    def authenticate(self, username: str, password: str) -> Identity | None:
        """Check a user name and password.

        Return the Identity when they are right, and None when the user is unknown or the password is
        wrong (never tell the two apart). Refuse an empty password: many directories treat a bind
        without one as an anonymous sign-in that "works". Raise SignInError when the directory cannot
        give an answer."""


class RedirectProvider(SignInProvider):
    style = "redirect"

    @abstractmethod
    def start(self, redirect_uri: str, state: str) -> RedirectStart:
        """Build the address of the provider's sign-in page. Put ``state`` in it unchanged; the provider
        must send the browser back to ``redirect_uri`` with it."""

    @abstractmethod
    def finish(self, redirect_uri: str, params: Mapping[str, str], saved: Mapping[str, Any]) -> Identity:
        """Read the answer the browser brought back (the query parameters of ``redirect_uri``).

        Kosha has already checked ``state``. Check everything else (codes, signatures, nonces, audience,
        expiry) and return the Identity, or raise SignInError saying what was wrong."""
