# SPDX-License-Identifier: Apache-2.0
"""The sign-in provider contract, the conformance kit for it, and HTTP forms (used by OpenID Connect)."""
import json
import secrets
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kosha_sdk import AddinContext, AddinError, Health  # noqa: E402
from kosha_sdk import Identity as TopLevelIdentity  # noqa: E402
from kosha_sdk.cli import main  # noqa: E402
from kosha_sdk.permissions import PERMISSIONS  # noqa: E402
from kosha_sdk.signin import Identity, PasswordProvider, RedirectProvider, RedirectStart, SignInError  # noqa: E402
from kosha_sdk.testing import check_manifest, check_password_provider, check_redirect_provider  # noqa: E402

USERS = {"asha": ("Mixer-Line-2026", "guid-0001", ("Kosha Engineers",))}


def ctx(**settings):
    return AddinContext("com.example.directory", settings=settings)


class MemoryDirectory(PasswordProvider):
    """A directory in a dict. `sloppy` makes the mistakes the conformance kit must catch."""

    def __init__(self, ctx, sloppy=False):
        super().__init__(ctx)
        self.sloppy = sloppy

    def health(self):
        return Health.up("memory")

    def authenticate(self, username, password):
        if self.sloppy and (not password or username == "*"):     # anonymous bind / wildcard "work"
            return Identity("guid-0001", "asha")
        entry = USERS.get(username)
        if not entry or not password or password != entry[0]:
            return None
        return Identity(entry[1] if not self.sloppy else username, username, "Asha Rao", "asha@example.com", entry[2])


class MemorySso(RedirectProvider):
    """A redirect provider whose 'site' is a dict of issued codes."""

    def __init__(self, ctx, check_nonce=True):
        super().__init__(ctx)
        self.codes: dict[str, str] = {}
        self.check_nonce = check_nonce

    def health(self):
        return Health.up("memory")

    def start(self, redirect_uri, state):
        nonce = secrets.token_hex(8)
        return RedirectStart(f"https://sso.example.com/auth?{urllib.parse.urlencode({'state': state, 'nonce': nonce})}",
                             {"nonce": nonce})

    def finish(self, redirect_uri, params, saved):
        if params.get("error"):
            raise SignInError("The sign-in was cancelled.")
        nonce = self.codes.pop(params.get("code", ""), None)
        if nonce is None:
            raise SignInError("Unknown code.")
        if self.check_nonce and nonce != saved.get("nonce"):
            raise SignInError("Wrong nonce.")
        return Identity("https://sso.example.com|sub-1", "asha", groups=["kosha-engineers"])

    def browser(self, url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        code = secrets.token_hex(8)
        self.codes[code] = q["nonce"]
        return {"code": code, "state": q["state"]}


def test_identity_needs_ids_and_cleans_groups():
    i = Identity("guid", "asha", groups=["A", "", "  ", 7])
    assert i.groups == ("A", "7") and TopLevelIdentity is Identity
    for bad in (("", "asha"), ("guid", " "), (None, "asha")):
        with pytest.raises(ValueError):
            Identity(*bad)
    assert issubclass(SignInError, AddinError)          # never counted as a crash by the server's guard
    assert "users:sign-in" in PERMISSIONS


def test_providers_describe_themselves():
    assert MemoryDirectory(ctx()).describe() == {"style": "password", "label": "single sign-on"}
    assert MemorySso(ctx(label="Demo Engineering account")).describe() == {"style": "redirect",
                                                                           "label": "Demo Engineering account"}
    with pytest.raises(TypeError):                      # the abstract methods must be written
        PasswordProvider(ctx())


def test_password_provider_conformance():
    assert check_password_provider(MemoryDirectory(ctx()), "asha", "Mixer-Line-2026") == []
    assert "returned None for the test account" in check_password_provider(MemoryDirectory(ctx()), "asha", "x")[0]
    problems = check_password_provider(MemoryDirectory(ctx(), sloppy=True), "asha", "Mixer-Line-2026")
    assert any("external_id is the user name" in p for p in problems)
    assert any("an empty password" in p for p in problems) and any("the user name '*'" in p for p in problems)


def test_redirect_provider_conformance():
    good = MemorySso(ctx(label="Demo"))
    assert check_redirect_provider(good, good.browser) == []
    lax = MemorySso(ctx(label="Demo"), check_nonce=False)
    assert any("belongs to another sign-in" in p for p in check_redirect_provider(lax, lax.browser))


def test_sign_in_template_creates_a_valid_addin(tmp_path):
    assert main(["new", "sign-in", "my-directory", "--publisher", "com.example", "--dest", str(tmp_path)]) == 0
    folder = tmp_path / "my-directory"
    assert check_manifest(folder) == []
    sys.path.insert(0, str(folder))
    try:
        from my_directory.provider import Provider
        p = Provider(ctx(base_url="https://directory.example.com"))
        assert p.authenticate("asha", "") is None and p.describe()["style"] == "password"
    finally:
        sys.path.remove(str(folder))


def test_manifest_check_asks_sign_in_addins_for_their_permission(tmp_path):
    (tmp_path / "kosha-addin.yaml").write_text(
        "id: com.example.sso\nname: SSO\nversion: 1.0.0\npublisher: Example\nlicence: Apache-2.0\n"
        "kosha: \">=0.5\"\nkind: [sign-in]\nentry_points: {server: \"sso.main:Provider\"}\n")
    assert check_manifest(tmp_path) == ["a sign-in add-in needs the permission users:sign-in"]


def test_http_forms_and_per_call_headers():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.update(ctype=self.headers["Content-Type"], auth=self.headers.get("Authorization"),
                        body=dict(urllib.parse.parse_qsl(body.decode())), path=self.path)
            out = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        http = ctx().http(f"http://127.0.0.1:{server.server_port}", retries=0)
        verifier = "v" * 50
        r = http.post("/token?x=1", form={"grant_type": "authorization_code", "code_verifier": verifier},
                      headers={"Authorization": "Basic abc"}, params={"y": "2"})
        assert r.json() == {"ok": True}
        assert seen["ctype"] == "application/x-www-form-urlencoded" and seen["auth"] == "Basic abc"
        assert seen["body"] == {"grant_type": "authorization_code", "code_verifier": verifier}
        assert seen["path"] == "/token?x=1&y=2"
    finally:
        server.shutdown()
