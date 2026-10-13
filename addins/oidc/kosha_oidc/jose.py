# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Checking the signature of an ID token against the provider's published keys (JWKS), with PyJWT.

PyJWT checks RSA and elliptic-curve signatures with the `cryptography` package (Apache-2.0 / BSD). Where that
package is not installed, RS256 (the default of nearly every provider) is checked by the small, verify-only
PKCS #1 v1.5 implementation below, so sign-in still works; other algorithms then ask for `cryptography`.
Symmetric (HS*) and unsigned ("none") tokens are never accepted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Callable

import jwt
from jwt.algorithms import Algorithm

from kosha_sdk.signin import SignInError

try:  # cryptography is optional; PyJWT uses it for RSA, RSA-PSS, EC and EdDSA keys
    from jwt.algorithms import has_crypto
except ImportError:  # pragma: no cover - very old PyJWT
    has_crypto = False

ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA")
MIN_RSA_BITS = 2048
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def b64url_decode(text: str) -> bytes:
    text = str(text or "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class PlainRS256(Algorithm):
    """RS256 verification without the cryptography package (RFC 8017, RSASSA-PKCS1-v1_5).

    It rebuilds the whole expected encoding of the hash and compares it with the decrypted signature, so there
    is nothing to parse and nothing an altered signature could slip through."""

    def prepare_key(self, key: Any) -> tuple[int, int]:
        if isinstance(key, tuple) and len(key) == 2:
            n, e = key
        elif isinstance(key, dict) and key.get("kty") == "RSA":
            n, e = int.from_bytes(b64url_decode(key["n"]), "big"), int.from_bytes(b64url_decode(key["e"]), "big")
        else:
            raise jwt.InvalidKeyError("not an RSA public key")
        if n.bit_length() < MIN_RSA_BITS or e < 3 or e % 2 == 0:
            raise jwt.InvalidKeyError(f"RSA keys need at least {MIN_RSA_BITS} bits")
        return n, e

    def sign(self, msg: bytes, key: Any) -> bytes:  # pragma: no cover - Kosha only checks signatures
        raise NotImplementedError("Kosha only checks signatures")

    def verify(self, msg: bytes, key: tuple[int, int], sig: bytes) -> bool:
        n, e = key
        k = (n.bit_length() + 7) // 8
        if len(sig) != k:
            return False
        s = int.from_bytes(sig, "big")
        if s >= n:
            return False
        decrypted = pow(s, e, n).to_bytes(k, "big")
        t = _SHA256_DIGEST_INFO + hashlib.sha256(msg).digest()
        expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
        return hmac.compare_digest(decrypted, expected)

    @staticmethod
    def to_jwk(key_obj: Any, as_dict: bool = False):  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def from_jwk(jwk: Any):  # pragma: no cover
        raise NotImplementedError


def _pick(keys: list[dict], kid: str | None, alg: str) -> dict | None:
    kty = {"R": "RSA", "P": "RSA", "E": "EC"}.get(alg[0], "OKP")
    usable = [k for k in keys if isinstance(k, dict) and k.get("kty") == kty and k.get("use", "sig") == "sig"
              and k.get("alg", alg) == alg]
    if kid is not None:
        return next((k for k in usable if k.get("kid") == kid), None)
    return usable[0] if len(usable) == 1 else None


def verify_signature(token: str, keys: list[dict], refresh: Callable[[], list[dict]], use_crypto: bool | None = None,
                     who: str = "The provider") -> dict:
    """The token's claims, once its signature checks out against one of `keys`. Raises SignInError otherwise.

    `refresh()` fetches the keys again, for a provider that has just rolled over to a new key."""
    use_crypto = has_crypto if use_crypto is None else use_crypto
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise SignInError(f"{who} sent an ID token Kosha cannot read.") from None
    alg = header.get("alg")
    if alg not in ALGORITHMS:
        raise SignInError(f"{who} signed the ID token with {alg!r}. Kosha accepts only public-key signatures "
                          f"({', '.join(ALGORITHMS[:3])} ...); choose one of them for this client at the provider.")
    if not use_crypto and alg != "RS256":
        raise SignInError(f"{who} signs with {alg}. Install the Python package 'cryptography' on the Kosha server "
                          "to check it, or choose RS256 for this client at the provider.")
    key = _pick(keys, header.get("kid"), alg)
    if key is None:
        key = _pick(refresh(), header.get("kid"), alg)
    if key is None:
        raise SignInError(f"{who} signed the ID token with a key it does not publish (key id "
                          f"{header.get('kid')!r}). Nothing was changed; try again in a minute.")
    try:
        if use_crypto:
            jws = jwt.PyJWS(algorithms=[alg])
            prepared = jwt.PyJWK(key, algorithm=alg).key
        else:
            jws = jwt.PyJWS(algorithms=[])              # nothing built in: only the checker registered here
            jws.register_algorithm(alg, PlainRS256())
            prepared = key
        payload = jws.decode(token, key=prepared, algorithms=[alg])
    except (jwt.InvalidSignatureError, jwt.InvalidKeyError, jwt.DecodeError, jwt.InvalidAlgorithmError,
            jwt.PyJWKError, ValueError) as e:
        raise SignInError(f"The ID token's signature does not check out ({e.__class__.__name__}): it was not issued "
                          "by the provider, or it was changed on the way. Nothing was changed in Kosha.") from None
    try:
        claims = json.loads(payload)
    except ValueError:
        raise SignInError(f"{who} sent an ID token Kosha cannot read.") from None
    if not isinstance(claims, dict):
        raise SignInError(f"{who} sent an ID token Kosha cannot read.")
    return claims
