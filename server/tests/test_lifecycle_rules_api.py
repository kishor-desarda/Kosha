# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: the checks before review and release, the four-eyes rule and its small-company settings, part and
drawing revising together, superseded revisions, spares only through a change order, and the revision scheme
(specification 7.1 to 7.3)."""
from kosha.cad import dxfwriter as W
from kosha.cad import mesh as M


def _dxf(pn, desc, material=""):
    values = {"PART_NO": pn, "DESCRIPTION": desc}
    if material:
        values["MATERIAL"] = material
    return W.flange(values=values)


def _put(c, folder, name, data):
    r = c.call("POST", "/api/documents/upload", files={"file": (name, data)}, fields={"folder_id": folder})
    assert not r["errors"], r
    return r["documents"][0]


def _state(c, doc, to, expect=200, **extra):
    return c.call("POST", f"/api/documents/{doc['id']}/state", {"to": to, **extra}, expect=expect)


def test_checks_before_review_and_release(kosha):
    admin = kosha.client()
    eng = kosha.add_user("erin", "engineer")
    abel = kosha.add_user("abel", "approver")
    root = admin.call("GET", "/api/folders")["folders"][0]["id"]
    f = admin.call("POST", "/api/folders", {"parent_id": root, "name": "Rules"})["folder"]["id"]

    doc = _put(eng, f, "LR-100-001 Bracket.dxf", _dxf("LR-100-001", "BRACKET"))
    acts = eng.call("GET", f"/api/documents/{doc['id']}")["actions"]
    assert not acts["submit"]["ok"] and "LR-100-001 needs its material" in acts["submit"]["why"]
    err = _state(eng, doc, "in_review", expect=409)
    assert err["code"] == "checks_failed" and err["problems"] == ["LR-100-001 needs its material."]
    item = eng.call("GET", "/api/items?q=LR-100-001")["items"][0]
    eng.call("PATCH", f"/api/items/{item['id']}", {"material": "S355"})
    _state(eng, doc, "in_review")

    # sent back by the approver: their comment is shown on top of the file
    _state(abel, doc, "in_work", comment="Add the weld symbol at the gusset")
    page = eng.call("GET", f"/api/documents/{doc['id']}")
    assert page["returned"]["by"] == "Abel" and "weld symbol" in page["returned"]["comment"]
    _state(eng, doc, "in_review")
    assert eng.call("GET", f"/api/documents/{doc['id']}")["returned"] is None
    _state(abel, doc, "released")

    # four-eyes: the approver who made the last change does not release it
    own = _put(abel, f, "LR-100-002 Plate.dxf", _dxf("LR-100-002", "PLATE", "S235"))
    acts = abel.call("GET", f"/api/documents/{own['id']}")["actions"]
    assert not acts["release"]["ok"] and "four-eyes" in acts["release"]["why"]
    assert "four-eyes" in _state(abel, own, "released", expect=409)["error"]
    # a small company may allow it with a written reason
    admin.call("PUT", "/api/settings", {"allow_self_approval_with_reason": True})
    acts = abel.call("GET", f"/api/documents/{own['id']}")["actions"]
    assert acts["release"]["ok"] and acts["release_needs_reason"] and "Write why" in acts["release_why"]
    assert "Write why" in _state(abel, own, "released", expect=409)["error"]
    _state(abel, own, "released", reason="Only approver in the office this week")
    audit = admin.call("GET", f"/api/audit?entity=document&entity_id={own['id']}")
    assert any(e["action"] == "state_reason" and "Only approver" in e["details"] for e in audit["entries"])
    # or limit the rule to some categories
    admin.call("PUT", "/api/settings", {"allow_self_approval_with_reason": False, "four_eyes_categories": "rot, prv"})
    assert admin.call("GET", "/api/settings")["four_eyes_categories"] == ["PRV", "ROT"]
    plain = _put(abel, f, "LR-100-003 Spacer.dxf", _dxf("LR-100-003", "SPACER", "S235"))
    _state(abel, plain, "released")
    rot = _put(abel, f, "ROT-000001 Rotor.dxf", _dxf("ROT-000001", "ROTOR", "42CrMo4"))
    assert "four-eyes" in _state(abel, rot, "released", expect=409)["error"]
    assert "category codes" in admin.call("PUT", "/api/settings", {"four_eyes_categories": ["R-1!"]}, expect=400)["error"]
    admin.call("PUT", "/api/settings", {"four_eyes_categories": []})

    # an assembly waits for its children
    assy = eng.call("POST", "/api/items", {"part_number": "LR-200-000", "description": "FRAME ASSY",
                                           "item_type": "assembly"})["item"]
    child = _put(eng, f, "LR-100-004 Gusset.dxf", _dxf("LR-100-004", "GUSSET", "S235"))
    child_item = eng.call("GET", "/api/items?q=LR-100-004")["items"][0]
    bom = eng.call("POST", "/api/boms", {"item_id": assy["id"]})["bom_id"]
    eng.call("PUT", f"/api/boms/{bom}/lines", {"lines": [{"item_id": item["id"], "qty": 1},
                                                         {"item_id": child_item["id"], "qty": 2}]})
    ga = _put(eng, f, "LR-200-000 Frame Assy.dxf", _dxf("LR-200-000", "FRAME ASSY"))
    err = _state(eng, ga, "in_review", expect=409)
    assert "not released yet: LR-100-004" in err["error"] and "LR-100-001" not in err["error"]
    _state(eng, child, "in_review")         # waiting in review together is fine
    _state(eng, ga, "in_review")


def test_revise_together_superseded_and_spares_only(kosha):
    admin = kosha.client()
    eng = kosha.add_user("tara", "engineer")
    abel = kosha.add_user("alma", "approver")
    root = admin.call("GET", "/api/folders")["folders"][0]["id"]
    f = admin.call("POST", "/api/folders", {"parent_id": root, "name": "Spares"})["folder"]["id"]

    drw = _put(eng, f, "SP-100-001 Sleeve.dxf", _dxf("SP-100-001", "SLEEVE", "1.4401"))
    model = _put(eng, f, "SP-100-001 Sleeve.stl", M.write_stl(M.shaft_mesh((40,), (60,))))
    for d in (drw, model):
        _state(abel, d, "released")
    # revising the drawing starts the model's next revision too: they always carry the same revision
    eng.call("POST", f"/api/documents/{drw['id']}/revise", {})
    for d in (drw, model):
        page = eng.call("GET", f"/api/documents/{d['id']}")
        assert (page["revision"], page["state"]) == ("B", "in_work")
    eng.call("POST", f"/api/documents/{drw['id']}/checkout")
    eng.call("POST", f"/api/documents/{drw['id']}/checkin", files={"file": ("SP-100-001 Sleeve.dxf",
                                                                             _dxf("SP-100-001", "SLEEVE", "1.4462"))})
    _state(abel, drw, "released")
    versions = eng.call("GET", f"/api/documents/{drw['id']}")["versions"]
    assert {(v["revision"], v["superseded_by"]) for v in versions} == {("A", "B"), ("B", None)}

    # spares only: through a change order, naming the part new machines use instead
    new = _put(eng, f, "SP-100-002 Sleeve Duplex.dxf", _dxf("SP-100-002", "SLEEVE DUPLEX", "1.4462"))
    _state(abel, new, "released")
    err = _state(abel, drw, "spares_only", expect=409)
    assert err["code"] == "eco_required" and "make spares only" in err["error"]
    me = abel.call("GET", "/api/auth/me")["user"]
    co = eng.call("POST", "/api/changes", {"title": "Duplex sleeve for new builds", "approver_ids": [me["id"]]})
    err = eng.call("POST", f"/api/changes/{co['id']}/items", {"document_id": drw["id"], "action": "spares_only"},
                   expect=409)
    assert "Name the part that replaces it" in err["error"]
    assert "no part SP-999" in eng.call("POST", f"/api/changes/{co['id']}/items", {
        "document_id": drw["id"], "action": "spares_only", "replacement": "SP-999"}, expect=409)["error"]
    eng.call("POST", f"/api/changes/{co['id']}/items", {"document_id": drw["id"], "action": "spares_only",
                                                        "replacement": "sp-100-002"})
    eng.call("POST", f"/api/changes/{co['id']}/submit")
    abel.call("POST", f"/api/changes/{co['id']}/decide", {"decision": "approved"})
    done = abel.call("POST", f"/api/changes/{co['id']}/implement")
    assert any("spares only, replaced by SP-100-002" in x for x in done["done"])
    page = eng.call("GET", f"/api/documents/{drw['id']}")
    assert page["state"] == "spares_only" and page["replaced_by"]["part_number"] == "SP-100-002"
    assert page["item"]["state"] == "spares_only"
    eng.call("POST", f"/api/documents/{drw['id']}/checkout", expect=409)

    # obsolete: not while a released assembly still uses it
    admin.call("PUT", "/api/settings", {"obsolete_needs_change_order": False})
    assy = eng.call("POST", "/api/items", {"part_number": "SP-200-000", "description": "PUMP ASSY",
                                           "item_type": "assembly"})["item"]
    bom = eng.call("POST", "/api/boms", {"item_id": assy["id"]})["bom_id"]
    new_item = eng.call("GET", "/api/items?q=SP-100-002")["items"][0]
    eng.call("PUT", f"/api/boms/{bom}/lines", {"lines": [{"item_id": new_item["id"], "qty": 1}]})
    ga = _put(eng, f, "SP-200-000 Pump Assy.dxf", _dxf("SP-200-000", "PUMP ASSY"))
    _state(abel, ga, "released")
    abel.call("POST", f"/api/boms/{bom}/release")
    err = _state(abel, new, "obsolete", expect=409)
    assert "still used in released assemblies: SP-200-000" in err["error"]
    _state(abel, drw, "obsolete")           # the spares-only sleeve is in no released assembly

    # the revision scheme is chosen once, before go-live
    assert "chosen once" in admin.call("PUT", "/api/settings", {"revision_scheme": "numeric"}, expect=400)["error"]


def test_numeric_revisions_and_legacy_sequences():
    from kosha.lifecycle import clean_revision, next_revision
    assert [next_revision(r) for r in ("R0", "R1", "R9")] == ["R1", "R2", "R10"]
    assert next_revision(clean_revision("Rev 3")) == "4"        # legacy prints stay in order
    assert next_revision(clean_revision("R-2")) == "3"
