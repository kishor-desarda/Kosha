# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenID Connect sign-in end to end: the official add-in, a small provider in the test process, and a browser
that keeps cookies and does not follow redirects by itself, so every step of the round trip is checked."""
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from erp_helpers import serve
from harness import Client

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "addins" / "oidc" / "tests", REPO / "addins" / "oidc"):
    if str(p) not in sys.path:
        sys.path.append(str(p))

import fake_oidc as FO  # noqa: E402

OIDC = "org.kosha.oidc"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Browser:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPCookieProcessor(self.jar),
                                                  urllib.request.ProxyHandler({}))

    def go(self, url: str) -> str:
        """Open an address; returns where the answer redirects to."""
        try:
            self.opener.open(url, timeout=30)
        except urllib.error.HTTPError as e:
            assert e.code in (302, 303), (e.code, e.read()[:300])
            return e.headers["Location"]
        raise AssertionError(f"{url} did not redirect")

    def post(self, url: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


@pytest.fixture(scope="module")
def provider_url():
    server, url = serve(FO.app)
    FO.reset(url)
    yield url
    server.should_exit = True


@pytest.fixture(autouse=True)
def fresh(provider_url):
    FO.reset()


def _sign_in(kosha, browser: Browser | None = None, back: Browser | None = None):
    """Start at Kosha, sign in at the provider, come back. Returns (browser, fragment Kosha sends it to)."""
    browser = browser or Browser()
    at_provider = browser.go(f"{kosha.base}/api/auth/sso/{OIDC}/start")
    assert at_provider.startswith(FO.issuer() + "/protocol/openid-connect/auth?")
    answer = FO.browser(at_provider)
    callback = f"{kosha.base}/api/auth/sso/{OIDC}/callback?{urllib.parse.urlencode(answer)}"
    return browser, (back or browser).go(callback)


def _finish(kosha, browser: Browser, location: str):
    assert location.startswith("/#/sso/"), location
    return browser.post(f"{kosha.base}/api/auth/sso/finish", {"ticket": location[len("/#/sso/"):]})


def test_redirect_sign_in(kosha):
    admin = kosha.client()
    assert Client(kosha.base).call("GET", "/api/auth/providers")["redirect"] == []
    admin.call("PUT", f"/api/addins/{OIDC}/settings", {"issuer": FO.issuer(), "client_id": FO.CLIENT_ID,
                                                       "client_secret": FO.CLIENT_SECRET,
                                                       "label": "Demo Engineering account", "timeout_s": 5})
    assert admin.call("POST", f"/api/addins/{OIDC}/health")["ok"]
    assert Client(kosha.base).call("GET", "/api/auth/providers")["redirect"] == [
        {"id": OIDC, "label": "Demo Engineering account"}]
    d = admin.call("GET", "/api/admin/directory")
    oidc = next(p for p in d["providers"] if p["id"] == OIDC)
    assert oidc["ready"] and oidc["style"] == "redirect"
    assert oidc["redirect_uri"] == f"{kosha.base}/api/auth/sso/{OIDC}/callback"
    admin.call("PUT", "/api/admin/directory", {"group_roles": [{"group": "kosha-engineers", "role": "engineer"},
                                                               {"group": "kosha-approvers", "role": "approver"}]})

    browser, location = _sign_in(kosha)
    assert FO.STATE["auth_calls"][-1]["redirect_uri"] == oidc["redirect_uri"]
    status, r = _finish(kosha, browser, location)
    assert status == 200 and r["user"]["username"] == "asha" and r["user"]["role"] == "engineer"
    assert r["user"]["full_name"] == "Asha Rao"
    me = Client(kosha.base, r["token"]).call("GET", "/api/auth/me")["user"]
    assert me["email"] == "asha@example.com"
    # the ticket works once, and the answer from the provider cannot be brought back a second time
    status, again = _finish(kosha, browser, location)
    assert status == 400 and again["code"] == "sso_expired"
    entries = admin.call("GET", "/api/audit?entity=user&limit=100")["entries"]
    assert any(e["action"] == "login" and "through OpenID Connect sign-in" in e["details"] for e in entries)

    # the same person again: same user, role follows the groups
    FO.STATE["user"] = "daniel"
    browser, location = _sign_in(kosha)
    status, r = _finish(kosha, browser, location)
    assert status == 200 and r["user"]["username"] == "daniel" and r["user"]["role"] == "approver"


def test_answers_that_do_not_belong_to_this_browser_are_refused(kosha):
    # an answer opened in another browser (someone sends you their sign-in link): refused, nobody is signed in
    _, location = _sign_in(kosha, back=Browser())
    assert location == "/#/sso-error/expired"
    # the ticket redeemed by another browser
    browser, location = _sign_in(kosha)
    status, r = _finish(kosha, Browser(), location)
    assert status == 400 and r["code"] == "sso_expired"
    # replaying the provider's answer
    browser = Browser()
    at_provider = browser.go(f"{kosha.base}/api/auth/sso/{OIDC}/start")
    callback = f"{kosha.base}/api/auth/sso/{OIDC}/callback?{urllib.parse.urlencode(FO.browser(at_provider))}"
    assert browser.go(callback).startswith("/#/sso/")
    assert browser.go(callback) == "/#/sso-error/expired"
    # a made-up state
    assert Browser().go(f"{kosha.base}/api/auth/sso/{OIDC}/callback?code=x&state=made-up") == "/#/sso-error/expired"


def test_problems_come_back_as_plain_messages(kosha):
    FO.STATE["user"] = "tom"                         # in no group that gives access
    browser, location = _sign_in(kosha)
    status, r = _finish(kosha, browser, location)
    assert status == 403 and "not in a group that gives access to Kosha" in r["error"]
    FO.STATE["user"] = "asha"
    FO.STATE["mode"].add("deny")
    browser, location = _sign_in(kosha)
    status, r = _finish(kosha, browser, location)
    assert status == 403 and "cancelled or refused" in r["error"] and "Your work is safe" in r["error"]
    FO.STATE["mode"] = {"bad_sig"}
    browser, location = _sign_in(kosha)
    status, r = _finish(kosha, browser, location)
    assert status == 403 and "signature does not check out" in r["error"]
    entries = kosha.client().call("GET", "/api/audit?entity=user&limit=100")["entries"]
    assert any(e["action"] == "login_refused" for e in entries) and any(
        e["action"] == "login_failed" and OIDC in e["details"] for e in entries)
    assert kosha.client().call("GET", f"/api/addins/{OIDC}")["status"] == "enabled"


def test_a_switched_off_provider_is_not_offered(kosha):
    admin = kosha.client()
    admin.call("POST", f"/api/addins/{OIDC}/disable")
    try:
        assert Client(kosha.base).call("GET", "/api/auth/providers")["redirect"] == []
        assert Browser().go(f"{kosha.base}/api/auth/sso/{OIDC}/start") == "/#/sso-error/unavailable"
        assert Browser().go(f"{kosha.base}/api/auth/sso/org.example.nothing/start") == "/#/sso-error/unavailable"
    finally:
        admin.call("POST", f"/api/addins/{OIDC}/enable")
