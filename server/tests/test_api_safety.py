# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: the API rules for programs (specification 13.1): /api/v1, request IDs, one error format, no lost
updates (If-Match), safe retries (Idempotency-Key), cursor paging and the per-account limit."""
import json
import urllib.error
import urllib.request


def _raw(kosha, client, method, path, body=None, headers=None):
    """(status, headers, JSON) of one call, for the checks that need the answer's headers."""
    hdrs = {"Authorization": f"Bearer {client.token}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(kosha.base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, json.loads(e.read() or b"null")


def test_versioned_path_request_ids_and_one_error_format(kosha):
    admin = kosha.client()
    assert admin.call("GET", "/api/v1/auth/me")["user"]["username"] == "admin"
    assert "/api/items" in admin.call("GET", "/api/v1/openapi.json")["paths"]
    status, headers, _ = _raw(kosha, admin, "GET", "/api/v1/items", headers={"X-Request-ID": "job-42-attempt-1"})
    assert status == 200 and headers["X-Request-ID"] == "job-42-attempt-1"
    status, headers, err = _raw(kosha, admin, "GET", "/api/items/999999")
    assert status == 404 and err["code"] == "not_found" and err["request_id"] == headers["X-Request-ID"]
    status, headers, err = _raw(kosha, admin, "GET", "/api/v1/no-such-thing", headers={"X-Request-ID": "bad id!"})
    assert status == 404 and err["request_id"] == headers["X-Request-ID"] != "bad id!"
    status, _, err = _raw(kosha, admin, "DELETE", "/api/v1/auth/me")
    assert status == 405 and err["code"] == "method_not_allowed" and err["request_id"]
    err = admin.call("POST", "/api/v1/items", {}, expect=400)
    assert set(err) >= {"error", "code", "request_id"}


def test_no_lost_updates(kosha):
    admin = kosha.client()
    eng = kosha.add_user("safe-eng", "engineer", full_name="Rahul Rao")
    item = eng.call("POST", "/api/items", {"part_number": "SAFE-100-001", "description": "PLATE"})["item"]
    assert item["row_version"] == 1
    # both read version 1; Rahul saves first, the other update is refused and says who changed it
    status, _, out = _raw(kosha, eng, "PATCH", f"/api/v1/items/{item['id']}", {"description": "PLATE 10"},
                          {"If-Match": '"1"'})
    assert status == 200 and out["item"]["row_version"] == 2 and out["item"]["updated_by"]
    status, _, err = _raw(kosha, admin, "PATCH", f"/api/items/{item['id']}", {"description": "PLATE 12"},
                          {"If-Match": '"1"'})
    assert status == 412 and err["code"] == "changed_meanwhile" and err["row_version"] == 2
    assert err["error"].startswith("Rahul Rao changed SAFE-100-001 at ") and "not saved" in err["error"]
    assert admin.call("GET", f"/api/items/{item['id']}")["item"]["description"] == "PLATE 10"
    status, _, out = _raw(kosha, admin, "PATCH", f"/api/items/{item['id']}", {"description": "PLATE 12"},
                          {"If-Match": 'W/"2"'})
    assert status == 200 and out["item"]["row_version"] == 3
    status, _, err = _raw(kosha, admin, "PATCH", f"/api/items/{item['id']}", {"description": "x"},
                          {"If-Match": "yesterday"})
    assert status == 400 and err["code"] == "bad_if_match"
    # without If-Match the update goes through as before (the browser's own forms)
    admin.call("PATCH", f"/api/items/{item['id']}", {"description": "PLATE 14"})

    # a change order and its version
    co = eng.call("POST", "/api/changes", {"title": "Thicker plate"})
    status, _, out = _raw(kosha, eng, "PATCH", f"/api/changes/{co['id']}", {"reason": "Bends"}, {"If-Match": "1"})
    assert status == 200 and out["row_version"] == 2
    status, _, err = _raw(kosha, eng, "PATCH", f"/api/changes/{co['id']}", {"reason": "x"}, {"If-Match": "1"})
    assert status == 412 and co["number"] in err["error"]


def test_safe_retries_with_an_idempotency_key(kosha):
    eng = kosha.add_user("idem-eng", "engineer")
    other = kosha.add_user("idem-other", "engineer")
    body = {"part_number": "IDEM-100-001", "description": "SPACER"}
    s1, _, first = _raw(kosha, eng, "POST", "/api/v1/items", body, {"Idempotency-Key": "net-drop-0001"})
    s2, h2, again = _raw(kosha, eng, "POST", "/api/v1/items", body, {"Idempotency-Key": "net-drop-0001"})
    assert s1 == s2 == 200 and first == again and h2["Idempotent-Replayed"] == "true"
    assert len(eng.call("GET", "/api/items?q=IDEM-100-001")["items"]) == 1
    # the same key for a different request is refused
    status, _, err = _raw(kosha, eng, "POST", "/api/v1/items", {**body, "description": "WASHER"},
                          {"Idempotency-Key": "net-drop-0001"})
    assert status == 422 and err["code"] == "idempotency_key_reused"
    # keys belong to one account: someone else's same key is a new request
    status, _, err = _raw(kosha, other, "POST", "/api/v1/items", body, {"Idempotency-Key": "net-drop-0001"})
    assert status == 409 and "already exists" in err["error"]
    # a refused request does not keep the key, so the caller can fix it and try again with the same key
    status, _, _ = _raw(kosha, other, "POST", "/api/items", {"part_number": ""}, {"Idempotency-Key": "fix-and-retry"})
    assert status == 400
    status, _, out = _raw(kosha, other, "POST", "/api/items", {"part_number": "IDEM-100-002"},
                          {"Idempotency-Key": "fix-and-retry"})
    assert status == 200 and out["item"]["part_number"] == "IDEM-100-002"


def test_cursor_paging(kosha):
    eng = kosha.add_user("page-eng", "engineer")
    made = {eng.call("POST", "/api/items", {"part_number": f"PG-100-{n:03d}"})["item"]["id"] for n in range(7)}
    seen, cursor, pages = [], "", 0
    while cursor is not None:
        r = eng.call("GET", f"/api/v1/items?category=PG&limit=3&cursor={cursor}")
        seen += [i["id"] for i in r["items"]]
        cursor, pages = r["next_cursor"], pages + 1
    assert set(seen) == made and len(seen) == 7 and pages == 3 and seen == sorted(seen, reverse=True)
    assert "not valid" in eng.call("GET", "/api/items?cursor=garbage!", expect=400)["error"]
    admin = kosha.client()
    r = admin.call("GET", "/api/audit?limit=2&cursor=")
    assert len(r["entries"]) == 2 and r["next_cursor"]
    nxt = admin.call("GET", f"/api/audit?limit=2&cursor={r['next_cursor']}")
    assert nxt["entries"][0]["id"] < r["entries"][-1]["id"]
    assert "next_cursor" in admin.call("GET", "/api/changes?cursor=")


def test_rate_limit_per_account(kosha):
    admin = kosha.client()
    busy = kosha.add_user("rate-script", "viewer")
    assert "at least 60" in admin.call("PUT", "/api/settings", {"api_rate_limit_per_minute": 5}, expect=400)["error"]
    admin.call("PUT", "/api/settings", {"api_rate_limit_per_minute": 60})
    try:
        statuses = []
        for _ in range(130):        # a minute boundary can reset the count once
            status, headers, out = _raw(kosha, busy, "GET", "/api/auth/me")
            statuses.append(status)
            if status == 429:
                break
        assert statuses[-1] == 429 and out["code"] == "rate_limited" and int(headers["Retry-After"]) >= 1
        assert "nothing was changed" in out["error"]
        assert admin.call("GET", "/api/auth/me")["user"]["username"] == "admin"      # other accounts carry on
    finally:
        admin.call("PUT", "/api/settings", {"api_rate_limit_per_minute": 0})
    assert busy.call("GET", "/api/auth/me")
