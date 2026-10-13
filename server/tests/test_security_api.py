# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: two-step sign-in, the bulk-download alarm, the tamper-evident audit chain, and personal data export
and erase (specification 15.1, 15.3, 15.5 and 15.7)."""
import time

from harness import Client

from kosha import twostep
from kosha.cad import dxfwriter as W


def _code(secret: str, offset: int = 0) -> str:
    return twostep.code_at(secret.replace(" ", ""), int(time.time() // 30) + offset)


def _login(kosha, username, password="password-123", expect=200):
    return Client(kosha.base).call("POST", "/api/auth/login", {"username": username, "password": password},
                                   expect=expect)


def test_two_step_sign_in(kosha):
    admin = kosha.client()
    kosha.add_user("ts-appr", "approver")
    eng = kosha.add_user("ts-eng", "engineer")

    # an engineer switches it on for themselves: the first code proves the app works
    setup = eng.call("POST", "/api/me/two-step/start")
    assert setup["uri"].startswith("otpauth://totp/") and "secret=" in setup["uri"]
    assert eng.call("POST", "/api/me/two-step/confirm", {"code": "000000"}, expect=400)["code"] == "bad_code"
    on = eng.call("POST", "/api/me/two-step/confirm", {"code": _code(setup["secret"])})
    assert on["on"] and len(on["recovery_codes"]) == 10 and on["recovery_left"] == 10

    # signing in now takes the password, then the code
    first = _login(kosha, "ts-eng")
    assert first["two_step"] == "verify" and "token" not in first
    anon = Client(kosha.base)
    assert anon.call("POST", "/api/auth/two-step", {"challenge": first["challenge"], "code": "123456"},
                     expect=400)["code"] == "bad_code"
    assert anon.call("POST", "/api/auth/two-step", {"challenge": "forged", "code": "1"},
                     expect=401)["code"] == "challenge_expired"
    # the code of the current step was used when setting up, so the next one is needed
    done = anon.call("POST", "/api/auth/two-step", {"challenge": first["challenge"], "code": _code(setup["secret"], 1)})
    assert done["token"] and done["user"]["username"] == "ts-eng"
    # a code is accepted once
    again = _login(kosha, "ts-eng")
    assert "used" in anon.call("POST", "/api/auth/two-step", {"challenge": again["challenge"],
                                                              "code": _code(setup["secret"], 1)}, expect=400)["error"]
    # a recovery code works once
    rc = on["recovery_codes"][0]
    assert anon.call("POST", "/api/auth/two-step", {"challenge": again["challenge"], "code": rc})["token"]
    third = _login(kosha, "ts-eng")
    anon.call("POST", "/api/auth/two-step", {"challenge": third["challenge"], "code": rc}, expect=400)
    assert eng.call("GET", "/api/me/two-step")["recovery_left"] == 9

    # the admin requires it for approvers: an approver without it sets it up while signing in
    s = admin.call("GET", "/api/admin/security")["settings"]
    assert s["two_step_roles"] == [] and s["new_place_alerts"] is True
    assert "office networks" in admin.call("PUT", "/api/admin/security", {"two_step_outside_office": True},
                                           expect=400)["error"]
    admin.call("PUT", "/api/admin/security", {"two_step_roles": ["approver"]})
    r = _login(kosha, "ts-appr")
    assert r["two_step"] == "setup"
    key = anon.call("POST", "/api/auth/two-step/setup", {"challenge": r["challenge"]})["secret"]
    signed = anon.call("POST", "/api/auth/two-step/setup/confirm", {"challenge": r["challenge"], "code": _code(key)})
    assert signed["token"] and len(signed["recovery_codes"]) == 10
    appr = Client(kosha.base, signed["token"])
    # required for the role: it cannot be switched off, only reset by an admin (a lost phone)
    assert appr.call("POST", "/api/me/two-step/off", {"code": _code(key, 1)}, expect=400)["code"] == "required"
    uid = appr.call("GET", "/api/auth/me")["user"]["id"]
    assert next(u for u in admin.call("GET", "/api/users")["users"] if u["id"] == uid)["two_step_on"]
    admin.call("POST", f"/api/users/{uid}/two-step/reset")
    appr.call("GET", "/api/auth/me", expect=401)                 # signed out
    assert _login(kosha, "ts-appr")["two_step"] == "setup"

    # someone not required switches it off with a code
    assert eng.call("POST", "/api/me/two-step/off", {"code": on["recovery_codes"][1]})["on"] is False
    assert "token" in _login(kosha, "ts-eng")
    admin.call("PUT", "/api/admin/security", {"two_step_roles": []})

    # outside the office: everyone signing in from elsewhere needs it (this test signs in from 127.0.0.1)
    admin.call("PUT", "/api/admin/security", {"office_networks": "10.20.0.0/16", "two_step_outside_office": True})
    assert _login(kosha, "ts-eng")["two_step"] == "setup"
    admin.call("PUT", "/api/admin/security", {"office_networks": "127.0.0.0/8"})
    assert "token" in _login(kosha, "ts-eng")
    admin.call("PUT", "/api/admin/security", {"two_step_outside_office": False})


def test_bulk_download_alarm_and_audit_chain(kosha):
    admin = kosha.client()
    eng = kosha.add_user("bd-eng", "engineer")
    root = next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])
    doc = eng.call("POST", "/api/documents/upload", fields={"folder_id": root}, files={
        "file": ("BD-100-001 Plate.dxf", W.flange(values={"PART_NO": "BD-100-001", "DESCRIPTION": "PLATE",
                                                            "MATERIAL": "S235"}))})["documents"][0]
    admin.call("PUT", "/api/admin/security", {"bulk_download_per_hour": 3})
    for _ in range(3):
        eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True)
    r = eng.call("GET", f"/api/documents/{doc['id']}/file", expect=423)
    assert r["code"] == "downloads_paused" and "administrator" in r["error"]
    eng.call("GET", f"/api/documents/{doc['id']}/preview", raw=True)       # viewing still works
    me = eng.call("GET", "/api/auth/me")["user"]["id"]
    assert next(u for u in admin.call("GET", "/api/users")["users"] if u["id"] == me)["downloads_paused_at"]
    admin.call("PUT", "/api/admin/security", {"bulk_download_per_hour": 0})
    eng.call("GET", f"/api/documents/{doc['id']}/file", expect=423)        # stays paused until an admin says so
    admin.call("POST", f"/api/users/{me}/downloads/resume")
    eng.call("GET", f"/api/documents/{doc['id']}/file", raw=True)
    with kosha.db() as con:
        assert con.execute("SELECT COUNT(*) FROM notifications WHERE title LIKE 'Downloads paused%'").fetchone()[0] >= 1

    # every entry is chained to the one before; a changed entry is found
    eng.call("GET", "/api/audit/verify", expect=403)
    ok = admin.call("GET", "/api/audit/verify")
    assert ok["ok"] and ok["checked"] > 10
    with kosha.db() as con:
        victim = con.execute("SELECT id FROM audit WHERE action = 'download' ORDER BY id LIMIT 1").fetchone()[0]
        con.execute("UPDATE audit SET details = 'nothing to see' WHERE id = ?", (victim,))
    bad = admin.call("GET", "/api/audit/verify")
    assert not bad["ok"] and bad["broken_at"] == victim and "changed" in bad["reason"]
    with kosha.db() as con:
        con.execute("DELETE FROM audit WHERE id = ?", (victim,))
    gone = admin.call("GET", "/api/audit/verify")
    assert not gone["ok"] and gone["broken_at"] > victim and "removed" in gone["reason"]


def test_personal_data_export_and_erase(kosha):
    admin = kosha.client()
    tom = kosha.add_user("pd-tom", "engineer", full_name="Tom Example")
    me = tom.call("GET", "/api/auth/me")["user"]["id"]
    data = admin.call("GET", f"/api/users/{me}/personal-data")
    assert data["profile"]["username"] == "pd-tom" and data["sessions"] and data["audit"]
    assert "password_hash" not in data["profile"] and "totp_secret" not in data["profile"]
    tom.call("GET", f"/api/users/{me}/personal-data", expect=403)

    assert "Type the user name" in admin.call("POST", f"/api/users/{me}/erase", {"confirm": "nope"}, expect=400)["error"]
    admin_id = admin.call("GET", "/api/auth/me")["user"]["id"]
    admin.call("POST", f"/api/users/{admin_id}/erase", {"confirm": "admin"}, expect=409)
    gone = admin.call("POST", f"/api/users/{me}/erase", {"confirm": "pd-tom"})
    assert gone["full_name"] == f"Former user {me}"
    tom.call("GET", "/api/auth/me", expect=401)                              # signed out
    _login(kosha, "pd-tom", expect=400)                                       # and the name is gone
    u = next(x for x in admin.call("GET", "/api/users")["users"] if x["id"] == me)
    assert u["username"] == f"erased-{me}" and u["email"] == "" and not u["active"]
    admin.call("POST", f"/api/users/{me}/erase", {"confirm": f"erased-{me}"}, expect=409)


def test_sign_in_from_a_new_place_tells_admins(kdb):
    from kosha import db, security
    with db.tx():
        db.insert("users", {"username": "np-admin", "full_name": "Admin", "role": "admin",
                            "password_hash": security.hash_password("x" * 12), "created_at": db.now()})
        uid = db.insert("users", {"username": "np-asha", "full_name": "Asha", "role": "engineer",
                                  "password_hash": security.hash_password("x" * 12), "created_at": db.now()})
    u = db.one("SELECT * FROM users WHERE id = ?", (uid,))
    assert twostep.note_place(u, "192.168.1.20", "Firefox") is False        # the first place is not news
    assert twostep.note_place(u, "192.168.1.77", "Firefox") is False        # same network
    assert twostep.note_place(u, "203.0.113.9", "Firefox") is True          # somewhere new
    assert twostep.place_of("2001:db8:1:2::5") == "2001:db8:1::/48"
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE title LIKE '%new place%'") == 1
    assert db.scalar("SELECT COUNT(*) FROM audit WHERE action = 'login_new_place'") == 1


def test_totp_matches_rfc_6238():
    # RFC 6238 appendix B, SHA-1, secret "12345678901234567890", last six digits
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert twostep.code_at(secret, 59 // 30) == "287082"
    assert twostep.code_at(secret, 1111111109 // 30) == "081804"
    assert twostep.code_at(secret, 2000000000 // 30) == "279037"
