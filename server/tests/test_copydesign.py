# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy design through the API on the Demo Engineering agitator: preview, copy, BOM, history, idempotency."""
import pytest


@pytest.fixture(scope="module")
def ctx(demo_kosha):
    asha = demo_kosha.client("asha")
    asm_doc = next(d for d in asha.call("GET", "/api/search?q=MX-100-000")["documents"] if d["ext"] == "stl")
    mixers = next(f for f in asha.call("GET", "/api/folders")["folders"] if f["name"] == "Mixers")
    std_bh = next(i for i in asha.call("GET", "/api/items?q=STD-BH-2208")["items"])
    return {"asha": asha, "tom": demo_kosha.client("tom"), "admin": demo_kosha.client(), "asm_doc": asm_doc,
            "mixers": mixers["id"], "std_bh": std_bh}


def body(ctx, **kw):
    b = {"document_id": ctx["asm_doc"]["id"], "folder_id": ctx["mixers"], "new_folder": "MX-VA-2000 Vertical Agitator",
         "prefix": "MX-200-", "replace_from": "AGITATOR", "replace_to": "MIXER"}
    b.update(kw)
    return b


def counts(c):
    return (len(c.call("GET", "/api/items?limit=1000")["items"]), len(c.call("GET", "/api/documents?limit=1000")["documents"]),
            len(c.call("GET", "/api/boms")["boms"]))


def test_preview_lists_every_part_with_a_sensible_default(ctx):
    p = ctx["asha"].call("POST", "/api/copy-design/preview", body(ctx))
    rows = {r["part_number"]: r for r in p["rows"]}
    assert p["problems"] == [] and p["rows"][0]["part_number"] == "MX-100-000" and p["rows"][0]["level"] == 0
    assert {k: rows[k]["action"] for k in rows} == {
        "MX-100-000": "copy", "MX-100-010": "copy", "MX-100-020": "copy", "MX-100-030": "copy", "MX-100-031": "copy",
        "MX-100-050": "copy", "STD-BRG-22220": "reuse", "MX-100-040": "reuse", "STD-BLT-M16X60": "reuse", "STD-WSH-M16": "reuse"}
    assert rows["MX-100-040"]["reason"] == "Bought-out part" and rows["STD-WSH-M16"]["reason"] == "Standard part"
    assert [r["new_part_number"] for r in p["rows"] if r["action"] == "copy"] == [f"MX-200-00{k}" for k in range(1, 7)]
    assert rows["MX-100-000"]["new_description"] == "MIXER ASSEMBLY"
    shaft_files = {f["filename"]: f["new_filename"] for f in rows["MX-100-020"]["files"]}
    assert shaft_files == {"MX-100-020 Agitator Shaft.dxf": "MX-200-003 Agitator Shaft.dxf",
                           "MX-100-020 Agitator Shaft.stl": "MX-200-003 Agitator Shaft.stl"}
    assert any("title block" in n for n in p["notes"]) and any("No file content is duplicated" in n for n in p["notes"])
    assert p["counts"] == {"copy": 6, "reuse": 4, "replace": 0, "files": 8}
    no_drawings = ctx["asha"].call("POST", "/api/copy-design/preview", body(ctx, include_drawings=False))
    assert no_drawings["counts"]["files"] == 3     # the 3D models only


def test_problems_are_listed_and_nothing_is_copied(ctx):
    asha = ctx["asha"]
    p = asha.call("POST", "/api/copy-design/preview", body(ctx, prefix="", folder_id=None,
                                                          rows=[{"item_id": ctx["asm_doc"]["item_id"], "action": "reuse"}]))
    assert "Choose the folder the copy goes into." in p["problems"]
    assert any("always copied" in x for x in p["problems"]) and any("prefix" in x for x in p["problems"])
    before = counts(asha)
    shaft_item = next(i for i in asha.call("GET", "/api/items?q=MX-100-020")["items"])
    r = asha.call("POST", "/api/copy-design", body(ctx, key="bad-copy-0001",
                                                  rows=[{"item_id": shaft_item["id"], "new_part_number": "MX-100-010"}]),
                  expect=409)
    assert r["code"] == "copy_problems" and "MX-100-010 already exists" in r["error"] and r["error"].startswith("Nothing was copied")
    assert counts(asha) == before
    asha.call("GET", "/api/copy-design/jobs/bad-copy-0001", expect=404)
    asha.call("POST", "/api/copy-design", body(ctx), expect=400)                       # no key
    ctx["tom"].call("POST", "/api/copy-design/preview", body(ctx), expect=403)


def test_copy_with_reuse_replace_and_typed_number(ctx):
    asha = ctx["asha"]
    items = {i["part_number"]: i for i in asha.call("GET", "/api/items?limit=1000")["items"]}
    before = counts(asha)
    rows = [{"item_id": items["MX-100-031"]["id"], "action": "replace", "replace_item_id": ctx["std_bh"]["id"]},
            {"item_id": items["MX-100-050"]["id"], "new_part_number": "MX-200-050", "new_description": "KEY 28X16X100"}]
    res = asha.call("POST", "/api/copy-design", body(ctx, key="copy-mx-va-2000", rows=rows))
    ctx["result"] = res
    made = {c["source"]["part_number"]: c for c in res["created"]}
    assert [c["item"]["part_number"] for c in res["created"]] == ["MX-200-001", "MX-200-002", "MX-200-003", "MX-200-004",
                                                                  "MX-200-050"]
    assert made["MX-100-050"]["item"]["description"] == "KEY 28X16X100"
    assert res["replaced"] == [{"item_id": items["MX-100-031"]["id"], "part_number": "MX-100-031",
                                "by_item_id": ctx["std_bh"]["id"], "by_part_number": "STD-BH-2208"}]
    assert {x["part_number"] for x in res["reused"]} == {"STD-BRG-22220", "MX-100-040", "STD-BLT-M16X60", "STD-WSH-M16"}
    assert res["folder"]["path"] == "Kosha › Products › Mixers › MX-VA-2000 Vertical Agitator"
    it, docs, bms = counts(asha)
    assert (it, docs, bms) == (before[0] + 5, before[1] + 7, before[2] + 1)

    # the BOM of the new assembly points at the new parts, the replacement and the reused standard parts
    bom = asha.call("GET", f"/api/boms/{made['MX-100-000']['bom_id']}")
    assert bom["bom"]["part_number"] == "MX-200-001" and bom["bom"]["revision"] == "A" and bom["bom"]["state"] == "draft"
    assert [(ln["part_number"], ln["qty"]) for ln in bom["lines"]] == [
        ("MX-200-002", 1), ("MX-200-003", 1), ("MX-200-004", 1), ("STD-BH-2208", 1), ("MX-200-050", 1),
        ("STD-BRG-22220", 2), ("MX-100-040", 2), ("STD-BLT-M16X60", 8), ("STD-WSH-M16", 8)]
    original = asha.call("GET", f"/api/items/{ctx['asm_doc']['item_id']}")
    old_bom = asha.call("GET", f"/api/boms/{original['boms'][0]['id']}")
    assert [ln["part_number"] for ln in old_bom["lines"]][:4] == ["MX-100-010", "MX-100-020", "MX-100-030", "MX-100-031"]

    # copies start at revision A in work, say where they came from, and share the stored file content
    shaft_copy = next(d for d in made["MX-100-020"]["documents"] if d["filename"].endswith(".dxf"))
    new = asha.call("GET", f"/api/documents/{shaft_copy['id']}")
    old = asha.call("GET", f"/api/documents/{shaft_copy['source_id']}")
    assert (new["filename"], new["revision"], new["state"], new["version"]) == ("MX-200-003 Agitator Shaft.dxf", "A", "in_work", 1)
    assert new["events"][0]["comment"] == "Copied from MX-100-020 rev C"
    assert new["versions"][0]["sha"] == next(v["sha"] for v in old["versions"] if v["revision"] == "C")
    assert new["versions"][0]["meta"]["copied_from"]["revision"] == "C"
    assert new["item"]["props"]["copied_from"].startswith("MX-100-020 rev")
    assert new["item"]["description"] == "MIXER SHAFT"
    # the numbering continues after the numbers the copy used
    assert asha.call("POST", "/api/numbering/next", {"prefix": "MX-200-"})["part_number"] == "MX-200-005"


def test_running_the_same_copy_again_creates_nothing(ctx):
    asha = ctx["asha"]
    first = ctx["result"]
    items = {i["part_number"]: i for i in asha.call("GET", "/api/items?limit=1000")["items"]}
    rows = [{"item_id": items["MX-100-031"]["id"], "action": "replace", "replace_item_id": ctx["std_bh"]["id"]},
            {"item_id": items["MX-100-050"]["id"], "new_part_number": "MX-200-050", "new_description": "KEY 28X16X100"}]
    before = counts(asha)
    again = asha.call("POST", "/api/copy-design", body(ctx, key="copy-mx-va-2000", rows=rows))
    assert again["replayed"] is True and again["created"] == first["created"] and again["job_id"] == first["job_id"]
    assert counts(asha) == before
    r = asha.call("POST", "/api/copy-design", body(ctx, key="copy-mx-va-2000", prefix="MX-201-"), expect=409)
    assert r["code"] == "copy_key_reused"
    job = asha.call("GET", "/api/copy-design/jobs/copy-mx-va-2000")
    assert job["status"] == "done" and job["result"]["created"] == first["created"]
    # a second copy into the same folder would clash with the file names the first one made
    p = asha.call("POST", "/api/copy-design/preview", body(ctx, prefix="MX-200-", rows=[
        {"item_id": items["MX-100-020"]["id"], "new_part_number": "MX-200-003"}]))
    assert "MX-200-003 already exists. Choose another number for MX-100-020." in p["problems"]
