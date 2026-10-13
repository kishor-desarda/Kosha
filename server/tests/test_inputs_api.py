# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inputs library and projects end to end, through the HTTP API of a real server: uploads (one input per file
or grouped), background text reading, search, restricted access (lists, search, files, counts), duplicates,
bulk actions, and the demo data."""
import time
import urllib.parse
import uuid

import pytest


def multipart(fields: dict, files: list[tuple[str, bytes]]) -> tuple[bytes, dict]:
    """Several files under the same field name ("file"), which the harness helper does not do."""
    boundary = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for name, content in files:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n').encode() + content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def send(client, path, fields, files, expect=200):
    body, headers = multipart(fields, files)
    return client.call("POST", path, raw_body=body, headers=headers, expect=expect)


def wait_read(client, input_ids, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        files = [f for i in input_ids for f in client.call("GET", f"/api/inputs/{i}")["files"]]
        if all(f["status"] not in ("pending", "running") for f in files):
            return files
        time.sleep(0.3)
    raise AssertionError("text reading did not finish")


def q(client, **params):
    return client.call("GET", "/api/inputs?" + urllib.parse.urlencode(params))


@pytest.fixture(scope="module")
def people(kosha):
    admin = kosha.client()
    return {"admin": admin, "eng": kosha.add_user("erin", "engineer"), "eng2": kosha.add_user("emil", "engineer"),
            "view": kosha.add_user("vera", "viewer"), "appr": kosha.add_user("arno", "approver")}


def test_upload_search_and_download(people):
    eng, view = people["eng"], people["view"]
    item = eng.call("POST", "/api/items", {"part_number": "CV-200-010", "description": "CONVEYOR SCREW FLIGHT"})["item"]
    prj = eng.call("POST", "/api/projects", {"code": "PRJ-2026-021", "name": "Cement conveyor",
                                             "customer": "Customer B", "start_date": "2026-05-01"})["project"]
    eng.call("POST", "/api/projects", {"code": "prj-2026-021"}, expect=409)              # codes are unique
    # two files, each its own input; linked to the part and the project
    r = send(eng, "/api/inputs", {"kind": "test_report", "source": "Test bench 2", "tags": '["wear", "flight"]',
                                  "item_ids": f"[{item['id']}]", "project_ids": f"[{prj['id']}]"},
             [("Wear test 2026.txt", b"Flight wear after 500 h: 1.8 mm on the Hardox flights."),
              ("Bench log.csv", b"hour,wear_mm\n500,1.8\n")])
    assert r["errors"] == [] and sorted(i["title"] for i in r["inputs"]) == ["Bench log", "Wear test 2026"]
    ids = [i["id"] for i in r["inputs"]]
    files = wait_read(eng, ids)
    assert {f["status"] for f in files} == {"done"}
    hit = q(eng, q="hardox")
    assert [i["title"] for i in hit["inputs"]] == ["Wear test 2026"]
    assert [s for s, m in hit["inputs"][0]["snippet"] if m] == ["Hardox"]
    assert q(eng, q="CV-200-010")["total"] == 2 and q(eng, q="PRJ-2026-021")["total"] == 2
    assert q(eng, tag="wear", type="spreadsheet")["total"] == 1
    # a viewer can find and download, but not add
    wear = hit["inputs"][0]["id"]
    data, _ = view.call("GET", f"/api/inputs/{wear}/files/1", raw=True)
    assert data.startswith(b"Flight wear")
    text, ctype = view.call("GET", f"/api/inputs/{wear}/files/1/text", raw=True)
    assert b"Hardox" in text and ctype.startswith("text/plain")
    send(view, "/api/inputs", {}, [("x.txt", b"x")], expect=403)
    # part page and project page see the inputs
    assert people["view"].call("GET", f"/api/items/{item['id']}/inputs")["total"] == 2
    assert view.call("GET", f"/api/projects/{prj['id']}")["project"]["input_count"] == 2


def test_grouped_upload_duplicates_and_files(people):
    eng, eng2 = people["eng"], people["eng2"]
    photo = b"\x89PNG\r\n\x1a\n" + b"not really a png, but stored safely" * 10
    r = send(eng, "/api/inputs", {"group": "1", "title": "Competitor D gearbox teardown", "kind": "competitor_benchmark"},
             [("summary.txt", b"Competitor D helical gearbox, ratio 14:1"), ("front.png", photo), ("side.png", photo + b"2")])
    assert len(r["inputs"]) == 1 and r["inputs"][0]["file_count"] == 3
    gid = r["inputs"][0]["id"]
    # the same summary again: stored once, and the uploader is told where it already is
    r = send(eng2, "/api/inputs", {}, [("summary copy.txt", b"Competitor D helical gearbox, ratio 14:1")])
    assert r["duplicates"][0]["inputs"][0]["id"] == gid and not r["duplicates"][0]["skipped"]
    r = send(eng2, "/api/inputs", {"skip_duplicates": "1"}, [("again.txt", b"Competitor D helical gearbox, ratio 14:1")])
    assert r["inputs"] == [] and r["duplicates"][0]["skipped"]
    # add a file to the group, remove one, put it back
    d = send(eng2, f"/api/inputs/{gid}/files", {}, [("notes.txt", b"teardown notes: bearing 6310")])
    assert [f["n"] for f in d["files"]] == [1, 2, 3, 4]
    eng2.call("DELETE", f"/api/inputs/{gid}/files/2")
    assert [f["n"] for f in eng2.call("GET", f"/api/inputs/{gid}")["files"]] == [1, 3, 4]
    eng2.call("GET", f"/api/inputs/{gid}/files/2", expect=404)                        # removed: hidden from others
    eng.call("GET", f"/api/inputs/{gid}/files/2", raw=True)                           # the creator still has it
    eng2.call("POST", f"/api/inputs/{gid}/files/2/restore", expect=403)
    eng.call("POST", f"/api/inputs/{gid}/files/2/restore")
    wait_read(eng, [gid])
    assert q(eng, q="6310")["inputs"][0]["id"] == gid
    # empty files are refused, the others still added
    r = send(eng, "/api/inputs", {}, [("empty.txt", b""), ("fine.txt", b"fine")])
    assert len(r["inputs"]) == 1 and "empty" in r["errors"][0]["error"]


def test_restricted_inputs_stay_hidden_everywhere(people):
    eng, eng2, view, appr, admin = (people[k] for k in ("eng", "eng2", "view", "appr", "admin"))
    prj = eng.call("POST", "/api/projects", {"code": "PRJ-SECRET", "name": "Pricing"})["project"]
    me = appr.call("GET", "/api/auth/me")["user"]
    r = send(eng, "/api/inputs", {"group": "1", "title": "Customer B target price", "kind": "customer_requirement",
                                  "confidentiality": "restricted", "access_users": f"[{me['id']}]",
                                  "tags": '["confidential-price"]', "project_ids": f"[{prj['id']}]"},
             [("price.txt", b"Target price 38500 per conveyor"), ("photo.jpg", b"\xff\xd8\xff\xe0 fake jpeg")])
    sid = r["inputs"][0]["id"]
    wait_read(eng, [sid])
    for who, sees in ((eng, True), (appr, True), (admin, True), (eng2, False), (view, False)):
        assert (q(who, q="38500")["total"] == 1) is sees
        assert any(i["id"] == sid for i in q(who, limit=500)["inputs"]) is sees
        assert any(t["key"] == "confidential-price" for t in who.call("GET", "/api/tags?q=confidential")["tags"]) is sees
        assert any(f["key"] == "customer_requirement" and f["n"] for f in q(who, project=prj["id"])["facets"]["kind"]) is sees
        assert who.call("GET", f"/api/projects/{prj['id']}")["project"]["input_count"] == (1 if sees else 0)
        listed = {p["id"]: p["input_count"] for p in who.call("GET", "/api/projects?status=open")["projects"]}
        assert listed[prj["id"]] == (1 if sees else 0)
        code = 200 if sees else 404
        who.call("GET", f"/api/inputs/{sid}", expect=code)
        who.call("GET", f"/api/inputs/{sid}/files/1", expect=code, raw=True)
        who.call("GET", f"/api/inputs/{sid}/files/1/text", expect=code, raw=True)
        who.call("GET", f"/api/inputs/{sid}/files/2/thumb", expect=code, raw=True)
    # people without access can't change it either, and get the same "not found"
    eng2.call("PATCH", f"/api/inputs/{sid}", {"title": "mine now"}, expect=404)
    eng2.call("POST", "/api/inputs/bulk", {"ids": [sid], "action": "add_tags", "tags": ["x"]})
    assert "x" not in eng.call("GET", f"/api/inputs/{sid}")["tags"]
    send(eng2, f"/api/inputs/{sid}/files", {}, [("sneak.txt", b"x")], expect=404)
    # a duplicate of the restricted file does not reveal where it is
    r = send(eng2, "/api/inputs", {}, [("price copy.txt", b"Target price 38500 per conveyor")])
    assert r["duplicates"] == [] and len(r["inputs"]) == 1


def test_edit_link_bulk_delete_and_restore(people):
    eng, eng2, view, admin = people["eng"], people["eng2"], people["view"], people["admin"]
    note = eng.call("POST", "/api/inputs", {"title": "Phone call with Customer B", "note": "They want stainless flights.",
                                            "kind": "meeting_note"})["inputs"][0]
    link = eng.call("POST", "/api/inputs", {"url": "https://www.example.com/abrasion", "kind": "standard",
                                            "tags": ["abrasion"]})["inputs"][0]
    eng.call("POST", "/api/inputs", {"title": "Bad", "url": "file:///etc/passwd"}, expect=400)
    item = eng.call("POST", "/api/items", {"part_number": "CV-200-020", "description": "TROUGH LINER"})["item"]
    d = eng2.call("PATCH", f"/api/inputs/{note['id']}", {"title": "Call with Customer B", "tags": ["stainless"],
                                                          "date": "2026-06-02"})
    assert (d["title"], d["tags"], d["date"]) == ("Call with Customer B", ["stainless"], "2026-06-02")
    d = eng.call("POST", f"/api/inputs/{note['id']}/link", {"item_ids": [item["id"]]})
    assert [i["part_number"] for i in d["items"]] == ["CV-200-020"]
    assert eng.call("POST", f"/api/inputs/{note['id']}/unlink", {"item_ids": [item["id"]]})["items"] == []
    r = eng.call("POST", "/api/inputs/bulk", {"ids": [note["id"], link["id"]], "action": "link", "item_ids": [item["id"]]})
    assert r["done"] == 2
    assert q(view, item=item["id"])["total"] == 2
    view.call("POST", "/api/inputs/bulk", {"ids": [note["id"]], "action": "delete"}, expect=403)
    r = eng2.call("POST", "/api/inputs/bulk", {"ids": [note["id"], link["id"]], "action": "delete"})
    assert r["done"] == 0                                                          # eng2 added neither of them
    eng.call("DELETE", f"/api/inputs/{note['id']}")
    view.call("GET", f"/api/inputs/{note['id']}", expect=404)
    assert q(eng, deleted="1", q="stainless")["total"] == 1
    eng.call("POST", f"/api/inputs/{note['id']}/restore")
    assert q(view, q="stainless")["total"] == 1
    # settings: everyone reads the kinds, only admins change them
    s = view.call("GET", "/api/inputs/settings")
    assert any(k["key"] == "photo" for k in s["kinds"]) and "ocr_available" in s
    view.call("PUT", "/api/inputs/settings", {"ocr": False}, expect=403)
    s = admin.call("PUT", "/api/inputs/settings", {"kinds": s["kinds"] + [{"label": "Site survey"}]})
    assert s["kinds"][-1]["key"] == "site_survey"
    assert admin.call("POST", "/api/inputs/reindex", {})["inputs"] >= 2


def test_demo_inputs_and_projects(demo_kosha):
    tom, daniel = demo_kosha.client("tom"), demo_kosha.client("daniel")
    titles = {i["title"] for i in q(tom, limit=100)["inputs"]}
    assert "Agitator benchmark: Competitor A and Competitor B" in titles
    assert "Customer A target price" not in titles                              # restricted to approvers
    assert "Customer A target price" in {i["title"] for i in q(daniel, limit=100)["inputs"]}
    bench = next(i for i in q(tom, q="benchmark")["inputs"] if i["kind"] == "competitor_benchmark")
    wait_read(tom, [bench["id"]])
    assert q(tom, q="42CrMo4")["inputs"][0]["id"] == bench["id"]               # found inside the spreadsheet
    projects = {p["code"]: p for p in tom.call("GET", "/api/projects?status=")["projects"]}
    assert projects["PRJ-2026-014"]["status"] == "open" and projects["PRJ-2025-031"]["status"] == "closed"
    assert projects["PRJ-2026-014"]["input_count"] == 3                         # the restricted one is not counted
    assert daniel.call("GET", f"/api/projects/{projects['PRJ-2026-014']['id']}")["project"]["input_count"] == 4
