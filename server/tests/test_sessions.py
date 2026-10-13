# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in throttling, the password policy and revocable sessions, end to end.

Each test signs in from its own made-up network address (X-Forwarded-For, trusted from 127.0.0.1 as from an
HTTPS proxy), so the per-address limit of one test never blocks another.
"""
import json
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from harness import Client, KoshaServer

STRONG = "mixer-shaft-blue-7"


def _login(kosha, user, password, ip, expect=200):
    return Client(kosha.base).call("POST", "/api/auth/login", {"username": user, "password": password},
                                   headers={"X-Forwarded-For": ip}, expect=expect)


def _client(kosha, user, password, ip):
    return Client(kosha.base, _login(kosha, user, password, ip)["token"])


def _db(kosha):
    return sqlite3.connect(kosha.data_dir / "data" / "kosha.db", timeout=30)


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).replace(microsecond=0).isoformat()


def _session_of(kosha, username, sid=None):
    """(id, last_seen_at, ended_reason) of a session: the one given, or the user's newest."""
    with _db(kosha) as con:
        if sid:
            return con.execute("SELECT id, last_seen_at, ended_reason FROM sessions WHERE id = ?", (sid,)).fetchone()
        return con.execute("SELECT s.id, s.last_seen_at, s.ended_reason FROM sessions s JOIN users u ON u.id = s.user_id "
                           "WHERE u.username = ? ORDER BY s.rowid DESC LIMIT 1", (username,)).fetchone()


def test_failed_sign_ins_pause_sign_in_without_revealing_who_exists(kosha):
    admin = kosha.client()
    admin.call("POST", "/api/users", {"username": "pia", "full_name": "Pia", "role": "engineer", "password": STRONG})
    ip = "10.1.0.1"
    for _ in range(4):
        r = _login(kosha, "pia", "wrong-password-1", ip, expect=400)
        assert r == {"error": "Wrong user name or password.", "code": "bad_login"}
    paused = _login(kosha, "PIA", "wrong-password-1", ip, expect=429)          # user names ignore case
    assert paused["code"] == "sign_in_paused" and 840 < paused["retry_after"] <= 900
    assert "try again in 15 minutes" in paused["error"] and "retry_at" in paused
    _login(kosha, "pia", STRONG, ip, expect=429)                               # even the right password waits
    _login(kosha, "pia", STRONG, "10.1.0.2", expect=429)                       # the pause is for the account
    for _ in range(4):
        assert _login(kosha, "ghost", "wrong-password-1", ip, expect=400)["code"] == "bad_login"
    ghost = _login(kosha, "ghost", "wrong-password-1", ip, expect=429)          # unknown user: same answers
    assert ghost["error"] == paused["error"]

    req = urllib.request.Request(kosha.base + "/api/auth/login", method="POST", data=json.dumps(
        {"username": "ghost", "password": "x"}).encode(), headers={"Content-Type": "application/json",
                                                                    "X-Forwarded-For": ip})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 429 and 0 < int(e.value.headers["Retry-After"]) <= 900

    entries = admin.call("GET", "/api/audit?entity=user&limit=500")["entries"]
    failed = [x for x in entries if x["action"] == "login_failed"]
    assert any("'pia' from 10.1.0.1: wrong password" in x["details"] for x in failed)
    assert any("'ghost' from 10.1.0.1: unknown user name" in x["details"] for x in failed)
    assert any(x["action"] == "login_paused" and "'pia'" in x["details"] for x in entries)

    with _db(kosha) as con:                                                    # the pause runs out
        con.execute("UPDATE sign_in_failures SET at = ? WHERE username = 'pia'", (_ago(minutes=16),))
    _login(kosha, "pia", STRONG, ip)
    assert any(x["action"] == "login" and "from 10.1.0.1" in x["details"] and x["user"] == "Pia"
               for x in admin.call("GET", "/api/audit?entity=user&limit=50")["entries"])
    with _db(kosha) as con:
        assert con.execute("SELECT COUNT(*) FROM sign_in_failures WHERE username = 'pia'").fetchone()[0] == 0


def test_failed_sign_ins_from_one_address_pause_that_address(kosha):
    admin = kosha.client()
    admin.call("PUT", "/api/admin/security", {"sign_in_ip_max_failures": 6})
    try:
        ip = "10.2.0.1"
        for i in range(5):
            _login(kosha, f"guess{i}", "wrong-password-1", ip, expect=400)
        _login(kosha, "guess5", "wrong-password-1", ip, expect=429)
        _login(kosha, "admin", "admin-test-pass-1", ip, expect=429)            # the whole address waits
        _login(kosha, "admin", "admin-test-pass-1", "10.2.0.2")                # other addresses don't
    finally:
        admin.call("PUT", "/api/admin/security", {"sign_in_ip_max_failures": 20})
    admin.call("PUT", "/api/admin/security", {"sign_in_ip_max_failures": 2}, expect=400)
    assert admin.call("GET", "/api/admin/security")["settings"]["sign_in_ip_max_failures"] == 20
    kosha.add_user("viv", "viewer", STRONG).call("GET", "/api/admin/security", expect=403)


def test_password_policy_when_creating_and_resetting(kosha):
    admin = kosha.client()
    for pw, why in (("short-1", "at least 10"), ("1234567890", "too common"), ("Qwertyuiop", "too common"),
                    ("ravi.kumar", "same as the user name")):
        r = admin.call("POST", "/api/users", {"username": "ravi.kumar", "role": "viewer", "password": pw}, expect=400)
        assert r["code"] == "weak_password" and why in r["error"]
    u = admin.call("POST", "/api/users", {"username": "ravi.kumar", "role": "viewer", "password": STRONG})["user"]
    r = admin.call("PATCH", f"/api/users/{u['id']}", {"password": "password123"}, expect=400)
    assert r["code"] == "weak_password"
    rules = Client(kosha.base).call("GET", "/api/auth/password-rules")
    assert rules["min_length"] == 10 and "common" in rules["hint"]


def test_changing_the_password_ends_every_other_session(kosha):
    kosha.add_user("mara", "engineer", STRONG)
    one = _client(kosha, "mara", STRONG, "10.3.0.1")
    two = _client(kosha, "mara", STRONG, "10.3.0.2")
    r = one.call("POST", "/api/auth/password", {"current": "not-it-at-all", "new": "another-good-pass"}, expect=400)
    assert r["code"] == "bad_password"
    assert one.call("POST", "/api/auth/password", {"current": STRONG, "new": "short"}, expect=400)["code"] == "weak_password"
    assert one.call("POST", "/api/auth/password", {"current": STRONG, "new": STRONG}, expect=400)["code"] == "weak_password"
    r = one.call("POST", "/api/auth/password", {"current": STRONG, "new": "another-good-pass"})
    assert r["sessions_ended"] == 3 and r["token"]                         # add_user signed in once, too
    one.call("GET", "/api/auth/me", expect=401)
    two.call("GET", "/api/auth/me", expect=401)
    assert Client(kosha.base, r["token"]).call("GET", "/api/auth/me")["user"]["username"] == "mara"
    _login(kosha, "mara", STRONG, "10.3.0.3", expect=400)
    _login(kosha, "mara", "another-good-pass", "10.3.0.3")
    assert _session_of(kosha, "mara") is not None


def test_signing_out_ends_the_session(kosha):
    kosha.add_user("lena", "viewer", STRONG)
    c = _client(kosha, "lena", STRONG, "10.4.0.1")
    keep = _client(kosha, "lena", STRONG, "10.4.0.2")
    c.call("POST", "/api/auth/logout")
    c.call("GET", "/api/auth/me", expect=401)
    keep.call("GET", "/api/auth/me")                                            # only that session ended
    with _db(kosha) as con:
        reasons = [r[0] for r in con.execute("SELECT ended_reason FROM sessions s JOIN users u ON u.id = s.user_id "
                                             "WHERE u.username = 'lena'")]
    assert sorted(reasons) == ["", "", "signed out"]                          # add_user signed in once, too


def test_admin_can_end_sessions_and_deactivation_works_at_once(kosha):
    admin = kosha.client()
    kosha.add_user("omar", "engineer", STRONG)
    uid = next(u["id"] for u in admin.call("GET", "/api/users")["users"] if u["username"] == "omar")
    a = _client(kosha, "omar", STRONG, "10.5.0.1")
    b = _client(kosha, "omar", STRONG, "10.5.0.2")
    listed = admin.call("GET", f"/api/users/{uid}/sessions")["sessions"]
    assert {s["ip"] for s in listed} >= {"10.5.0.1", "10.5.0.2"}
    a.call("POST", f"/api/users/{uid}/sign-out-everywhere", expect=403)
    assert admin.call("POST", f"/api/users/{uid}/sign-out-everywhere")["sessions_ended"] == 3   # add_user signed in too
    a.call("GET", "/api/auth/me", expect=401)
    b.call("GET", "/api/auth/me", expect=401)
    assert admin.call("GET", f"/api/users/{uid}/sessions")["sessions"] == []

    c = _client(kosha, "omar", STRONG, "10.5.0.3")                             # can sign in again at once
    admin.call("PATCH", f"/api/users/{uid}", {"active": False})
    c.call("GET", "/api/auth/me", expect=401)
    _login(kosha, "omar", STRONG, "10.5.0.4", expect=400)                     # same answer as a wrong password
    admin.call("PATCH", f"/api/users/{uid}", {"active": True})
    c.call("GET", "/api/auth/me", expect=401)                                   # ended sessions stay ended

    d = _client(kosha, "omar", STRONG, "10.5.0.5")
    admin.call("PATCH", f"/api/users/{uid}", {"password": "brand-new-pass-9"})
    d.call("GET", "/api/auth/me", expect=401)
    _login(kosha, "omar", "brand-new-pass-9", "10.5.0.5")
    actions = [e["action"] for e in admin.call("GET", f"/api/audit?entity=user&entity_id={uid}")["entries"]]
    assert "sign_out_everywhere" in actions and "user_update" in actions


def test_idle_and_absolute_session_limits(kosha):
    admin = kosha.client()
    kosha.add_user("ida", "viewer", STRONG)
    c = _client(kosha, "ida", STRONG, "10.6.0.1")
    sid = _session_of(kosha, "ida")[0]
    with _db(kosha) as con:
        con.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (_ago(minutes=31), sid))
    c.call("GET", "/api/auth/me", expect=401)
    assert _session_of(kosha, "ida", sid)[2] == "idle"

    admin.call("PUT", "/api/admin/security", {"session_idle_minutes": 120})
    try:
        c = _client(kosha, "ida", STRONG, "10.6.0.1")
        sid = _session_of(kosha, "ida")[0]
        with _db(kosha) as con:
            con.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (_ago(minutes=31), sid))
        c.call("GET", "/api/auth/me")                                           # within the new idle limit
        with _db(kosha) as con:
            con.execute("UPDATE sessions SET created_at = ? WHERE id = ?", (_ago(hours=13), sid))
        c.call("GET", "/api/auth/me", expect=401)                               # past the 12-hour lifetime
        assert _session_of(kosha, "ida", sid)[2] == "expired"
    finally:
        admin.call("PUT", "/api/admin/security", {"session_idle_minutes": 30})


def test_background_polls_do_not_keep_a_session_alive(kosha):
    kosha.add_user("bea", "viewer", STRONG)
    c = _client(kosha, "bea", STRONG, "10.7.0.1")
    sid = _session_of(kosha, "bea")[0]
    old = _ago(minutes=5)
    with _db(kosha) as con:
        con.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (old, sid))
    c.call("GET", "/api/dashboard", headers={"X-Kosha-Background": "1"})
    assert _session_of(kosha, "bea")[1] == old
    c.call("GET", "/api/dashboard")
    assert _session_of(kosha, "bea")[1] > old


def test_tokens_need_an_open_session_and_links_still_work(kosha):
    admin = kosha.client()
    secret = (kosha.data_dir / "data" / "secret.key").read_text().strip()
    now = int(time.time())
    no_session = jwt.encode({"sub": "1", "iat": now, "exp": now + 3600}, secret, algorithm="HS256")
    Client(kosha.base, no_session).call("GET", "/api/auth/me", expect=401)       # tokens from before sessions
    made_up = jwt.encode({"sub": "1", "sid": "made-up", "iat": now, "exp": now + 3600}, secret, algorithm="HS256")
    Client(kosha.base, made_up).call("GET", "/api/auth/me", expect=401)
    assert Client(kosha.base).call("GET", f"/api/auth/me?access_token={admin.token}")["user"]["username"] == "admin"
    Client(kosha.base, admin.token[:-2] + "xx").call("GET", "/api/auth/me", expect=401)


def test_a_weak_first_admin_password_stops_start_up():
    s = KoshaServer(env={"KOSHA_ADMIN_PASSWORD": "admin12345"})
    try:
        with pytest.raises(RuntimeError, match="KOSHA_ADMIN_PASSWORD is not accepted"):
            s.start()
    finally:
        s.stop()
