# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: programs and events (specification 13.2 and 13.4). API keys with scopes, addresses and revocation;
programs never approve or delete; signed webhooks with retries and replay; the live event stream for screens."""
import hashlib
import hmac
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from harness import Client


def _service_account(admin, username="robot"):
    return admin.call("POST", "/api/users", {"username": username, "full_name": "Drawing check robot",
                                             "role": "approver", "kind": "service",
                                             "password": "robot-password-123"})["user"]["id"]


def _wait(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if r:
            return r
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_api_keys_scopes_and_limits(kosha):
    admin = kosha.client()
    person = kosha.add_user("pk-eng", "engineer")
    sid = _service_account(admin)

    # keys only for service accounts; scopes are checked
    me = person.call("GET", "/api/auth/me")["user"]["id"]
    assert admin.call("POST", f"/api/users/{me}/api-keys", {"name": "x", "scopes": ["items:read"]},
                      expect=409)["code"] == "not_service_account"
    assert "scope" in admin.call("POST", f"/api/users/{sid}/api-keys", {"name": "x", "scopes": ["boss:all"]},
                                 expect=400)["error"]
    made = admin.call("POST", f"/api/users/{sid}/api-keys",
                      {"name": "Drawing check robot", "scopes": ["items:read", "items:write", "changes:write"]})["key"]
    key = made["key"]
    assert key.startswith("ksk_") and made["key_prefix"] == key[:12]
    listed = admin.call("GET", f"/api/users/{sid}/api-keys")["keys"]
    assert "key" not in listed[0] and "key_hash" not in listed[0] and listed[0]["active"]

    bot = Client(kosha.base, key)
    assert bot.call("GET", "/api/auth/me")["user"]["username"] == "robot"
    item = bot.call("POST", "/api/items", {"part_number": "PK-100-001", "description": "BRACKET"})["item"]
    assert bot.call("GET", "/api/items?q=PK-100")["items"][0]["id"] == item["id"]
    # the X-API-Key header works too
    assert Client(kosha.base).call("GET", "/api/items", headers={"X-API-Key": key})["items"]
    # never in the address
    Client(kosha.base).call("GET", f"/api/items?access_token={key}", expect=401)

    # outside its scopes, outside the API areas, and deleting: refused with a plain reason
    assert bot.call("GET", "/api/boms", expect=403)["code"] == "scope_missing"
    assert "files:read" in bot.call("GET", "/api/folders", expect=403)["error"]
    assert "person" in bot.call("GET", "/api/users", expect=403)["error"]
    assert "person" in bot.call("DELETE", "/api/oem-refs/1", expect=403)["error"]

    # a program proposes but never approves: the account is an approver, the key acts as an engineer at most
    wide = Client(kosha.base, admin.call("POST", f"/api/users/{sid}/api-keys",
                                         {"name": "Files", "scopes": ["files:read", "files:write"]})["key"]["key"])
    root = admin.call("GET", "/api/folders")["folders"][0]["id"]
    from kosha.cad import dxfwriter as W
    up = admin.call("POST", "/api/documents/upload", fields={"folder_id": root},
                    files={"file": ("PK-100-002 Plate.dxf",
                                    W.flange(values={"PART_NO": "PK-100-002", "DESCRIPTION": "PLATE",
                                                     "MATERIAL": "S235"}))})["documents"][0]
    wide.call("POST", f"/api/documents/{up['id']}/state", {"to": "released"}, expect=403)

    # usage is recorded; a revoked key stops at once and stays listed
    k = admin.call("GET", f"/api/users/{sid}/api-keys")["keys"][0]
    assert k["last_used_at"] and k["last_used_ip"] == "127.0.0.1"
    gone = admin.call("POST", f"/api/api-keys/{k['id']}/revoke")["key"]
    assert gone["revoked_at"] and not gone["active"] and gone["revoked_by_name"]
    bot.call("GET", "/api/items", expect=401)

    # limited to addresses: from elsewhere the key is refused
    far = admin.call("POST", f"/api/users/{sid}/api-keys",
                     {"name": "Far", "scopes": ["items:read"], "allowed_ips": "10.9.9.0/24"})["key"]["key"]
    Client(kosha.base, far).call("GET", "/api/items", expect=401)
    near = admin.call("POST", f"/api/users/{sid}/api-keys",
                      {"name": "Near", "scopes": ["items:read"], "allowed_ips": "127.0.0.0/8"})["key"]["key"]
    Client(kosha.base, near).call("GET", "/api/items")
    assert "address" in admin.call("POST", f"/api/users/{sid}/api-keys",
                                   {"name": "Bad", "scopes": ["items:read"], "allowed_ips": "here"}, expect=400)["error"]

    # only admins manage keys; the service account cannot sign in on a screen
    person.call("GET", f"/api/users/{sid}/api-keys", expect=403)
    Client(kosha.base).call("POST", "/api/auth/login", {"username": "robot", "password": "robot-password-123"},
                            expect=403)


class _Receiver:
    """A small HTTP server standing in for another program."""

    def __init__(self):
        self.calls, self.fail_next = [], 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):      # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.calls.append((dict(self.headers), body))
                code = 500 if outer.fail_next > 0 else 200
                outer.fail_next -= 1
                self.send_response(code)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/kosha"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def test_webhooks_signed_retried_and_replayed(kosha):
    admin = kosha.client()
    eng = kosha.add_user("wh-eng", "engineer")
    rx = _Receiver()
    try:
        assert "https" in admin.call("POST", "/api/webhooks", {"name": "x", "url": "ftp://x", "events": ["item.created"]},
                                     expect=400)["error"]
        assert "event" in admin.call("POST", "/api/webhooks", {"name": "x", "url": rx.url, "events": ["nope"]},
                                     expect=400)["error"]
        hook = admin.call("POST", "/api/webhooks", {"name": "Shop-floor screens", "url": rx.url,
                                                    "events": ["item.created"]})["webhook"]
        secret = hook["secret"]
        assert secret.startswith("whsec_")
        assert "secret" not in admin.call("GET", "/api/webhooks")["webhooks"][0]
        eng.call("GET", "/api/webhooks", expect=403)

        # an event becomes a signed delivery
        eng.call("POST", "/api/items", {"part_number": "WH-100-001", "description": "SHAFT"})
        _wait(lambda: admin.call("GET", "/api/webhooks")["deliveries"])
        assert admin.call("POST", "/api/webhooks/process")["processed"] == 1
        headers, body = rx.calls[0]
        payload = json.loads(body)
        assert payload["event"] == "item.created" and payload["data"]["part_number"] == "WH-100-001"
        assert headers["X-Kosha-Event"] == "item.created"
        sig = dict(p.split("=", 1) for p in headers["X-Kosha-Signature"].split(","))
        want = hmac.new(secret.encode(), f"{sig['t']}.".encode() + body, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(sig["v1"], want)
        first = admin.call("GET", "/api/webhooks")["deliveries"][0]
        assert first["status"] == "sent" and first["attempts"] == 1 and first["last_status"] == 200

        # a failing receiver: kept, tried again later with a growing wait
        rx.fail_next = 1
        eng.call("POST", "/api/items", {"part_number": "WH-100-002", "description": "SHAFT"})
        _wait(lambda: len(admin.call("GET", "/api/webhooks")["deliveries"]) == 2)
        admin.call("POST", "/api/webhooks/process")
        d = admin.call("GET", "/api/webhooks")["deliveries"][0]
        assert d["status"] == "pending" and d["attempts"] == 1 and "500" in d["last_error"] and d["next_at"] > first["sent_at"]
        assert admin.call("POST", "/api/webhooks/process")["processed"] == 0      # not due yet
        with kosha.db() as con:
            con.execute("UPDATE webhook_deliveries SET next_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (d["id"],))
        admin.call("POST", "/api/webhooks/process")
        d = admin.call("GET", "/api/webhooks")["deliveries"][0]
        assert d["status"] == "sent" and d["attempts"] == 2

        # replay sends the same event again (same event id, so receivers can ignore duplicates)
        again = admin.call("POST", f"/api/webhooks/deliveries/{first['id']}/replay")["delivery"]
        assert again["replay_of"] == first["id"] and again["event_id"] == first["event_id"]
        admin.call("POST", "/api/webhooks/process")
        assert json.loads(rx.calls[-1][1])["id"] == first["event_id"]

        # paused and removed webhooks get nothing; the log stays
        admin.call("PATCH", f"/api/webhooks/{hook['id']}", {"active": False})
        n = len(rx.calls)
        eng.call("POST", "/api/items", {"part_number": "WH-100-003", "description": "SHAFT"})
        time.sleep(0.5)
        admin.call("POST", "/api/webhooks/process")
        assert len(rx.calls) == n
        admin.call("DELETE", f"/api/webhooks/{hook['id']}")
        assert admin.call("GET", "/api/webhooks")["webhooks"] == []
        assert admin.call("GET", f"/api/webhooks/{hook['id']}/deliveries")["deliveries"]
    finally:
        rx.close()


def test_live_stream_tells_open_screens(kosha):
    admin = kosha.client()
    eng = kosha.add_user("live-eng", "engineer")
    Client(kosha.base).call("GET", "/api/events/stream", expect=401)
    got: list[str] = []
    resp = urllib.request.urlopen(f"{kosha.base}/api/events/stream?access_token={admin.token}", timeout=15)
    assert resp.headers["Content-Type"].startswith("text/event-stream")

    def read():
        try:
            for raw in resp:
                got.append(raw.decode())
        except Exception:
            pass

    threading.Thread(target=read, daemon=True).start()
    _wait(lambda: any(line.startswith("retry:") for line in got))
    eng.call("POST", "/api/items", {"part_number": "LV-100-001", "description": "PIN"})
    _wait(lambda: any(line.startswith("data:") for line in got))
    data = json.loads(next(line for line in got if line.startswith("data:"))[5:])
    assert data["event"] == "item.created" and data["item_id"] and "part_number" not in data   # ids only
    assert "event: item.created\n" in got
    resp.close()
    assert "item.created" in admin.call("GET", "/api/events")["events"]
