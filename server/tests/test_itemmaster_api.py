# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: product families and spare flags, OEM cross-references and customer parts locked against reuse."""


def test_families_flags_and_customer_fields(kosha):
    admin = kosha.client()
    eng = kosha.add_user("erin", "engineer")
    appr = kosha.add_user("abel", "approver")
    admin.call("POST", "/api/packs/general-machinery/install")

    it = eng.call("POST", "/api/items", {"part_number": "SHF-000101", "description": "Pump shaft"})["item"]
    path = f"/api/items/{it['id']}"
    done = eng.call("PATCH", path, {"family": "pu", "spare": True, "wear_part": 1, "hsn": "84833000"})["item"]
    assert (done["family"], done["spare"], done["wear_part"], done["recommended_spare"]) == ("PU", 1, 1, 0)
    page = eng.call("GET", path)
    assert page["family"]["code"] == "PU" and page["oem_refs"] == [] and page["reuse_allowed"] == []

    err = eng.call("PATCH", path, {"family": "ZZ"}, expect=400)["error"]
    assert "no product family 'ZZ'" in err and "PU" in err
    assert "4, 6 or 8 digits" in eng.call("PATCH", path, {"hsn": "123"}, expect=400)["error"]
    assert "yes or no" in eng.call("PATCH", path, {"spare": "maybe"}, expect=400)["error"]

    # who a part belongs to and whether it may be reused is an approver's decision
    eng.call("PATCH", path, {"customer": "Demo Customer C"}, expect=409)
    eng.call("PATCH", path, {"customer_part_number": "CP-77-01"})
    assert "Name the customer first" in appr.call("PATCH", path, {"restricted": True}, expect=400)["error"]
    assert "Nobody has access to Demo Customer A" in appr.call(
        "PATCH", path, {"customer": "Demo Customer A", "restricted": True}, expect=400)["error"]
    abel = next(u for u in admin.call("GET", "/api/users")["users"] if u["username"] == "abel")
    admin.call("POST", "/api/access/rules", {"scope": "customer", "value": "Demo Customer A",
                                             "members": [f"user:{abel['id']}"]})
    appr.call("PATCH", path, {"customer": "Demo Customer A", "restricted": True, "no_reuse": True})
    assert "Name the customer first" in appr.call("PATCH", path, {"customer": ""}, expect=400)["error"]
    # the part is now restricted: the engineer, not on the customer's team, no longer finds it
    eng.call("PATCH", path, {"customer": "Demo Customer A", "description": "Pump shaft, keyed"}, expect=404)
    appr.call("PATCH", path, {"customer": "Demo Customer A", "description": "Pump shaft, keyed"})

    # the customer's own part number finds the part, for those who may see it
    assert [i["part_number"] for i in appr.call("GET", "/api/items?q=CP-77")["items"]] == ["SHF-000101"]
    assert [i["part_number"] for i in appr.call("GET", "/api/search?q=CP-77")["items"]] == ["SHF-000101"]
    assert eng.call("GET", "/api/items?q=CP-77")["items"] == []


def test_oem_cross_references_and_search(kosha):
    admin = kosha.client()
    eng = kosha.add_user("olga", "engineer")
    viewer = kosha.add_user("victor", "viewer")

    it = eng.call("POST", "/api/items", {"part_number": "SLV-000007", "description": "Shaft sleeve"})["item"]
    refs = f"/api/items/{it['id']}/oem-refs"
    assert "no OEM code 'ABC'" in eng.call("POST", refs, {"oem": "abc", "oem_part_number": "1"}, expect=400)["error"]
    eng.call("POST", "/api/oems", {"code": "ABC", "name": "Another Pump Maker"}, expect=403)
    assert "2 to 4 capital letters" in admin.call("POST", "/api/oems", {"code": "A1", "name": "X"}, expect=400)["error"]
    admin.call("POST", "/api/oems", {"code": "ABC", "name": "Another Pump Maker"})
    admin.call("POST", "/api/oems", {"code": "abc", "name": "Again"}, expect=409)
    listed = viewer.call("GET", "/api/oems")
    assert listed["oems"] == [{"code": "ABC", "name": "Another Pump Maker"}] and listed["can_add"] is False

    assert "part number or the OEM machine model" in eng.call("POST", refs, {"oem": "ABC"}, expect=400)["error"]
    assert "Source of design" in eng.call("POST", refs, {"oem": "ABC", "oem_part_number": "1",
                                                         "source": "guess"}, expect=400)["error"]
    ref = eng.call("POST", refs, {"oem": "abc", "oem_model": "CP-200", "oem_part_number": "45.120-07",
                                  "source": "reverse_engineered", "fit": "direct"})["oem_ref"]
    eng.call("POST", refs, {"oem": "ABC", "oem_part_number": "45.120-07"}, expect=409)
    viewer.call("POST", refs, {"oem": "ABC", "oem_part_number": "9"}, expect=403)
    shown = eng.call("GET", f"/api/items/{it['id']}")["oem_refs"]
    assert [(r["oem"], r["oem_name"], r["oem_part_number"]) for r in shown] == [
        ("ABC", "Another Pump Maker", "45.120-07")]

    # typed with or without the dots and dashes, the OEM number finds the company's own part
    for q in ("45.120-07", "4512007", "45 120 07"):
        m = viewer.call("GET", f"/api/oem-refs?q={q.replace(' ', '%20')}")["matches"]
        assert [x["part_number"] for x in m] == ["SLV-000007"], q
    assert viewer.call("GET", "/api/oem-refs?q=cp-200")["matches"][0]["oem_model"] == "CP-200"
    assert [i["part_number"] for i in viewer.call("GET", "/api/search?q=45.120")["items"]] == ["SLV-000007"]
    assert [i["part_number"] for i in viewer.call("GET", "/api/items?q=CP-200")["items"]] == ["SLV-000007"]
    # the maker's name is never part of the company's own number or description
    assert "Another" not in eng.call("GET", f"/api/items/{it['id']}")["item"]["description"]

    eng.call("DELETE", f"/api/oem-refs/{ref['id']}")
    eng.call("DELETE", f"/api/oem-refs/{ref['id']}", expect=404)
    assert viewer.call("GET", "/api/oem-refs?q=4512007")["matches"] == []
    with kosha.db() as con:
        assert con.execute("SELECT deleted FROM item_oem_refs WHERE id = ?", (ref["id"],)).fetchone()[0] == 1


def test_a_locked_customer_part_needs_an_admin_to_be_reused(kosha):
    admin = kosha.client()
    eng = kosha.add_user("tara", "engineer")
    appr = kosha.add_user("ben", "approver")

    def item(pn, **fields):
        iid = eng.call("POST", "/api/items", {"part_number": pn, "description": pn})["item"]["id"]
        if fields:
            appr.call("PATCH", f"/api/items/{iid}", fields)
        return iid

    locked = item("PLT-000001", customer="Demo Customer A", no_reuse=True)
    same = item("ASM-000001", customer="demo customer a")
    other = item("ASM-000002", customer="Demo Customer B")
    plain = item("BRG-000001")

    def bom(parent):
        return eng.call("POST", "/api/boms", {"item_id": parent})["bom_id"]

    ok = bom(same)
    eng.call("PUT", f"/api/boms/{ok}/lines", {"lines": [{"item_id": locked, "qty": 1}, {"item_id": plain, "qty": 2}]})

    blocked = bom(other)
    err = eng.call("PUT", f"/api/boms/{blocked}/lines", {"lines": [{"item_id": plain, "qty": 1},
                                                                   {"item_id": locked, "qty": 1}]}, expect=409)
    assert "PLT-000001 is made for Demo Customer A and locked against reuse" in err["error"]
    assert eng.call("GET", f"/api/boms/{blocked}")["lines"] == []          # nothing half-saved

    path = f"/api/items/{locked}/allow-reuse"
    appr.call("POST", path, {"parent_part_number": "ASM-000002", "reason": "Customer B agreed"}, expect=403)
    assert "Write why" in admin.call("POST", path, {"parent_part_number": "ASM-000002", "reason": "ok"},
                                     expect=400)["error"]
    assert "no part ASM-9" in admin.call("POST", path, {"parent_part_number": "ASM-9", "reason": "Because so"},
                                         expect=400)["error"]
    admin.call("POST", path, {"parent_part_number": "asm-000002", "reason": "Both customers agreed in writing"})
    admin.call("POST", path, {"parent_item_id": other, "reason": "Both customers agreed"}, expect=409)
    eng.call("PUT", f"/api/boms/{blocked}/lines", {"lines": [{"item_id": locked, "qty": 1}]})
    allowed = eng.call("GET", f"/api/items/{locked}")["reuse_allowed"]
    assert allowed[0]["parent_part_number"] == "ASM-000002" and allowed[0]["approved_by_name"]

    # a part locked after it was already used stays where it is; it is not added anywhere new
    late = item("PLT-000002")
    third = bom(item("ASM-000003"))
    eng.call("PUT", f"/api/boms/{third}/lines", {"lines": [{"item_id": late, "qty": 1}]})
    appr.call("PATCH", f"/api/items/{late}", {"customer": "Demo Customer A", "no_reuse": True})
    eng.call("PUT", f"/api/boms/{third}/lines", {"lines": [{"item_id": late, "qty": 3}, {"item_id": plain, "qty": 1}]})
    with kosha.db() as con:
        assert con.execute("SELECT COUNT(*) FROM audit WHERE action = 'reuse_allow'").fetchone()[0] == 1


def test_demo_data_shows_families_spares_and_an_oem_number(demo_kosha):
    tom = demo_kosha.client("tom")
    m = tom.call("GET", "/api/oem-refs?q=S441021")["matches"]
    assert [(x["part_number"], x["oem"]) for x in m] == [("MX-100-021", "OMW")]
    page = tom.call("GET", f"/api/items/{m[0]['item_id']}")
    assert (page["item"]["family"], page["item"]["spare"], page["item"]["wear_part"]) == ("MX", 1, 1)
