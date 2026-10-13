# SPDX-License-Identifier: AGPL-3.0-or-later
"""Directory sign-in without a server: group → role mapping, links by stable id, no silent take-over, round trips."""
import json

import pytest

from kosha import db, directory, security
from kosha.web import ApiError
from kosha_sdk.signin import Identity

LDAP = "org.kosha.ldap"


def ident(username="asha", external_id="guid-asha", groups=("Kosha Engineers",), **kw):
    return Identity(external_id=external_id, username=username, groups=groups, **kw)


@pytest.fixture
def mapped(kdb):
    from kosha.addins import registry
    registry.discover()          # the official sign-in add-ins in addins/ (links name one of them)
    directory.save_settings({"group_roles": [{"group": "Kosha Engineers", "role": "engineer"},
                                             {"group": "Kosha Approvers", "role": "approver"},
                                             {"group": "Kosha Admins", "role": "admin"}]})
    return kdb


def _local_user(username, role="engineer", password="local-password-1"):
    return db.insert("users", {"username": username, "full_name": username.title(), "email": "", "role": role,
                               "password_hash": security.hash_password(password), "created_at": db.now()})


def test_settings_are_checked(kdb):
    assert directory.settings() == directory.DEFAULTS
    for bad, words in (({"group_roles": "x"}, "list"),
                       ({"group_roles": [{"group": "A", "role": "boss"}]}, "choose a role"),
                       ({"group_roles": [{"group": "A", "role": "viewer"}, {"group": "a", "role": "admin"}]}, "twice"),
                       ({"default_role": "owner"}, "everyone else")):
        with pytest.raises(ValueError, match=words):
            directory.save_settings(bad)
    s = directory.save_settings({"group_roles": [{"group": "  Kosha   Engineers ", "role": "engineer"}, {"group": "", "role": ""}],
                                 "default_role": "viewer", "create_users": False})
    assert s["group_roles"] == [{"group": "Kosha Engineers", "role": "engineer"}]
    assert s["default_role"] == "viewer" and s["create_users"] is False and s["roles_from_directory"] is True


def test_highest_listed_role_wins_and_names_ignore_case(mapped):
    assert directory.role_for(["kosha engineers"]) == "engineer"
    assert directory.role_for(["Kosha Engineers", "KOSHA APPROVERS", "Sales"]) == "approver"
    assert directory.role_for(["Sales"]) is None
    directory.save_settings({"default_role": "viewer"})
    assert directory.role_for([]) == "viewer"


def test_first_sign_in_creates_a_user_that_cannot_use_a_kosha_password(mapped):
    u = directory.complete_sign_in(LDAP, ident(full_name="Asha Rao", email="asha@example.com"), "10.0.0.1")
    assert (u["username"], u["full_name"], u["email"], u["role"]) == ("asha", "Asha Rao", "asha@example.com", "engineer")
    assert u["password_hash"] == directory.EXTERNAL_PASSWORD
    assert not security.verify_password("", u["password_hash"]) and not security.verify_password("!directory", u["password_hash"])
    assert directory.is_linked(u["id"])
    again = directory.complete_sign_in(LDAP, ident(groups=("Kosha Approvers",), full_name="Asha R."), "10.0.0.1")
    assert again["id"] == u["id"] and again["role"] == "approver" and again["full_name"] == "Asha R."
    link = directory.links(u["id"])[0]
    assert link["groups"] == ["Kosha Approvers"] and not link["pending"] and link["last_sign_in_at"]


def test_a_local_account_with_the_same_name_is_never_taken_over(mapped):
    uid = _local_user("daniel", role="viewer")
    with pytest.raises(directory.Refused) as e:
        directory.complete_sign_in(LDAP, ident("Daniel", "guid-daniel"), "10.0.0.2")
    assert e.value.code == "link_needed" and "ask your Kosha administrator to link it" in e.value.message
    row = db.one("SELECT role, password_hash FROM users WHERE id = ?", (uid,))
    assert row["role"] == "viewer" and row["password_hash"] != directory.EXTERNAL_PASSWORD
    assert not directory.is_linked(uid)
    assert db.one("SELECT action, details FROM audit WHERE action = 'login_refused'")["details"].startswith(
        "LDAP / Active Directory sign-in account 'Daniel' from 10.0.0.2: a local account")
    # the admin links it by directory user name; the next directory sign-in completes the link
    pending = directory.link(uid, LDAP, "daniel", None)
    assert pending["pending"] and not directory.is_linked(uid)
    u = directory.complete_sign_in(LDAP, ident("DANIEL", "guid-daniel", groups=("Kosha Approvers",)), "10.0.0.2")
    assert u["id"] == uid and u["role"] == "approver" and directory.is_linked(uid)
    with pytest.raises(ApiError, match="already linked"):
        directory.link(uid, LDAP, "someone", None)
    # a renamed directory account stays the same person: the link is by stable id
    assert directory.complete_sign_in(LDAP, ident("dokafor", "guid-daniel", groups=("Kosha Approvers",)), "x")["id"] == uid


def test_the_built_in_admin_is_never_linked(mapped):
    admin = _local_user("admin", role="admin")
    with pytest.raises(ApiError, match="stays a local account"):
        directory.link(admin, LDAP, "admin", None)
    with pytest.raises(directory.Refused):        # nor taken over by a directory account called admin
        directory.complete_sign_in(LDAP, ident("admin", "guid-x", groups=("Kosha Admins",)), "x")


def test_refusals_say_what_to_do(mapped):
    with pytest.raises(directory.Refused) as e:
        directory.complete_sign_in(LDAP, ident(groups=("Sales",)), "x")
    assert e.value.code == "no_kosha_group" and "Admin › Sign-in & sessions" in e.value.message
    assert not db.scalar("SELECT COUNT(*) FROM users")
    directory.save_settings({"create_users": False})
    with pytest.raises(directory.Refused) as e:
        directory.complete_sign_in(LDAP, ident(), "x")
    assert e.value.code == "no_kosha_account"
    directory.save_settings({"create_users": True})
    with pytest.raises(directory.Refused) as e:
        directory.complete_sign_in(LDAP, ident(username="!!"), "x")
    assert e.value.code == "bad_username"
    u = directory.complete_sign_in(LDAP, ident(username="Asha Rao (Design)"), "x")
    assert u["username"] == "Asha-Rao-Design"
    db.execute("UPDATE users SET active = 0 WHERE id = ?", (u["id"],))
    with pytest.raises(directory.Refused) as e:
        directory.complete_sign_in(LDAP, ident(username="Asha Rao (Design)"), "x")
    assert e.value.code == "account_disabled"


def test_roles_can_stay_with_kosha(mapped):
    directory.save_settings({"roles_from_directory": False})
    u = directory.complete_sign_in(LDAP, ident(), "x")
    db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (u["id"],))
    assert directory.complete_sign_in(LDAP, ident(), "x")["role"] == "admin"


def test_unlink_keeps_the_record_and_ends_sessions(mapped):
    from kosha import sessions
    u = directory.complete_sign_in(LDAP, ident(), "x")
    sessions.create(u["id"], "10.0.0.1", "test")
    link_id = directory.links(u["id"])[0]["id"]
    assert directory.unlink(u["id"], link_id, None) == 1
    assert not directory.is_linked(u["id"]) and directory.links(u["id"]) == []
    row = db.one("SELECT unlinked_at, external_id FROM user_identities WHERE id = ?", (link_id,))
    assert row["unlinked_at"] and row["external_id"] == "guid-asha"
    with pytest.raises(ApiError):
        directory.unlink(u["id"], link_id, None)


def test_round_trips_are_single_use_bound_to_the_browser_and_expire(mapped, monkeypatch):
    calls = []

    class FakeRegistry:
        def call(self, aid, method, *args, **kw):
            calls.append(method)
            if method == "start":
                from kosha_sdk.signin import RedirectStart
                return RedirectStart(f"https://sso.example.com/auth?state={args[1]}", {"nonce": "n1"})
            assert method == "finish" and args[2] == {"nonce": "n1"}
            return ident(username="sofia", external_id="sub-sofia", groups=("Kosha Engineers",))

        def get(self, aid):
            return None

    monkeypatch.setattr(directory, "registry", FakeRegistry())
    monkeypatch.setattr(directory, "provider", lambda aid, style=None: directory.Provider(aid, "SSO", "redirect", "SSO", True))
    url, browser = directory.begin("org.kosha.oidc", "https://plm.example.com/cb", "10.0.0.9")
    state = url.split("state=")[1]
    row = db.one("SELECT * FROM sign_in_requests")
    assert state not in json.dumps(row) and browser not in json.dumps(row) and json.loads(row["saved_json"]) == {"nonce": "n1"}
    with pytest.raises(ApiError) as e:            # another browser brings the answer back
        directory.returned("org.kosha.oidc", {"state": state, "code": "c"}, "other-browser", "10.0.0.9")
    assert e.value.code == "sso_expired"
    url, browser = directory.begin("org.kosha.oidc", "https://plm.example.com/cb", "10.0.0.9")
    state = url.split("state=")[1]
    ticket = directory.returned("org.kosha.oidc", {"state": state, "code": "c"}, browser, "10.0.0.9")
    assert db.one("SELECT saved_json FROM sign_in_requests WHERE ticket_key IS NOT NULL")["saved_json"] == "{}"
    with pytest.raises(ApiError):                 # the same answer twice
        directory.returned("org.kosha.oidc", {"state": state, "code": "c"}, browser, "10.0.0.9")
    with pytest.raises(ApiError):                 # the ticket from another browser
        directory.redeem(ticket, "other-browser", "10.0.0.9", "test")
    u, token = directory.redeem(ticket, browser, "10.0.0.9", "test")
    assert u["username"] == "sofia" and token
    with pytest.raises(ApiError):                 # a ticket works once
        directory.redeem(ticket, browser, "10.0.0.9", "test")
    # too old
    url, browser = directory.begin("org.kosha.oidc", "https://plm.example.com/cb", "10.0.0.9")
    db.execute("UPDATE sign_in_requests SET created_at = '2000-01-01T00:00:00+00:00' WHERE returned_at IS NULL")
    with pytest.raises(ApiError):
        directory.returned("org.kosha.oidc", {"state": url.split("state=")[1], "code": "c"}, browser, "10.0.0.9")
    # too many starts from one address
    monkeypatch.setattr(directory, "STARTS_PER_ADDRESS", 2)
    directory.begin("org.kosha.oidc", "https://plm.example.com/cb", "10.0.0.7")
    directory.begin("org.kosha.oidc", "https://plm.example.com/cb", "10.0.0.7")
    with pytest.raises(ApiError) as e:
        directory.begin("org.kosha.oidc", "https://plm.example.com/cb", "10.0.0.7")
    assert e.value.status == 429
