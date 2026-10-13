# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: change requests (ECR) into change orders, the interchangeability questions, effectivity, approval
routes (parallel and one after another), stand-ins for leave, reminders, and decisions kept for good
(specification 7.4 to 7.6)."""
from datetime import date, timedelta

from kosha.cad import dxfwriter as W


def _dxf(pn, desc, material="S235"):
    return W.flange(values={"PART_NO": pn, "DESCRIPTION": desc, "MATERIAL": material})


def _put(c, folder, name, data):
    r = c.call("POST", "/api/documents/upload", files={"file": (name, data)}, fields={"folder_id": folder})
    assert not r["errors"], r
    return r["documents"][0]


def _folder(admin, name):
    root = admin.call("GET", "/api/folders")["folders"][0]["id"]
    return admin.call("POST", "/api/folders", {"parent_id": root, "name": name})["folder"]["id"]


def _uid(client):
    return client.call("GET", "/api/auth/me")["user"]["id"]


def test_change_request_into_change_order(kosha):
    admin = kosha.client()
    eng = kosha.add_user("cr-eng", "engineer")
    viewer = kosha.add_user("cr-view", "viewer")
    appr = kosha.add_user("cr-appr", "approver")
    f = _folder(admin, "Requests")
    doc = _put(eng, f, "CR-100-001 Housing.dxf", _dxf("CR-100-001", "HOUSING"))
    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released"})

    # anyone who can see the part raises a request, with a reason
    assert "why" in viewer.call("POST", "/api/change-requests", {"title": "Bigger bore", "document_id": doc["id"]},
                                expect=400)["error"]
    cr = viewer.call("POST", "/api/change-requests", {"title": "Bigger bore", "reason": "Bearings fail on site",
                                                      "document_id": doc["id"]})
    assert cr["number"].startswith("ECR-") and cr["state"] == "open" and cr["item"]["part_number"] == "CR-100-001"
    assert not cr["actions"]["convert"]["ok"] and "engineer" in cr["actions"]["convert"]["why"]
    listed = eng.call("GET", "/api/change-requests?state=open")["requests"]
    assert [r["number"] for r in listed] == [cr["number"]] and listed[0]["part_number"] == "CR-100-001"
    # the discussion stays with it
    eng.call("POST", "/api/discussions", {"entity_type": "change_request", "entity_id": cr["id"],
                                          "body": "Is 90 mm enough?"})

    # an engineer turns it into a change order; the file comes along
    r = eng.call("POST", f"/api/change-requests/{cr['id']}/convert", {"approver_ids": [_uid(appr)]})
    assert r["state"] == "accepted" and r["change_order"]["number"].startswith("ECO-")
    co = eng.call("GET", f"/api/changes/{r['change_id']}")
    assert co["ecr"]["number"] == cr["number"] and [i["document_id"] for i in co["items"]] == [doc["id"]]
    assert "closed" in eng.call("POST", f"/api/change-requests/{cr['id']}/convert", {}, expect=409)["error"]

    # declined needs a reason; withdrawn by the person who raised it
    cr2 = viewer.call("POST", "/api/change-requests", {"title": "Paint it blue", "reason": "Customer asked"})
    assert "why" in appr.call("POST", f"/api/change-requests/{cr2['id']}/decline", {}, expect=400)["error"]
    assert appr.call("POST", f"/api/change-requests/{cr2['id']}/decline",
                     {"note": "Colour is set per order, not on the drawing"})["state"] == "declined"
    cr3 = viewer.call("POST", "/api/change-requests", {"title": "Thicker plate", "reason": "Flexes"})
    assert viewer.call("POST", f"/api/change-requests/{cr3['id']}/withdraw")["state"] == "withdrawn"
    other = kosha.add_user("cr-view2", "viewer")
    cr4 = viewer.call("POST", "/api/change-requests", {"title": "Add a lifting lug", "reason": "Hard to lift"})
    other.call("POST", f"/api/change-requests/{cr4['id']}/withdraw", expect=403)


def test_interchangeability_and_effectivity(kosha):
    admin = kosha.client()
    eng = kosha.add_user("ic-eng", "engineer")
    appr = kosha.add_user("ic-appr", "approver")
    f = _folder(admin, "Interchange")
    doc = _put(eng, f, "IC-100-001 Shaft.dxf", _dxf("IC-100-001", "SHAFT", "42CrMo4"))
    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released"})
    co = eng.call("POST", "/api/changes", {"title": "Longer keyway", "document_ids": [doc["id"]],
                                           "approver_ids": [_uid(appr)]})
    eng.call("POST", f"/api/changes/{co['id']}/revise")
    err = eng.call("POST", f"/api/changes/{co['id']}/submit", expect=409)
    assert any("interchangeability" in p for p in err["problems"])
    assert co["interchangeability"]["questions"][0]["text"].startswith("Will the new part fit everywhere")

    # one "no": the part needs a new number, not a new revision
    page = eng.call("PATCH", f"/api/changes/{co['id']}", {"ic_fit": "yes", "ic_mix": "no", "ic_function": "yes"})
    assert page["interchangeability"]["result"] == "new" and page["interchangeability"]["needed"]
    err = eng.call("POST", f"/api/changes/{co['id']}/submit", expect=409)
    assert any("needs a new part number" in p for p in err["problems"])
    assert "yes or no" in eng.call("PATCH", f"/api/changes/{co['id']}", {"ic_mix": "maybe"}, expect=400)["error"]

    # effectivity: a date needs the date, a serial needs the serial
    eng.call("PATCH", f"/api/changes/{co['id']}", {"ic_mix": "yes", "effectivity": "date"})
    err = eng.call("POST", f"/api/changes/{co['id']}/submit", expect=409)
    assert err["problems"] == ["say from which date the change takes effect"]
    assert "a date" in eng.call("PATCH", f"/api/changes/{co['id']}", {"effective_date": "next week"}, expect=400)["error"]
    page = eng.call("PATCH", f"/api/changes/{co['id']}", {"effectivity": "serial", "effective_serial": "mx26-0042"})
    assert page["effectivity_text"] == "From machine MX26-0042 onwards"
    assert page["interchangeability"]["result"] == "same"
    eng.call("POST", f"/api/changes/{co['id']}/submit")
    appr.call("POST", f"/api/changes/{co['id']}/decide", {"decision": "approved"})
    done = appr.call("POST", f"/api/changes/{co['id']}/implement")
    assert done["state"] == "implemented"

    # immediately: says why it cannot wait, and the open work orders are flagged
    co2 = eng.call("POST", "/api/changes", {"title": "Crack at the shoulder", "document_ids": [doc["id"]],
                                            "approver_ids": [_uid(appr)], "effectivity": "immediate",
                                            "ic_fit": "yes", "ic_mix": "yes", "ic_function": "yes"})
    eng.call("POST", f"/api/changes/{co2['id']}/revise")
    err = eng.call("POST", f"/api/changes/{co2['id']}/submit", expect=409)
    assert any("why it cannot wait" in p for p in err["problems"])
    eng.call("PATCH", f"/api/changes/{co2['id']}", {"reason": "Shaft cracked on two machines"})
    eng.call("POST", f"/api/changes/{co2['id']}/submit")
    appr.call("POST", f"/api/changes/{co2['id']}/decide", {"decision": "approved"})
    done = appr.call("POST", f"/api/changes/{co2['id']}/implement")
    assert any("open work orders" in x for x in done["done"])


def test_routes_delegates_reminders_and_kept_decisions(kosha):
    admin = kosha.client()
    eng = kosha.add_user("rt-eng", "engineer")
    lead = kosha.add_user("rt-lead", "approver", full_name="Lena Lead")
    quality = kosha.add_user("rt-qual", "engineer", full_name="Quinn Quality")
    normal = kosha.add_user("rt-norm", "approver", full_name="Nora Normal")
    stand_in = kosha.add_user("rt-stand", "approver", full_name="Sam Standin")
    qid = _uid(quality)
    f = _folder(admin, "Routes")

    # quality may approve once they hold the extra role
    assert "cannot approve" in admin.call("POST", "/api/approval-routes", {
        "name": "Rotating parts", "scope": "category", "value": "rot", "mode": "sequential",
        "approver_ids": [_uid(lead), qid]}, expect=400)["error"]
    admin.call("PATCH", f"/api/users/{qid}", {"roles": ["quality"]})
    rot = admin.call("POST", "/api/approval-routes", {
        "name": "Rotating parts", "scope": "category", "value": "rot", "mode": "sequential",
        "approver_ids": [_uid(lead), qid], "lead_id": _uid(lead)})["route"]
    assert rot["value"] == "ROT" and [a["name"] for a in rot["approvers"]] == ["Lena Lead", "Quinn Quality"]
    admin.call("POST", "/api/approval-routes", {"name": "Normal parts", "scope": "default",
                                                "approver_ids": [_uid(normal)]})
    assert "already a route" in admin.call("POST", "/api/approval-routes", {
        "name": "Again", "scope": "default", "approver_ids": [_uid(normal)]}, expect=400)["error"]
    eng.call("POST", "/api/approval-routes", {"name": "x", "approver_ids": [_uid(normal)]}, expect=403)

    # a normal part takes the default route
    plain = _put(eng, f, "RT-100-001 Cover.dxf", _dxf("RT-100-001", "COVER"))
    co = eng.call("POST", "/api/changes", {"title": "Release cover", "document_ids": [plain["id"]]})
    assert [a["name"] for a in co["approvers"]] == ["Nora Normal"]

    # a rotating part: the lead first, then quality
    rotor = _put(eng, f, "ROT-000101 Rotor.dxf", _dxf("ROT-000101", "ROTOR", "42CrMo4"))
    co = eng.call("POST", "/api/changes", {"title": "Release rotor", "document_ids": [rotor["id"]]})
    assert [(a["name"], a["step"]) for a in co["approvers"]] == [("Lena Lead", 1), ("Quinn Quality", 2)]
    assert co["route_mode"] == "sequential" and co["lead_name"] == "Lena Lead"
    co = eng.call("POST", f"/api/changes/{co['id']}/submit")
    assert [a["decision"] for a in co["approvers"]] == ["pending", "waiting"]
    assert "not your turn" in quality.call("POST", f"/api/changes/{co['id']}/decide", {"decision": "approved"},
                                           expect=409)["error"]
    assert not quality.call("GET", "/api/dashboard")["approvals"]

    # the lead is on leave: their stand-in decides in their name
    assert "has passed" in lead.call("PUT", "/api/me/delegate", {"user_id": _uid(stand_in), "until": "2020-01-01"},
                                     expect=400)["error"]
    until = (date.today() + timedelta(days=7)).isoformat()
    me = lead.call("PUT", "/api/me/delegate", {"user_id": _uid(stand_in), "until": until})
    assert me["delegate"]["name"] == "Sam Standin" and me["delegate"]["active"]
    waiting = stand_in.call("GET", "/api/dashboard")["approvals"]
    assert [(a["number"], a["for_name"]) for a in waiting] == [(co["number"], "Lena Lead")]
    page = stand_in.call("GET", f"/api/changes/{co['id']}")
    assert page["my_decision"]["for_name"] == "Lena Lead"
    page = stand_in.call("POST", f"/api/changes/{co['id']}/decide", {"decision": "rejected",
                                                                     "comment": "Balance grade missing"})
    assert page["state"] == "rejected"
    assert page["decisions"][0]["name"] == "Sam Standin" and page["decisions"][0]["for_name"] == "Lena Lead"
    assert "ROT-000101" in page["decisions"][0]["revisions"]

    # submitted again: a new round; the first decision is still there
    eng.call("POST", f"/api/changes/{co['id']}/submit")
    lead.call("PUT", "/api/me/delegate", {"user_id": None})
    lead.call("POST", f"/api/changes/{co['id']}/decide", {"decision": "approved"})
    assert [a["number"] for a in quality.call("GET", "/api/dashboard")["approvals"]] == [co["number"]]

    # waiting too long: a reminder after two working days, the lead is told after four
    with kosha.db() as con:
        con.execute("UPDATE co_approvers SET asked_at = ? WHERE co_id = ? AND user_id = ?",
                    ((date.today() - timedelta(days=10)).isoformat() + "T08:00:00+00:00", co["id"], qid))
    assert admin.call("POST", "/api/changes/reminders") == {"reminded": 1, "escalated": 1}
    assert admin.call("POST", "/api/changes/reminders") == {"reminded": 0, "escalated": 0}       # once only
    notes = quality.call("GET", "/api/notifications")["notifications"]
    assert any(n["title"].startswith(f"Reminder: {co['number']}") for n in notes)
    notes = lead.call("GET", "/api/notifications")["notifications"]
    assert any("Quinn Quality" in n["title"] for n in notes)

    page = quality.call("POST", f"/api/changes/{co['id']}/decide", {"decision": "approved", "comment": "OK"})
    assert page["state"] == "approved"
    assert [(d["round"], d["decision"]) for d in page["decisions"]] == [(1, "rejected"), (2, "approved"),
                                                                        (2, "approved")]
    # there is no way to change or remove a decision
    for method in ("PATCH", "PUT", "DELETE"):
        admin.call(method, f"/api/changes/{co['id']}/decisions/1", {}, expect=404)


def test_working_days():
    from datetime import datetime, timezone

    from kosha.approval import working_days
    fri = datetime(2026, 10, 9, 17, 0, tzinfo=timezone.utc)
    assert working_days(fri, datetime(2026, 10, 12, 9, tzinfo=timezone.utc)) == 1       # Monday
    assert working_days(fri, datetime(2026, 10, 13, 9, tzinfo=timezone.utc)) == 2       # Tuesday: remind
    assert working_days(fri, datetime(2026, 10, 15, 9, tzinfo=timezone.utc)) == 4       # Thursday: tell the lead
    assert working_days(fri, datetime(2026, 10, 11, 9, tzinfo=timezone.utc)) == 0       # weekend
