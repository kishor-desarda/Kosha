# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""OpenID Connect sign-in against a small test provider, through the SDK conformance kit."""
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(HERE.parent), str(ROOT / "sdk" / "python")]

uvicorn = pytest.importorskip("uvicorn")
jwt = pytest.importorskip("jwt")

import fake_oidc as FO  # noqa: E402
from kosha_oidc import jose  # noqa: E402
from kosha_oidc.provider import OidcProvider  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.signin import Identity, SignInError  # noqa: E402
from kosha_sdk.testing import check_manifest, check_redirect_provider  # noqa: E402

REDIRECT = "https://plm.example.com/api/auth/sso/org.kosha.oidc/callback"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(FO.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    FO.reset(f"http://127.0.0.1:{port}")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(autouse=True)
def fresh(base):
    FO.reset()


def provider(**overrides) -> OidcProvider:
    settings = {"issuer": FO.issuer(), "client_id": FO.CLIENT_ID, "client_secret": FO.CLIENT_SECRET,
                "label": "Demo Engineering account", "timeout_s": 5, **overrides}
    return OidcProvider(AddinContext("org.kosha.oidc", settings=settings))


def sign_in(p: OidcProvider, state: str = "s-1") -> Identity:
    start = p.start(REDIRECT, state)
    return p.finish(REDIRECT, FO.browser(start.url), start.saved)


def test_manifest_is_valid():
    assert check_manifest(HERE.parent) == []


def test_conformance():
    assert check_redirect_provider(provider(), FO.browser, REDIRECT) == []


def test_sign_in_gives_a_stable_identity_with_groups():
    p = provider()
    ident = sign_in(p)
    assert ident == Identity(external_id=f"{FO.issuer()}|8f14e45f-0001", username="asha", full_name="Asha Rao",
                             email="asha@example.com", groups=("kosha-engineers",))
    FO.STATE["user"] = "daniel"
    assert sign_in(p).groups == ("kosha-approvers", "kosha-engineers")
    assert p.describe() == {"style": "redirect", "label": "Demo Engineering account"}


def test_start_uses_pkce_and_a_fresh_nonce():
    p = provider()
    a, b = p.start(REDIRECT, "s-a"), p.start(REDIRECT, "s-b")
    assert a.saved["nonce"] != b.saved["nonce"] and a.saved["code_verifier"] != b.saved["code_verifier"]
    assert 43 <= len(a.saved["code_verifier"]) <= 128
    q = dict(__import__("urllib.parse").parse.parse_qsl(a.url.split("?", 1)[1]))
    assert q["code_challenge_method"] == "S256" and q["state"] == "s-a" and q["redirect_uri"] == REDIRECT
    assert q["code_challenge"] == jose.b64url_encode(__import__("hashlib").sha256(a.saved["code_verifier"].encode()).digest())
    assert "nonce" in q and q["nonce"] == a.saved["nonce"] and "code_verifier" not in a.url


def test_client_secret_basic_or_post_and_public_clients():
    sign_in(provider())
    assert FO.STATE["token_calls"][-1]["basic"] and "client_secret" not in FO.STATE["token_calls"][-1]["form"]
    FO.STATE["mode"].add("secret_post")
    provider().discovery()      # a new instance reads the discovery document again
    sign_in(provider())
    assert FO.STATE["token_calls"][-1]["form"]["client_secret"] == FO.CLIENT_SECRET
    with pytest.raises(SignInError, match="client ID or client secret"):
        sign_in(provider(client_secret="wrong"))


@pytest.mark.parametrize("mode, message", [
    ("wrong_aud", "another application"),
    ("wrong_iss", "another provider"),
    ("expired", "expired"),
    ("wrong_nonce", "nonce"),
    ("bad_sig", "signature does not check out"),
    ("alg_none", "public-key signatures"),
    ("hs256", "public-key signatures"),
    ("userinfo_other_sub", "another person"),
])
def test_tampered_answers_are_refused(mode, message):
    FO.STATE["mode"].add(mode)
    if mode == "userinfo_other_sub":
        FO.STATE["mode"].add("groups_only_in_userinfo")
    with pytest.raises(SignInError, match=message):
        sign_in(provider())


def test_groups_from_userinfo_when_the_token_leaves_them_out():
    FO.STATE["mode"].add("groups_only_in_userinfo")
    FO.STATE["user"] = "daniel"
    assert sign_in(provider()).groups == ("kosha-approvers", "kosha-engineers")


def test_a_code_works_once_and_only_with_its_verifier():
    p = provider()
    start = p.start(REDIRECT, "s-1")
    answer = FO.browser(start.url)
    p.finish(REDIRECT, answer, start.saved)
    with pytest.raises(SignInError, match="used already"):
        p.finish(REDIRECT, answer, start.saved)
    other = p.start(REDIRECT, "s-2")
    with pytest.raises(SignInError, match="refused the sign-in code"):
        p.finish(REDIRECT, FO.browser(other.url), {**other.saved, "code_verifier": start.saved["code_verifier"]})
    assert len(FO.STATE["token_calls"]) == 3          # never repeated automatically


def test_the_answer_of_another_provider_and_cancelled_sign_ins():
    p = provider()
    start = p.start(REDIRECT, "s-1")
    answer = FO.browser(start.url)
    with pytest.raises(SignInError, match="another provider"):
        p.finish(REDIRECT, {**answer, "iss": "https://evil.example.com"}, start.saved)
    FO.STATE["mode"].add("deny")
    with pytest.raises(SignInError, match="cancelled or refused"):
        sign_in(p)


def test_a_new_signing_key_is_fetched_once_the_provider_rolls_over():
    p = provider()
    sign_in(p)
    FO.STATE["mode"].add("rolled_key")
    assert sign_in(p).username == "asha"


def test_health_and_discovery_problems():
    assert provider().health().ok
    assert "Connected to" in provider().health().message
    assert not provider(issuer="", client_id="").health().ok
    FO.STATE["mode"].add("wrong_discovery_issuer")
    h = provider().health()
    assert not h.ok and "Copy the issuer address exactly" in h.message
    h = provider(issuer="http://127.0.0.1:9/realms/none").health()
    assert not h.ok and "could not read the discovery document" in h.message


def test_plain_rs256_matches_pyjwt_and_refuses_changes():
    key = FO.STATE["key"]
    token = FO.make_jwt({"sub": "x", "n": 1}, key)
    keys = [FO.jwk(key)]
    assert jose.verify_signature(token, keys, lambda: keys, use_crypto=False) == {"sub": "x", "n": 1}
    head, body, sig = token.split(".")
    forged = FO.b64u(b'{"sub":"admin","n":1}')
    for bad in (f"{head}.{forged}.{sig}", f"{head}.{body}.{sig[:-4]}AAAA", f"{head}.{body}."):
        with pytest.raises(SignInError):
            jose.verify_signature(bad, keys, lambda: keys, use_crypto=False)
    small = FO.rsa_key(1024)
    with pytest.raises(SignInError):          # keys shorter than 2048 bits are refused
        jose.verify_signature(FO.make_jwt({"sub": "x"}, small), [FO.jwk(small)], lambda: [], use_crypto=False)
    if jose.has_crypto:                       # the same token through PyJWT's own RSA (cryptography installed)
        assert jose.verify_signature(token, keys, lambda: keys, use_crypto=True)["sub"] == "x"
