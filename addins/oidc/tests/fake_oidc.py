# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""A small OpenID Connect provider for tests: discovery, keys, sign-in page, token and user-info endpoints.

It signs ID tokens with a fresh RSA key made here (pure Python, so the tests need no extra packages) and can be
told to misbehave (``STATE["mode"]``) the ways a real provider, or an attacker in between, might.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

CLIENT_ID = "kosha"
CLIENT_SECRET = "client-secret-for-tests"
REALM = "/realms/demo"
USERS = {
    "asha": {"sub": "8f14e45f-0001", "preferred_username": "asha", "name": "Asha Rao", "email": "asha@example.com",
             "groups": ["/kosha-engineers"]},
    "daniel": {"sub": "8f14e45f-0002", "preferred_username": "daniel", "name": "Daniel Okafor",
               "email": "daniel@example.com", "groups": ["/kosha-approvers", "/kosha-engineers"]},
    "tom": {"sub": "8f14e45f-0004", "preferred_username": "tom", "name": "Tom Berg", "email": "tom@example.com",
            "groups": []},
}
STATE: dict = {}


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_b64u(n: int) -> str:
    return b64u(n.to_bytes((n.bit_length() + 7) // 8, "big"))


# ------------------------------------------------------------------ RSA for tests (never for real keys)

_SMALL = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p ** 0.5) + 1, 2))]


def _probably_prime(n: int, rounds: int = 24) -> bool:
    if any(n % p == 0 for p in _SMALL):
        return n in _SMALL
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int) -> int:
    while True:
        n = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _probably_prime(n):
            return n


def rsa_key(bits: int = 2048) -> dict:
    e = 65537
    while True:
        p, q = _prime(bits // 2), _prime(bits // 2)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e and (p * q).bit_length() == bits:
            return {"n": p * q, "e": e, "d": pow(e, -1, phi)}


def rs256_sign(key: dict, data: bytes) -> bytes:
    k = (key["n"].bit_length() + 7) // 8
    t = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(data).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), key["d"], key["n"]).to_bytes(k, "big")


def make_jwt(claims: dict, key: dict, kid: str = "k1", alg: str = "RS256") -> str:
    head = b64u(json.dumps({"alg": alg, "typ": "JWT", "kid": kid}).encode())
    body = b64u(json.dumps(claims).encode())
    signing_input = f"{head}.{body}".encode()
    if alg == "none":
        return f"{head}.{body}."
    if alg == "HS256":
        import hmac
        sig = hmac.new(CLIENT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    else:
        sig = rs256_sign(key, signing_input)
    return f"{head}.{body}.{b64u(sig)}"


def jwk(key: dict, kid: str = "k1") -> dict:
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": _int_b64u(key["n"]), "e": _int_b64u(key["e"])}


# ------------------------------------------------------------------ the provider

def reset(base: str | None = None) -> None:
    if "key" not in STATE:
        STATE["key"] = rsa_key()
        STATE["other_key"] = rsa_key()
    if base is not None:
        STATE["base"] = base
    STATE.update({"mode": set(), "user": "asha", "codes": {}, "tokens": {}, "token_calls": [], "auth_calls": []})


def issuer() -> str:
    return STATE["base"] + REALM


async def discovery(request: Request):
    iss = issuer()
    return JSONResponse({
        "issuer": iss + "/other" if "wrong_discovery_issuer" in STATE["mode"] else iss,
        "authorization_endpoint": f"{iss}/protocol/openid-connect/auth",
        "token_endpoint": f"{iss}/protocol/openid-connect/token",
        "userinfo_endpoint": f"{iss}/protocol/openid-connect/userinfo",
        "jwks_uri": f"{iss}/protocol/openid-connect/certs",
        "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": (["client_secret_post"] if "secret_post" in STATE["mode"]
                                                  else ["client_secret_basic", "client_secret_post"]),
    })


async def certs(request: Request):
    keys = [jwk(STATE["key"], "rolled" if "rolled_key" in STATE["mode"] else "k1")]
    return JSONResponse({"keys": keys})


async def auth(request: Request):
    q = dict(request.query_params)
    STATE["auth_calls"].append(q)
    if q.get("client_id") != CLIENT_ID or q.get("response_type") != "code" or q.get("code_challenge_method") != "S256" \
            or not q.get("code_challenge") or "openid" not in q.get("scope", "").split():
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    back = q["redirect_uri"]
    if "deny" in STATE["mode"]:
        args = {"error": "access_denied", "error_description": "The user said no", "state": q.get("state", "")}
    else:
        code = secrets.token_urlsafe(16)
        STATE["codes"][code] = {"redirect_uri": back, "nonce": q.get("nonce"), "challenge": q["code_challenge"],
                                "user": STATE["user"], "used": False}
        args = {"code": code, "state": q.get("state", ""), "iss": issuer()}
    return RedirectResponse(back + ("&" if "?" in back else "?") + urllib.parse.urlencode(args), status_code=302)


def _client_ok(request: Request, form) -> bool:
    h = request.headers.get("authorization", "")
    if h.startswith("Basic "):
        cid, _, secret = base64.b64decode(h[6:]).decode().partition(":")
        return urllib.parse.unquote(cid) == CLIENT_ID and urllib.parse.unquote(secret) == CLIENT_SECRET
    return form.get("client_id") == CLIENT_ID and form.get("client_secret") == CLIENT_SECRET


async def token(request: Request):
    form = dict(await request.form())
    STATE["token_calls"].append({"form": form, "basic": request.headers.get("authorization", "").startswith("Basic ")})
    if not _client_ok(request, form):
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    c = STATE["codes"].get(form.get("code", ""))
    if form.get("grant_type") != "authorization_code" or not c or c["used"] or c["redirect_uri"] != form.get("redirect_uri"):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    verifier = form.get("code_verifier", "")
    if b64u(hashlib.sha256(verifier.encode()).digest()) != c["challenge"]:
        return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)
    c["used"] = True
    mode, now = STATE["mode"], int(time.time())
    user = dict(USERS[c["user"]])
    claims = {"iss": issuer(), "sub": user["sub"], "aud": CLIENT_ID, "exp": now + 300, "iat": now, "nonce": c["nonce"],
              "azp": CLIENT_ID, "preferred_username": user["preferred_username"], "name": user["name"],
              "email": user["email"], "email_verified": True}
    if "groups_only_in_userinfo" not in mode:
        claims["groups"] = user["groups"]
    if "wrong_aud" in mode:
        claims["aud"] = "another-app"
    if "wrong_iss" in mode:
        claims["iss"] = "https://evil.example.com"
    if "expired" in mode:
        claims["exp"], claims["iat"] = now - 600, now - 900
    if "wrong_nonce" in mode:
        claims["nonce"] = "not-the-nonce"
    key, kid, alg = STATE["key"], "k1", "RS256"
    if "bad_sig" in mode:
        key = STATE["other_key"]
    if "rolled_key" in mode:
        kid = "rolled"
    if "alg_none" in mode:
        alg = "none"
    if "hs256" in mode:
        alg = "HS256"
    access = secrets.token_urlsafe(16)
    STATE["tokens"][access] = user
    return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": 300,
                         "id_token": make_jwt(claims, key, kid, alg)})


async def userinfo(request: Request):
    h = request.headers.get("authorization", "")
    user = STATE["tokens"].get(h[7:]) if h.startswith("Bearer ") else None
    if not user:
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    sub = "someone-else" if "userinfo_other_sub" in STATE["mode"] else user["sub"]
    return JSONResponse({"sub": sub, "groups": user["groups"], "preferred_username": user["preferred_username"]})


app = Starlette(routes=[
    Route(f"{REALM}/.well-known/openid-configuration", discovery),
    Route(f"{REALM}/protocol/openid-connect/certs", certs),
    Route(f"{REALM}/protocol/openid-connect/auth", auth),
    Route(f"{REALM}/protocol/openid-connect/token", token, methods=["POST"]),
    Route(f"{REALM}/protocol/openid-connect/userinfo", userinfo),
])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def browser(url: str) -> dict:
    """Play the person's browser at the provider: open the sign-in page and return what it sends back."""
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
    try:
        opener.open(url, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307):
            return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(e.headers["Location"]).query))
        raise
    raise AssertionError("the provider did not send the browser back")
