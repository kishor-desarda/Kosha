# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: stacking roles, access rules on folders, projects and categories, view-only people, vendor accounts
with projects and an end date, service accounts and the access review (specification sections 2 and 15.2)."""
from datetime import date, timedelta

from harness import Client


def _folder(c, name):
    return next(f["id"] for f in c.call("GET", "/api/folders")["folders"] if f["name"] == name)


def _user(c, username):
    return next(u for u in c.call("GET", "/api/users")["users"] if u["username"] == username)


def _files(c, q):
    return {d["id"] for d in c.call("GET", f"/api/search?q={q}")["documents"]}


def test_roles_stack_and_extra_roles(demo_kosha):
    admin = demo_kosha.client()
    chk = demo_kosha.add_user("chandra", "checker")
    me = chk.call("GET", "/api/auth/me")["user"]
    assert me["role"] == "checker" and me["roles"] == []
    # a checker does what an engineer does, not what an approver does
    item = chk.call("POST", "/api/items", {"part_number": "SHF-000900", "description": "Test shaft"})["item"]
    chk.call("GET", "/api/audit", expect=403)
    assert "Approver role or higher" in chk.call("POST", "/api/boms/1/release", expect=403)["error"]

    roles = {r["id"]: r for r in chk.call("GET", "/api/access")["roles"]}
    assert list(roles)[:5] == ["viewer", "engineer", "checker", "approver", "admin"]
    assert roles["data_steward"]["main"] is False and chk.call("GET", "/api/access")["rules"] == []

    uid = me["id"]
    assert "Extra roles are" in admin.call("PATCH", f"/api/users/{uid}", {"roles": ["boss"]}, expect=400)["error"]
    err = chk.call("POST", f"/api/items/{item['id']}/allow-reuse", {"parent_item_id": 1, "reason": "x" * 10},
                   expect=403)["error"]
    assert "needs the Data steward role" in err
    u = admin.call("PATCH", f"/api/users/{uid}", {"roles": ["data_steward", "spares"]})["user"]
    assert u["roles"] == ["data_steward", "spares"]
    assert chk.call("GET", "/api/auth/me")["user"]["roles"] == ["data_steward", "spares"]   # at once, no new sign-in
    # the steward may now decide (the part is not locked, but the decision is recorded all the same)
    assert "Allowed in" in chk.call("POST", f"/api/items/{item['id']}/allow-reuse",
                                    {"parent_item_id": 1, "reason": "Same customer family"})["message"]
    # an admin cannot take away their own admin access
    admin_id = _user(admin, "admin")["id"]
    assert "own admin access" in admin.call("PATCH", f"/api/users/{admin_id}", {"view_only": True}, expect=400)["error"]


def test_rules_hide_folders_projects_and_categories(demo_kosha):
    admin = demo_kosha.client()
    asha, daniel, tom = demo_kosha.client("asha"), demo_kosha.client("daniel"), demo_kosha.client("tom")
    pumps = _folder(admin, "Pumps")
    doc = admin.call("GET", f"/api/folders/{_folder(admin, 'CP-80 Centrifugal Pump')}/contents")["documents"][0]
    assert doc["id"] in _files(tom, "CP-80")

    # the pumps folder (and the CP-80 folder below it) only for daniel and the approvers
    rule = admin.call("POST", "/api/access/rules", {"scope": "folder", "value": str(pumps), "note": "customer NDA",
                                                    "members": [f"user:{_user(admin, 'asha')['id']}", "role:approver"]})
    assert rule["rule"]["label"] == "Kosha/Products/Pumps" and "Asha Menon" in rule["rule"]["member_names"]
    admin.call("POST", "/api/access/rules", {"scope": "folder", "value": str(pumps), "members": ["role:viewer"]},
               expect=409)
    assert "not a person or a role" in admin.call("POST", "/api/access/rules", {
        "scope": "folder", "value": str(_folder(admin, "Gearboxes")), "members": ["user:9999"]}, expect=400)["error"]
    asha.call("POST", "/api/access/rules", {"scope": "folder", "value": "1", "members": ["role:viewer"]}, expect=403)

    assert doc["id"] not in _files(tom, "CP-80")
    tom.call("GET", f"/api/documents/{doc['id']}", expect=404)
    tom.call("GET", f"/api/documents/{doc['id']}/preview", expect=404)
    tom.call("GET", f"/api/folders/{pumps}/contents", expect=404)
    tom.call("GET", f"/api/folders/{_folder(admin, 'CP-80 Centrifugal Pump')}/contents", expect=404)
    assert "Pumps" not in {f["name"] for f in tom.call("GET", "/api/folders")["folders"]}
    for c in (asha, daniel, admin):
        assert doc["id"] in _files(c, "CP-80")
        c.call("GET", f"/api/documents/{doc['id']}")

    # a category: shaft-sleeve parts (prefix MX) hidden from the viewer
    cat = admin.call("POST", "/api/access/rules", {"scope": "category", "value": "mx", "members": ["role:engineer"]})["rule"]
    assert cat["value"] == "MX"
    assert tom.call("GET", "/api/items?q=MX-100")["items"] == []
    assert asha.call("GET", "/api/items?q=MX-100")["items"]
    item = asha.call("GET", "/api/items?q=MX-100-020")["items"][0]
    tom.call("GET", f"/api/items/{item['id']}", expect=404)
    assert not any(d["filename"].startswith("MX-100") for d in tom.call("GET", "/api/search?q=MX-100")["documents"])
    admin.call("DELETE", f"/api/access/rules/{cat['id']}")
    assert tom.call("GET", "/api/items?q=MX-100")["items"]

    # a project: hidden, with its parts
    prj = admin.call("POST", "/api/access/rules", {"scope": "project", "value": "2", "members": ["user:%d" % _user(
        admin, "daniel")["id"]]})["rule"]
    assert prj["label"].startswith("PRJ-2025-031")
    assert {p["id"] for p in asha.call("GET", "/api/projects")["projects"]} == {1}
    asha.call("GET", "/api/projects/2", expect=404)
    assert {p["id"] for p in daniel.call("GET", "/api/projects")["projects"]} == {1, 2}
    hidden = daniel.call("GET", "/api/projects/2")["project"]["items"][0]
    asha.call("GET", f"/api/items/{hidden['id']}", expect=404)

    # the review: who sees which limited area; approvers may read it, engineers not
    rev = daniel.call("GET", "/api/access/review")
    by = {u["username"]: u for u in rev["users"]}
    assert "Folder Kosha/Products/Pumps" in by["asha"]["sees_limited"]
    assert "Folder Kosha/Products/Pumps" not in by["tom"]["sees_limited"]
    assert any(s.startswith("Project PRJ-2025-031") for s in by["daniel"]["sees_limited"])
    assert by["admin"]["check"] == "sees everything"
    asha.call("GET", "/api/access/review", expect=403)
    csv, ctype = daniel.call("GET", "/api/access/review?format=csv", raw=True)
    assert ctype.startswith("text/csv") and "Limited areas they can see" in csv.decode("utf-8-sig")

    for r in admin.call("GET", "/api/access")["rules"]:
        admin.call("DELETE", f"/api/access/rules/{r['id']}")
    assert doc["id"] in _files(tom, "CP-80")


def test_view_only_vendor_service_and_end_date(demo_kosha):
    admin = demo_kosha.client()
    tom = demo_kosha.client("tom")
    doc = next(d for d in tom.call("GET", "/api/search?q=MX-100-020")["documents"])
    tom.call("GET", f"/api/documents/{doc['id']}/file", raw=True)
    admin.call("PATCH", f"/api/users/{_user(admin, 'tom')['id']}", {"view_only": True})
    err = tom.call("GET", f"/api/documents/{doc['id']}/file", expect=403)["error"]
    assert "view-only access" in err and "preview" in err
    tom.call("GET", f"/api/documents/{doc['id']}")
    admin.call("PATCH", f"/api/users/{_user(admin, 'tom')['id']}", {"view_only": False})

    # a vendor: no project yet, so no sign-in; then only the parts and files of its project
    demo_kosha.client().call("POST", "/api/users", {"username": "vinod", "full_name": "Vinod (outside designer)",
                                                     "role": "engineer", "password": "password-123", "kind": "vendor",
                                                     "company": "Demo Design Services"})
    vid = _user(admin, "vinod")["id"]

    r = Client(demo_kosha.base).call("POST", "/api/auth/login", {"username": "vinod", "password": "password-123"},
                                     expect=403)
    assert "no project yet" in r["error"]
    admin.call("PATCH", f"/api/users/{vid}", {"projects": [1], "expires_on": (date.today() + timedelta(days=30)).isoformat()})
    vendor = demo_kosha.client("vinod", "password-123")
    me = vendor.call("GET", "/api/auth/me")["user"]
    assert me["kind"] == "vendor"
    assert [p["code"] for p in vendor.call("GET", "/api/projects")["projects"]] == ["PRJ-2026-014"]
    vendor.call("GET", "/api/projects/2", expect=404)
    allowed = {i["part_number"] for i in vendor.call("GET", "/api/items")["items"]}
    assert allowed == {"MX-100-000", "MX-100-020"}
    assert {d["part_number"] for d in vendor.call("GET", "/api/search?q=MX")["documents"]} <= allowed
    assert vendor.call("GET", "/api/search?q=CP-80")["documents"] == []
    other = tom.call("GET", "/api/search?q=CP-80")["documents"][0]
    vendor.call("GET", f"/api/documents/{other['id']}", expect=404)
    vendor.call("GET", "/api/folders", expect=403)
    assert "vendor account can only" in vendor.call("GET", "/api/dashboard", expect=403)["error"]
    vendor.call("POST", "/api/items", {"part_number": "X-1"}, expect=403)
    vendor.call("GET", "/api/users", expect=403)

    # past its end date the account stops at once, open sessions included
    admin.call("PATCH", f"/api/users/{vid}", {"expires_on": (date.today() - timedelta(days=1)).isoformat()})
    vendor.call("GET", "/api/projects", expect=401)
    r = Client(demo_kosha.base).call("POST", "/api/auth/login", {"username": "vinod", "password": "password-123"},
                                     expect=403)
    assert "This account ended on" in r["error"]
    assert "end date passed" in {u["username"]: u for u in admin.call("GET", "/api/access/review")["users"]}["vinod"]["check"]
    assert "Write the end date" in admin.call("PATCH", f"/api/users/{vid}", {"expires_on": "31/03/2027"}, expect=400)["error"]

    # a service account never signs in on a screen
    admin.call("POST", "/api/users", {"username": "erp-bot", "role": "engineer", "password": "password-123",
                                      "kind": "service"})
    r = Client(demo_kosha.base).call("POST", "/api/auth/login", {"username": "erp-bot", "password": "password-123"},
                                     expect=403)
    assert "service account" in r["error"]
