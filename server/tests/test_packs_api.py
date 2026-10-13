# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: Admin › Packs, the numbering API (with parallel requests), the standard-parts library and BOMs."""
import threading


def test_packs_numbering_and_library_through_the_api(kosha):
    admin = kosha.client()
    eng = kosha.add_user("erin", "engineer")
    viewer = kosha.add_user("victor", "viewer")

    # packs: listed for the admin only, nothing installed yet
    packs = {p["id"]: p for p in admin.call("GET", "/api/packs")["packs"]}
    assert {"general-machinery", "standard-parts", "spares-and-service", "engineered-components",
            "india-localisation"} <= set(packs)
    assert all(p["status"] == "not_installed" and not p["problem"] for p in packs.values())
    assert packs["standard-parts"]["counts"]["library_row"] > 100
    eng.call("GET", "/api/packs", expect=403)
    eng.call("POST", "/api/packs/general-machinery/install", expect=403)
    assert admin.call("GET", "/api/library")["installed"] is False
    schemes = {s["id"]: s for s in eng.call("GET", "/api/numbering/schemes")["schemes"]}
    assert set(schemes) == {"prefix", "category", "document", "kit", "machine-build"}
    assert schemes["category"]["available"] is False and schemes["document"]["available"] is True

    plan = admin.call("GET", "/api/packs/general-machinery/plan")
    assert plan["action"] == "install" and plan["counts"]["added"] > 20
    for pid in ("general-machinery", "standard-parts", "spares-and-service"):
        assert "installed" in admin.call("POST", f"/api/packs/{pid}/install")["message"]
    admin.call("POST", "/api/packs/nope/install", expect=400)
    assert {r["status"] for r in admin.call("GET", "/api/packs/general-machinery")["records"]} == {"pack"}
    shared = {r["key"]: r for r in admin.call("GET", "/api/packs/standard-parts")["records"] if r["kind"] == "category"}
    assert shared["FST"]["status"] == "other_pack" and shared["FST"]["provided_by"] == "general-machinery"
    assert shared["KEY"]["status"] == "pack"

    # numbering: spec codes, live preview and find-before-create
    schemes = {s["id"]: s for s in eng.call("GET", "/api/numbering/schemes")["schemes"]}
    assert {"spec:spur-gear", "spec:shaft", "spec:flange", "spec:filter-element", "project",
            "general-arrangement"} <= set(schemes)
    module = next(f for f in schemes["spec:spur-gear"]["fields"] if f["name"] == "module")
    assert module["unit"] == "mm" and {"value": "1.5", "label": "1.5 mm"} in module["choices"]
    material = next(f for f in schemes["spec:spur-gear"]["fields"] if f["name"] == "material")
    assert {"value": "C45", "label": "C45 (1.0503)"} in material["choices"]
    gear = {"module": "2", "teeth": "40", "face_width_mm": "30", "material": "C45"}
    p = viewer.call("POST", "/api/numbering/preview", {"scheme": "spec:spur-gear", "values": gear})
    assert p["ok"] and p["part_number"] == "GR-2-40-30-C45" and p["existing"] is None
    bad = viewer.call("POST", "/api/numbering/preview", {"scheme": "spec:spur-gear", "values": {**gear, "teeth": 2}})
    assert not bad["ok"] and bad["errors"]["teeth"] == "Teeth must be between 8 and 300."
    viewer.call("POST", "/api/numbering/allocate", {"scheme": "spec:spur-gear", "values": gear}, expect=403)
    err = eng.call("POST", "/api/numbering/allocate", {"scheme": "spec:spur-gear", "values": {**gear, "teeth": 2}},
                   expect=400)
    assert err["code"] == "invalid_values" and "teeth" in err["errors"]

    # parallel requests for the same spec code give one item; parallel series requests never collide
    results, errors = [], []

    def worker(n):
        try:
            r = eng.call("POST", "/api/numbering/allocate", {"scheme": "spec:spur-gear", "values": gear})
            s = eng.call("POST", "/api/numbering/allocate", {"scheme": "prefix", "values": {"prefix": "RT-7-"},
                                                             "description": f"PART {n}"})
            legacy = eng.call("POST", "/api/numbering/next", {"prefix": "RT-7-"})
            results.append((r["item"]["id"], s["part_number"], legacy["part_number"]))
        except Exception as e:  # pragma: no cover
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len({r[0] for r in results}) == 1
    numbers = [r[1] for r in results] + [r[2] for r in results]
    assert len(numbers) == 20 and len(set(numbers)) == 20 and all(n.startswith("RT-7-") for n in numbers)
    again = eng.call("POST", "/api/numbering/allocate", {"scheme": "spec:spur-gear", "values": gear})
    assert again["existing"] and again["item"]["id"] == results[0][0]
    ga = eng.call("POST", "/api/numbering/allocate", {"scheme": "general-arrangement",
                                                       "values": {"kind": "GAD", "project": "26014"},
                                                       "description": "ROTARY TABLE GA"})
    assert ga["part_number"] == "GAD-26014-001" and ga["item"]["item_type"] == "assembly"
    allocations = eng.call("GET", "/api/numbering/allocations")["allocations"]
    assert any(a["part_number"] == "GR-2-40-30-C45" and a["scheme"] == "spec:spur-gear" for a in allocations)

    # library: browse, filter, search and use; the part goes straight into a BOM
    lib = eng.call("GET", "/api/library")
    assert lib["installed"] and {c["code"] for c in lib["categories"]} == {"FST", "BRG", "KEY"}
    rows = eng.call("GET", "/api/library/tables/deep-groove-ball-bearings?size=25")["rows"]
    assert [r["key"] for r in rows] == ["6005", "6205", "6305"]
    eng.call("GET", "/api/library/tables/nothing", expect=404)
    hits = eng.call("GET", "/api/library/search?q=6205-2RS")["hits"]
    assert hits[0]["part_number"] == "BRG-6205-2RS" and hits[0]["verified"] is False
    viewer.call("POST", "/api/library/use", {"table": "deep-groove-ball-bearings", "row": "6205",
                                             "options": {"seal": "2RS"}}, expect=403)
    used = eng.call("POST", "/api/library/use", {"table": "deep-groove-ball-bearings", "row": "6205",
                                                 "options": {"seal": "2RS"}})
    assert used["part_number"] == "BRG-6205-2RS" and not used["existing"] and used["item"]["make_buy"] == "buy"
    bolt = eng.call("POST", "/api/library/use", {"table": "hex-screws-iso4017", "row": "M16",
                                                 "options": {"l": "60", "grade": "8.8"}})
    assert bolt["part_number"] == "FST-ISO4017-M16X60-8.8"
    bad = eng.call("POST", "/api/library/use", {"table": "hex-bolts-iso4014", "row": "M16",
                                                "options": {"l": "30", "grade": "8.8"}}, expect=400)
    assert "thread length" in bad["errors"]["l"]
    assert eng.call("POST", "/api/library/preview", {"table": "deep-groove-ball-bearings", "row": "6205",
                                                     "options": {"seal": "2RS"}})["existing"]["id"] == used["item"]["id"]
    bom_id = eng.call("POST", "/api/boms", {"item_id": ga["item"]["id"]})["bom_id"]
    bom = eng.call("PUT", f"/api/boms/{bom_id}/lines", {"lines": [
        {"item_id": used["item"]["id"], "qty": 2}, {"part_number": "FST-ISO4017-M16X60-8.8", "qty": 8},
        {"item_id": results[0][0], "qty": 1}]})
    assert [ln["part_number"] for ln in bom["lines"]] == ["BRG-6205-2RS", "FST-ISO4017-M16X60-8.8", "GR-2-40-30-C45"]
    assert eng.call("GET", "/api/items?q=FST-ISO4017")["items"][0]["id"] == bolt["item"]["id"]

    # the company's own change survives; removing a pack never deletes items
    admin.call("PUT", "/api/pack-records", {"kind": "dictionary", "key": "materials/EN8",
                                            "value": {"code": "EN8", "name": "EN8 (080M40)"}})
    bad = admin.call("PUT", "/api/pack-records", {"kind": "category", "key": "GR", "value": {"code": "GR"}},
                     expect=400)
    assert "'name' is missing" in bad["error"]
    eng.call("PUT", "/api/pack-records", {"kind": "category", "key": "GR", "value": {"code": "GR", "name": "x"}},
             expect=403)
    changes = admin.call("GET", "/api/pack-records/changes")["changes"]
    assert [(c["key"], c["origin"]) for c in changes] == [("materials/EN8", "local")]
    removed = admin.call("POST", "/api/packs/standard-parts/remove")
    assert removed["items_kept"] == 2
    assert eng.call("GET", f"/api/items/{used['item']['id']}")["item"]["part_number"] == "BRG-6205-2RS"
    assert eng.call("GET", "/api/library")["installed"] is False
    assert eng.call("GET", f"/api/boms/{bom_id}")["lines"][0]["part_number"] == "BRG-6205-2RS"
    admin.call("POST", "/api/packs/standard-parts/remove", expect=400)
    assert admin.call("POST", "/api/packs/standard-parts/install")["action"] == "reinstall"
    assert eng.call("POST", "/api/library/use", {"table": "deep-groove-ball-bearings", "row": "6205",
                                                 "options": {"seal": "2RS"}})["existing"] is True
    materials = viewer.call("GET", "/api/pack-records?kind=dictionary")["records"]
    assert any(r["key"] == "materials/EN8" and r["origin"] == "local" for r in materials)

    # the CAD add-ins' endpoint still works and stays in step with the new service
    assert eng.call("POST", "/api/numbering/next", {"prefix": "RT-7-"})["part_number"] == "RT-7-021"


def test_demo_installation_has_the_packs(demo_kosha):
    asha = demo_kosha.client("asha")
    lib = asha.call("GET", "/api/library")
    assert lib["installed"] and lib["can_use"]
    schemes = {s["id"] for s in asha.call("GET", "/api/numbering/schemes")["schemes"]}
    assert {"spec:spur-gear", "spec:shaft", "spec:flange"} <= schemes
    hits = asha.call("GET", "/api/library/search?q=M16x60%208.8")["hits"]
    assert "FST-ISO4017-M16X60-8.8" in [h["part_number"] for h in hits]
    key = asha.call("POST", "/api/library/use", {"table": "parallel-keys-iso773", "row": "28x16",
                                                 "options": {"form": "A", "l": "100"}})
    assert key["part_number"] == "KEY-ISO773-A-28X16X100" and key["item"]["make_buy"] == "buy"
    tom = demo_kosha.client("tom")
    assert tom.call("GET", "/api/library")["can_use"] is False
