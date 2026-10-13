# SPDX-License-Identifier: AGPL-3.0-or-later
"""LDAP / Active Directory sign-in end to end: the official add-in against a small LDAP server in the test process.

First sign-in creates the user with the role of their groups; a local account of the same name is never taken
over until an admin links it; the local admin keeps working when the directory is down; failed directory
sign-ins count towards the same pause as local ones.
"""
import sys
from pathlib import Path

import pytest

from harness import Client

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "addins" / "ldap" / "tests", REPO / "addins" / "ldap"):
    if str(p) not in sys.path:
        sys.path.append(str(p))

import fake_ldap as FL  # noqa: E402

LDAP = "org.kosha.ldap"
NAME = "LDAP / Active Directory sign-in"


@pytest.fixture(scope="module")
def directory_server():
    s = FL.FakeLdap(FL.ad_entries(), FL.passwords()).start()
    yield s
    s.stop()


def _login(kosha, user, password, ip, expect=200):
    return Client(kosha.base).call("POST", "/api/auth/login", {"username": user, "password": password},
                                   headers={"X-Forwarded-For": ip}, expect=expect)


def _configure(admin, server, **extra):
    admin.call("PUT", f"/api/addins/{LDAP}/settings", {
        "server_url": server.url, "directory_type": "active-directory", "base_dn": FL.AD_BASE, "bind_mode": "search",
        "bind_dn": f"CN=svc-kosha,OU=Service,{FL.AD_BASE}", "bind_password": FL.SERVICE_PASSWORD,
        "starttls": False, "timeout_s": 3, "label": "Demo Engineering network account", **extra})


def test_providers_wait_until_they_are_set_up(kosha):
    assert Client(kosha.base).call("GET", "/api/auth/providers") == {"redirect": [], "directory_passwords": []}
    d = kosha.client().call("GET", "/api/admin/directory")
    by_id = {p["id"]: p for p in d["providers"]}
    assert not by_id[LDAP]["ready"] and "Not set up yet: fill in Directory address, Search base" in by_id[LDAP]["why"]
    assert not by_id["org.kosha.oidc"]["ready"]
    assert d["settings"]["group_roles"] == [] and d["local_admin"] == "admin"


def test_directory_sign_in(kosha, directory_server):
    admin = kosha.client()
    local_daniel = admin.call("POST", "/api/users", {"username": "daniel", "full_name": "Daniel (local)",
                                                     "role": "viewer", "password": "daniel-local-1"})["user"]
    _configure(admin, directory_server)
    assert admin.call("POST", f"/api/addins/{LDAP}/health")["ok"]
    assert Client(kosha.base).call("GET", "/api/auth/providers")["directory_passwords"] == ["Demo Engineering network account"]
    admin.call("PUT", "/api/admin/directory", {"group_roles": [{"group": "Kosha Engineers", "role": "engineer"},
                                                               {"group": "Kosha Approvers", "role": "approver"}]})
    admin.call("PUT", "/api/admin/directory", {"group_roles": [{"group": "x", "role": "boss"}]}, expect=400)

    # the admin checks an account first: nothing is created
    t = admin.call("POST", "/api/admin/directory/test", {"provider": LDAP, "username": "asha", "password": FL.PASSWORD})
    assert t["ok"] and t["role"] == "engineer" and t["identity"]["groups"] == ["Kosha Engineers"]
    assert "new Kosha user asha" in t["account"]
    assert not admin.call("POST", "/api/admin/directory/test", {"provider": LDAP, "username": "asha", "password": "x"})["ok"]

    # first sign-in creates the user with the role of their groups
    r = _login(kosha, "asha", FL.PASSWORD, "10.2.0.1")
    assert r["user"]["username"] == "asha" and r["user"]["full_name"] == "Asha Rao" and r["user"]["role"] == "engineer"
    asha = Client(kosha.base, r["token"])
    assert asha.call("GET", "/api/auth/me")["user"]["email"] == "asha@demo.example.com"
    pw = asha.call("POST", "/api/auth/password", {"current": FL.PASSWORD, "new": "another-pass-1"}, expect=400)
    assert pw["code"] == "directory_password" and "kept there" in pw["error"]
    _login(kosha, "asha", "wrong-password", "10.2.0.1", expect=400)
    entries = admin.call("GET", "/api/audit?entity=user&limit=200")["entries"]
    assert any(e["action"] == "login" and f"through {NAME}" in e["details"] for e in entries)
    assert any(e["action"] == "user_create" and "first sign-in through" in e["details"] for e in entries)

    # a local account with the same name is not taken over ...
    refused = _login(kosha, "daniel", FL.PASSWORD, "10.2.0.2", expect=403)
    assert refused["code"] == "link_needed" and "already exists" in refused["error"]
    assert _login(kosha, "daniel", "daniel-local-1", "10.2.0.2")["user"]["role"] == "viewer"   # still local
    # ... until the admin links it; then the directory decides, and the Kosha password is no longer used
    links = admin.call("POST", f"/api/users/{local_daniel['id']}/directory", {"provider": LDAP, "directory_username": "daniel"})
    assert links["links"][0]["pending"]
    r = _login(kosha, "daniel", FL.PASSWORD, "10.2.0.2")
    assert r["user"]["id"] == local_daniel["id"] and r["user"]["role"] == "approver"     # nested group
    assert r["user"]["full_name"] == "Daniel Okafor"
    _login(kosha, "daniel", "daniel-local-1", "10.2.0.2", expect=400)
    d = admin.call("GET", "/api/admin/directory")
    assert {(x["username"], x["pending"]) for x in d["links"]} == {("asha", False), ("daniel", False)}
    link_id = next(x["id"] for x in d["links"] if x["username"] == "daniel")
    daniel = Client(kosha.base, r["token"])
    out = admin.call("DELETE", f"/api/users/{local_daniel['id']}/directory/{link_id}")
    assert out["sessions_ended"] == 2 and out["links"] == []          # his local and his directory session
    daniel.call("GET", "/api/auth/me", expect=401)
    assert _login(kosha, "daniel", "daniel-local-1", "10.2.0.2")["user"]["id"] == local_daniel["id"]

    # the built-in admin is never linked
    admin_id = admin.call("GET", "/api/auth/me")["user"]["id"]
    assert "stays a local account" in admin.call("POST", f"/api/users/{admin_id}/directory",
                                                 {"provider": LDAP, "directory_username": "admin"}, expect=400)["error"]

    # groups decide access: without a listed group, no sign-in (unless there is a role for everyone else)
    admin.call("PUT", "/api/admin/directory", {"group_roles": [{"group": "Kosha Approvers", "role": "approver"}]})
    no = _login(kosha, "asha", FL.PASSWORD, "10.2.0.3", expect=403)
    assert no["code"] == "no_kosha_group" and "Admin › Sign-in & sessions" in no["error"]
    admin.call("PUT", "/api/admin/directory", {"default_role": "viewer"})
    assert _login(kosha, "asha", FL.PASSWORD, "10.2.0.3")["user"]["role"] == "viewer"
    _login(kosha, "tom", FL.PASSWORD, "10.2.0.3", expect=400)                          # disabled in the directory
    assert any(e["action"] == "login_refused" for e in admin.call("GET", "/api/audit?entity=user&limit=200")["entries"])

    # the directory is down: the local admin still signs in, directory users are told what happened
    directory_server.mode.add("hang")
    try:
        assert _login(kosha, "admin", "admin-test-pass-1", "10.2.0.4")["user"]["role"] == "admin"
        down = _login(kosha, "asha", FL.PASSWORD, "10.2.0.4", expect=503)
        assert down["code"] == "directory_unavailable" and "Your work is safe" in down["error"]
        assert kosha.client().call("GET", f"/api/addins/{LDAP}")["status"] == "enabled"   # not switched off for it
    finally:
        directory_server.mode.discard("hang")

    # failed directory sign-ins count towards the same pause as local ones
    for _ in range(4):
        _login(kosha, "asha", "wrong-password", "10.2.0.5", expect=400)
    paused = _login(kosha, "asha", "wrong-password", "10.2.0.5", expect=429)
    assert paused["code"] == "sign_in_paused"
    _login(kosha, "asha", FL.PASSWORD, "10.2.0.6", expect=429)


def test_directory_settings_need_an_admin(kosha):
    viewer = kosha.add_user("pia", "viewer", password="pia-password-1")
    viewer.call("GET", "/api/admin/directory", expect=403)
    viewer.call("PUT", "/api/admin/directory", {"default_role": "admin"}, expect=403)
    viewer.call("POST", "/api/users/1/directory", {"provider": LDAP, "directory_username": "x"}, expect=403)
