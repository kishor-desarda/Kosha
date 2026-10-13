# SPDX-License-Identifier: AGPL-3.0-or-later
"""Notices end to end: e-mail through an SMTP stub and Slack through a simulated webhook, people's own choices,
approvals, failed ERP transfers and releases, and that a failing notifier never blocks the action behind a notice."""
import time

import pytest

from erp_helpers import folder, released_part, serve
from fake_smtp import FakeSmtp
from harness import free_port

import fake_slack as FS  # noqa: E402  (found through erp_helpers)

SLACK = "org.kosha.slack"
TEAMS = "org.kosha.teams"


@pytest.fixture(scope="module")
def smtp():
    s = FakeSmtp().start()
    yield s
    s.stop()


@pytest.fixture(scope="module")
def slack():
    FS.reset()
    server, url = serve(FS.app)
    yield f"{url}/services/{FS.TEAM}/{FS.HOOK}/{FS.SECRET}"
    server.should_exit = True


def _user(kosha, username, role, full_name):
    kosha.client().call("POST", "/api/users", {"username": username, "full_name": full_name, "role": role,
                                               "email": f"{username}@example.com", "password": "password-123"})
    return kosha.client(username, "password-123")


def _rows(admin, n, timeout=10):
    """Wait until the outbox holds at least n notices (they are queued in the background)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = admin.call("GET", "/api/notify/outbox")["rows"]
        if len(rows) >= n:
            return rows
        time.sleep(0.1)
    raise AssertionError(f"expected {n} notices, have {admin.call('GET', '/api/notify/outbox')['rows']}")


def _mail_to(smtp, address):
    return [m for m in smtp.messages if address in m["to"]]


def test_notices(kosha, smtp, slack):
    admin = kosha.client()
    asha = _user(kosha, "asha", "engineer", "Asha Menon")
    daniel = _user(kosha, "daniel", "approver", "Daniel Okafor")
    tom = _user(kosha, "tom", "viewer", "Tom Becker")
    asha.call("GET", "/api/notify/settings", expect=403)

    # --- the admin sets up e-mail and Slack, and tests both
    s = admin.call("PUT", "/api/notify/settings", {"base_url": "https://plm.example.com", "smtp": {
        "enabled": True, "host": "127.0.0.1", "port": smtp.port, "security": "none", "from_address": "kosha@example.com",
        "password": "not-needed-1"}})
    assert s["smtp"]["password"] != "not-needed-1"
    assert next(c for c in s["channels"] if c["id"] == "email")["ready"]
    assert admin.call("POST", "/api/notify/email/check")["ok"]
    r = admin.call("POST", "/api/notify/email/test", {"to": "admin@example.com"})
    assert r["ok"], r
    assert smtp.wait_for(1)[0]["message"]["Subject"] == "[Kosha] Test message from Kosha"
    admin.call("PUT", f"/api/addins/{SLACK}/settings", {"webhook_url": slack})
    assert admin.call("POST", f"/api/notify/channels/{SLACK}/test")["ok"]
    assert len(FS.STATE["posts"]) == 1
    bad = admin.call("POST", f"/api/notify/channels/{TEAMS}/test")
    assert not bad["ok"] and "not set" in bad["message"]

    # --- Daniel wants approvals by e-mail and in Slack; Teams is offered but not set up yet
    mine = daniel.call("GET", "/api/me/notifications")
    channels = {c["id"]: c for c in mine["channels"]}
    assert channels["email"]["ready"] and channels[SLACK]["ready"] and not channels[TEAMS]["ready"]
    assert "Webhook address" in channels[TEAMS]["why"] and mine["email"] == "daniel@example.com"
    assert mine["prefs"]["approvals"] == ["email"] and mine["prefs"]["releases"] == []
    daniel.call("PUT", "/api/me/notifications", {"prefs": {"approvals": ["email", SLACK]}})
    daniel.call("PUT", "/api/me/notifications", {"prefs": {"weather": ["email"]}}, expect=400)

    # --- a change order waiting for approval reaches the approver named on it
    f = folder(asha, "Mixers")
    doc = asha.call("POST", "/api/documents/upload", files={"file": ("DE-400-001 Paddle.dxf", _dxf("DE-400-001"))},
                    fields={"folder_id": f})["documents"][0]
    me = daniel.call("GET", "/api/auth/me")["user"]
    co = asha.call("POST", "/api/changes", {"title": "Stronger paddle", "reason": "Cracks at the hub",
                                            "document_ids": [doc["id"]], "approver_ids": [me["id"]]})
    asha.call("POST", f"/api/changes/{co['id']}/submit")
    rows = _rows(admin, 2)
    assert {(r["channel"], r["kind"], r["status"]) for r in rows} == {("email", "approvals", "pending"),
                                                                      (SLACK, "approvals", "pending")}
    assert admin.call("POST", "/api/notify/process")["processed"] == 2
    mail = _mail_to(smtp, "daniel@example.com")[-1]["message"]
    assert mail["Subject"] == f"[Kosha] {co['number']} needs your approval: Stronger paddle"
    plain = mail.get_body(("plain",)).get_content()
    assert "Asha Menon asks you to approve" in plain and "Reason: Cracks at the hub" in plain
    assert f"https://plm.example.com/#/change/{co['id']}" in plain and mail.get_body(("html",)) is not None
    post = FS.STATE["posts"][-1]
    assert "needs your approval" in post["text"] and post["blocks"][1]["elements"][0]["text"] == "For: Daniel Okafor"

    # --- a failing notifier never blocks or breaks the action that caused the notice
    FS.STATE["mode"] = "broken"
    admin.call("PUT", f"/api/addins/{TEAMS}/settings", {"webhook_url": f"http://127.0.0.1:{free_port()}/hook"})
    daniel.call("PUT", "/api/me/notifications", {"prefs": {"approvals": ["email", SLACK, TEAMS]}})
    doc2 = asha.call("POST", "/api/documents/upload", files={"file": ("DE-400-002 Hub.dxf", _dxf("DE-400-002"))},
                     fields={"folder_id": f})["documents"][0]
    co2 = asha.call("POST", "/api/changes", {"title": "New hub", "document_ids": [doc2["id"]], "approver_ids": [me["id"]]})
    t0 = time.time()
    done = asha.call("POST", f"/api/changes/{co2['id']}/submit")
    assert done["state"] == "submitted" and time.time() - t0 < 3
    _rows(admin, 5)
    admin.call("POST", "/api/notify/process")
    log = {r["channel"]: r for r in admin.call("GET", "/api/notify/outbox")["rows"] if r["source"] == f"change:{co2['id']}"}
    assert log["email"]["status"] == "sent"
    assert log[SLACK]["status"] == "pending" and "Slack had a problem" in log[SLACK]["last_error"]
    assert log[TEAMS]["status"] == "pending" and "Cannot reach" in log[TEAMS]["last_error"]
    assert log[SLACK]["attempt_log"][0]["ok"] == 0
    FS.STATE["mode"] = "ok"
    r = admin.call("POST", f"/api/notify/outbox/{log[SLACK]['id']}/retry")
    assert r["row"]["status"] == "sent"
    assert asha.call("GET", f"/api/changes/{co2['id']}")["state"] == "submitted"

    # --- a failed ERP transfer is told to approvers and admins (not engineers), once per error and day
    admin.call("PATCH", "/api/users/1", {"email": "admin@example.com"})
    admin.call("PUT", "/api/erp/settings", {"enabled": True, "dry_run": False, "connector": "org.kosha.erpnext",
                                            "base_url": f"http://127.0.0.1:{free_port()}", "api_key": "k",
                                            "api_secret": "s", "timeout_s": 2})
    item = asha.call("GET", "/api/items?q=DE-400-001")["items"][0]
    before = len(admin.call("GET", "/api/notify/outbox")["rows"])
    job = admin.call("POST", "/api/erp/push", {"kind": "item", "id": item["id"]})["job"]
    assert job["status"] == "failed"
    failed = [r for r in _rows(admin, before + 2) if r["kind"] == "erp_failures"]
    assert sorted(r["address"] for r in failed) == ["admin@example.com", "daniel@example.com"]
    assert failed[0]["severity"] == "error" and "Nothing was lost in Kosha" in failed[0]["body"]
    assert failed[0]["title"] == "ERP transfer failed: item DE-400-001 PART"
    admin.call("POST", f"/api/erp/jobs/{job['id']}/retry")                      # fails again with the same error
    time.sleep(1)
    assert len([r for r in admin.call("GET", "/api/notify/outbox")["rows"] if r["kind"] == "erp_failures"]) == 2
    admin.call("PUT", "/api/erp/settings", {"enabled": False})

    # --- releases are opt-in: only Tom asked for them
    tom.call("PUT", "/api/me/notifications", {"prefs": {"releases": ["email"]}})
    before = len(admin.call("GET", "/api/notify/outbox")["rows"])
    released_part(admin, f, "DE-400-009", "SCRAPER")
    rows = _rows(admin, before + 1)
    rel = [r for r in rows if r["kind"] == "releases"]
    assert [r["address"] for r in rel] == ["tom@example.com"] and rel[0]["title"].startswith("Released: DE-400-009")
    admin.call("POST", "/api/notify/process")
    assert _mail_to(smtp, "tom@example.com")
    counts = admin.call("GET", "/api/notify/outbox")["counts"]
    assert counts["failed"] == 0 and counts["sent"] >= 6


def _dxf(pn):
    from kosha.cad import dxfwriter as W
    return W.flange(values={"PART_NO": pn, "DESCRIPTION": "PART"})
