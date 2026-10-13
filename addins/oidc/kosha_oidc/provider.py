# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""OpenID Connect sign-in: the authorization-code flow with PKCE (RFC 6749, RFC 7636, OpenID Connect Core 1.0).

1. ``start`` reads the provider's discovery document, makes a fresh nonce and PKCE verifier (kept on the Kosha
   server) and sends the browser to the provider's sign-in page.
2. ``finish`` exchanges the code at the token endpoint (server to server, with the PKCE verifier and, for a
   confidential client, the client secret), then checks the ID token: its signature against the provider's
   published keys, the issuer, the audience (this client), the expiry and the nonce. Groups come from the ID
   token, or from the user-info endpoint when the token leaves them out.

Works with any standard provider (Keycloak, Authentik, Zitadel, Dex, cloud identity services ...). HTTP goes
only through the SDK's client (time-outs; the code exchange is never repeated, as a code works once).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse
from typing import Any, Mapping

import jwt

from kosha_sdk import AddinContext, Health
from kosha_sdk.http import HttpError
from kosha_sdk.signin import Identity, RedirectProvider, RedirectStart, SignInError

from .jose import b64url_encode, verify_signature

CACHE_SECONDS = 3600          # discovery document and keys are read again after an hour
LEEWAY_SECONDS = 60           # clocks of Kosha and the provider may differ this much


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/")


class OidcProvider(RedirectProvider):
    def __init__(self, ctx: AddinContext):
        super().__init__(ctx)
        s = ctx.settings
        self.issuer = _norm(s.get("issuer") or "")
        self.client_id = (s.get("client_id") or "").strip()
        self.client_secret = s.get("client_secret") or ""
        self.scopes = " ".join((s.get("scopes") or "openid profile email").split())
        if "openid" not in self.scopes.split():
            self.scopes = "openid " + self.scopes
        self.username_claim = (s.get("username_claim") or "preferred_username").strip()
        self.groups_claim = (s.get("groups_claim") or "groups").strip()
        self.timeout_s = float(s.get("timeout_s") or 10)
        self.verify_tls = bool(s.get("verify_tls", True))
        self._lock = threading.Lock()
        self._discovery: tuple[float, dict] | None = None
        self._keys: tuple[float, list] | None = None

    def label(self) -> str:
        return str(self.ctx.settings.get("label") or "").strip() or "single sign-on"

    # ------------------------------------------------------------------ HTTP
    def _client(self, url: str, retries: int = 1):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise SignInError(f"The provider gave an address Kosha cannot use: {url[:200]}")
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return self.ctx.http(f"{parts.scheme}://{parts.netloc}", timeout_s=self.timeout_s, verify_tls=self.verify_tls,
                             retries=retries), path

    def _get_json(self, url: str, what: str, headers: dict | None = None) -> dict:
        client, path = self._client(url)
        try:
            r = client.get(path, headers=headers)
            data = r.json()
        except HttpError as e:
            raise SignInError(f"Kosha could not read the {what} at {urllib.parse.urlsplit(url).netloc} ({e}).") from None
        except ValueError:
            raise SignInError(f"The {what} at {urllib.parse.urlsplit(url).netloc} is not valid JSON.") from None
        if not isinstance(data, dict):
            raise SignInError(f"The {what} at {urllib.parse.urlsplit(url).netloc} is not what Kosha expected.")
        return data

    def discovery(self, refresh: bool = False) -> dict:
        """The provider's discovery document (cached for an hour), checked against the configured issuer."""
        if not self.issuer or not self.client_id:
            raise SignInError("Fill in the issuer address and the client ID first.")
        with self._lock:
            if self._discovery and not refresh and time.monotonic() - self._discovery[0] < CACHE_SECONDS:
                return self._discovery[1]
        d = self._get_json(self.issuer + "/.well-known/openid-configuration", "discovery document")
        if _norm(d.get("issuer", "")) != self.issuer:
            raise SignInError(f"The provider calls itself {d.get('issuer')!r}, not {self.issuer!r}. Copy the issuer "
                              "address exactly as the provider shows it.")
        for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            v = d.get(k)
            if not isinstance(v, str) or not v.startswith(("https://", "http://")):
                raise SignInError(f"The provider's discovery document has no usable {k}.")
            if self.issuer.startswith("https://") and not v.startswith("https://"):
                raise SignInError(f"The provider's {k} is not HTTPS although its issuer is. Kosha does not send "
                                  "sign-in data over unencrypted connections in that case.")
        with self._lock:
            self._discovery = (time.monotonic(), d)
        return d

    def keys(self, refresh: bool = False) -> list[dict]:
        with self._lock:
            if self._keys and not refresh and time.monotonic() - self._keys[0] < CACHE_SECONDS:
                return self._keys[1]
        j = self._get_json(self.discovery()["jwks_uri"], "list of signing keys")
        keys = [k for k in j.get("keys") or [] if isinstance(k, dict)]
        with self._lock:
            self._keys = (time.monotonic(), keys)
        return keys

    # ---------------------------------------------------------------- contract
    def health(self) -> Health:
        if not self.issuer or not self.client_id:
            return Health.down("Fill in the issuer address and the client ID first.")
        try:
            d = self.discovery(refresh=True)
            n = len(self.keys(refresh=True))
        except SignInError as e:
            return Health.down(str(e))
        host = urllib.parse.urlsplit(d["authorization_endpoint"]).netloc
        if not n:
            return Health.down("The provider publishes no signing keys, so Kosha could not check its ID tokens.")
        note = "" if self.issuer.startswith("https://") else " The issuer is not HTTPS: use that only for tests."
        return Health.up(f"Connected to {self.issuer}: sign-in page on {host}, {n} signing key{'s' if n != 1 else ''}."
                         f"{note}")

    def start(self, redirect_uri: str, state: str) -> RedirectStart:
        d = self.discovery()
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)           # 64 characters, as RFC 7636 asks (43 to 128)
        challenge = b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
        params = {"response_type": "code", "client_id": self.client_id, "redirect_uri": redirect_uri,
                  "scope": self.scopes, "state": state, "nonce": nonce, "code_challenge": challenge,
                  "code_challenge_method": "S256"}
        ep = d["authorization_endpoint"]
        url = ep + ("&" if "?" in ep else "?") + urllib.parse.urlencode(params)
        return RedirectStart(url, {"nonce": nonce, "code_verifier": verifier})

    def finish(self, redirect_uri: str, params: Mapping[str, str], saved: Mapping[str, Any]) -> Identity:
        if params.get("error"):
            desc = " ".join(str(params.get("error_description") or "").split())[:200]
            if params["error"] == "access_denied":
                raise SignInError(f"The sign-in was cancelled or refused at {self.label()}." + (f" ({desc})" if desc else ""))
            raise SignInError(f"{self.label()} answered with the error {params['error']!r}" + (f": {desc}" if desc else "."))
        code = params.get("code")
        if not code:
            raise SignInError("The provider sent the browser back without a sign-in code.")
        if params.get("iss") and _norm(params["iss"]) != self.issuer:    # RFC 9207: the answer of another provider
            raise SignInError("The answer came from another provider than the one this sign-in started with.")
        nonce, verifier = saved.get("nonce"), saved.get("code_verifier")
        if not nonce or not verifier:
            raise SignInError("Kosha has no record of how this sign-in started. Please start again.")
        d = self.discovery()
        tokens = self._exchange(d, code, redirect_uri, verifier)
        claims = self.check_id_token(tokens.get("id_token"), nonce)
        if self.groups_claim not in claims and d.get("userinfo_endpoint") and tokens.get("access_token"):
            claims = {**self._userinfo(d["userinfo_endpoint"], tokens["access_token"], claims["sub"]), **claims}
        return self.identity(claims)

    # ----------------------------------------------------------------- steps
    def _exchange(self, d: dict, code: str, redirect_uri: str, verifier: str) -> dict:
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "code_verifier": verifier, "client_id": self.client_id}
        headers = {}
        if self.client_secret:
            methods = d.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
            if "client_secret_basic" in methods:
                pair = f"{urllib.parse.quote(self.client_id, safe='')}:{urllib.parse.quote(self.client_secret, safe='')}"
                headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
            else:
                form["client_secret"] = self.client_secret
        client, path = self._client(d["token_endpoint"], retries=0)      # a code works once: never send it twice
        try:
            r = client.post(path, form=form, headers=headers)
            tokens = r.json()
        except HttpError as e:
            err = ""
            try:
                err = json.loads(e.detail or "{}").get("error", "")
            except (ValueError, AttributeError):
                pass
            if err == "invalid_client" or e.status == 401:
                raise SignInError("The provider refused Kosha's client ID or client secret. Check them under "
                                  "Admin › Add-ins.") from None
            if err == "invalid_grant":
                raise SignInError("The provider refused the sign-in code (it was used already, has expired, or "
                                  "belongs to another sign-in). Please sign in again.") from None
            raise SignInError(f"The provider's token endpoint refused the sign-in ({e}{': ' + err if err else ''}).") from None
        except ValueError:
            raise SignInError("The provider's token endpoint did not answer with JSON.") from None
        if not isinstance(tokens, dict) or not tokens.get("id_token"):
            raise SignInError("The provider sent no ID token. Make sure the scope includes 'openid'.")
        return tokens

    def check_id_token(self, token: Any, nonce: str) -> dict:
        """Signature, issuer, audience, expiry, issue time and nonce of an ID token; returns its claims."""
        if not isinstance(token, str):
            raise SignInError("The provider sent no ID token. Make sure the scope includes 'openid'.")
        d = self.discovery()
        verify_signature(token, self.keys(), lambda: self.keys(refresh=True))
        try:
            claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": True, "verify_iat": True,
                                                "verify_nbf": True, "verify_aud": True, "verify_iss": True,
                                                "require": ["iss", "sub", "aud", "exp", "iat"]},
                                audience=self.client_id, issuer=d["issuer"], leeway=LEEWAY_SECONDS)
        except jwt.ExpiredSignatureError:
            raise SignInError("The ID token has expired. Check that the clocks of the Kosha server and the provider "
                              "are right, then sign in again.") from None
        except jwt.InvalidAudienceError:
            raise SignInError("The ID token was made for another application (audience), not for this client ID.") from None
        except jwt.InvalidIssuerError:
            raise SignInError("The ID token was issued by another provider than the one configured.") from None
        except jwt.ImmatureSignatureError:
            raise SignInError("The ID token is dated in the future. Check the clocks of the Kosha server and the "
                              "provider.") from None
        except jwt.PyJWTError as e:
            raise SignInError(f"The ID token is not acceptable: {e}.") from None
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
            raise SignInError("The ID token belongs to another sign-in (the nonce does not match). Please sign in "
                              "again.")
        aud = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        if ("azp" in claims or len(aud) > 1) and claims.get("azp") != self.client_id:
            raise SignInError("The ID token was handed out to another application (azp), not to this client ID.")
        return claims

    def _userinfo(self, url: str, access_token: str, sub: str) -> dict:
        info = self._get_json(url, "user-info endpoint", headers={"Authorization": f"Bearer {access_token}"})
        if info.get("sub") != sub:        # OpenID Connect Core 5.3.2: must be the same person
            raise SignInError("The provider's user information is about another person than the ID token.")
        return info

    def identity(self, claims: dict) -> Identity:
        username = claims.get(self.username_claim) or claims.get("preferred_username") or claims.get("email") \
            or claims["sub"]
        groups = claims.get(self.groups_claim) or ()
        if isinstance(groups, str):
            groups = [groups]
        if isinstance(groups, dict):       # some providers send {"group": "role"}
            groups = list(groups)
        groups = [str(g).lstrip("/") if str(g).startswith("/") else str(g) for g in groups if str(g).strip()]
        name = claims.get("name") or " ".join(p for p in (claims.get("given_name"), claims.get("family_name")) if p)
        email = claims.get("email") if claims.get("email_verified", True) is not False else ""
        return Identity(external_id=f"{claims['iss']}|{claims['sub']}", username=str(username), full_name=str(name or ""),
                        email=str(email or ""), groups=tuple(groups))
