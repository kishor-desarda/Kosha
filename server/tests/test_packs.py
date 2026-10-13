# SPDX-License-Identifier: AGPL-3.0-or-later
"""Industry packs: every shipped pack loads; errors name the file and row; install, update and remove keep
the company's changes and never delete items."""
import re

import pytest
from packs_fixtures import REPO_PACKS

from kosha import db
from kosha.numbering import NumberingService
from kosha.packs import DataError, PackConfig, PackLoader, PackManager


def test_every_shipped_pack_loads_cleanly():
    packs, problems = PackLoader(REPO_PACKS).load_all()
    assert problems == {}
    ids = {p.id for p in packs}
    assert {"general-machinery", "engineered-components", "spares-and-service", "india-localisation",
            "standard-parts"} <= ids
    for p in packs:
        assert p.licence == "CC-BY-4.0", p.id
        assert re.match(r"^\d+\.\d+\.\d+$", p.version)
    templates = {r.key for p in packs for r in p.records if r.kind == "spec_template"}
    assert {"spur-gear", "shaft", "flange", "filter-element"} <= templates
    rows = [r for p in packs for r in p.records if r.kind == "library_row"]
    assert len(rows) > 100
    for r in rows:      # every row names its standard; nothing is marked verified without an edition
        assert r.value["standard"].startswith(("ISO", "DIN"))
        assert r.value["verified"] is False or r.value["edition"]
    india = next(p for p in packs if p.id == "india-localisation")
    assert {r.key for r in india.records if r.kind == "field"} == {"hsn_code", "gst_rate"}
    assert any(r.kind == "unit" and r.key == "Nos" for r in india.records)


def _edit(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


@pytest.mark.parametrize("file, old, new, message", [
    ("standard-parts/fasteners/hex-nuts-iso4032.csv", "6,1,10,5.2,ISO 4032,,no", "6,1,10,5.2,ISO 4032,,maybe",
     r"packs/standard-parts/fasteners/hex-nuts-iso4032.csv row 5: 'verified' must be yes or no, found 'maybe'"),
    ("standard-parts/fasteners/hex-nuts-iso4032.csv", "8,1.25,13,6.8", "8,1.25,1three,6.8",
     r"hex-nuts-iso4032.csv row 6: 's' \(Across flats\) must be a number, found '1three'"),
    ("standard-parts/fasteners/hex-nuts-iso4032.csv", "10,1.5,16,8.4,ISO 4032,,no", "8,1.5,16,8.4,ISO 4032,,no",
     r"hex-nuts-iso4032.csv row 7: M8 appears twice \(first on row 6\)"),
    ("standard-parts/fasteners/hex-nuts-iso4032.csv", "12,1.75,18,10.8,ISO 4032,,no", "12,1.75,18,10.8,,,no",
     r"row 8: 'standard' is empty"),
    ("standard-parts/fasteners/hex-nuts-iso4032.csv", "16,2,24,14.8,ISO 4032,,no", "16,2,24,14.8,ISO 4032,,yes",
     r"row 9: a verified row must name the edition"),
    ("standard-parts/bearings/deep-groove-ball-bearings.csv", "bearing,series,bore", "bearing,series,bore_d",
     r"deep-groove-ball-bearings.csv: the first line is missing the column bore"),
    ("general-machinery/pack.yaml", "  - {code: GR, name: Gears}", "  - {code: GR, name: Gears",
     r"packs/general-machinery/pack.yaml line \d+: this is not valid YAML"),
    ("general-machinery/pack.yaml", "  - {code: SH, name: Shafts}", "  - {code: GR, name: Shafts}",
     r"pack.yaml, categories entry 2: 'GR' appears twice"),
    ("general-machinery/spec-codes/shaft.yaml", "min: 10, max: 12000", "min: 12000, max: 10",
     r"packs/general-machinery/spec-codes/shaft.yaml \(template 'shaft'\), field 'length_mm': min \(12000\) is greater"),
    ("general-machinery/pack.yaml", "version: 1.0.0", "version: one",
     r"version must look like 1.0.0"),
])
def test_errors_name_the_file_and_row(packs_copy, file, old, new, message):
    _edit(packs_copy / file, old, new)
    with pytest.raises(DataError) as e:
        PackLoader(packs_copy).load(file.split("/")[0])
    assert re.search(message, str(e.value)), str(e.value)


def _names(kind="category"):
    return {r.key: r.value.get("name") for r in PackConfig.current().records(kind)}


def test_install_update_remove_keep_company_changes_and_items(kdb, packs_copy):
    m = PackManager(PackLoader(packs_copy))
    r = m.install("general-machinery", 7)
    assert r["action"] == "install" and r["counts"]["added"] > 20
    assert _names()["GR"] == "Gears"
    assert m.install("general-machinery", 7)["action"] == "none"

    # the company renames a category, adds a material and switches off a unit
    m.set_local("category", "GR", {"code": "GR", "name": "Gears and pinions"}, 7)
    m.set_local("dictionary", "materials/EN8", {"code": "EN8", "name": "EN8 (080M40)"}, 7)
    m.hide("unit", "Set", 7)
    with pytest.raises(DataError, match="does not match"):
        m.set_local("category", "GR", {"code": "GX", "name": "x"}, 7)
    with pytest.raises(DataError, match="category code 'gear'"):
        m.set_local("category", "gear", {"code": "gear", "name": "x"}, 7)
    assert _names()["GR"] == "Gears and pinions"
    assert "EN8" in {c.code for c in PackConfig.current().dictionary("materials")}
    assert "Set" not in _names("unit")
    gear = NumberingService().allocate("spec:spur-gear", {"module": 2, "teeth": 30, "face_width_mm": 20,
                                                          "material": "EN8"}, 7)["item"]
    assert gear["part_number"] == "GR-2-30-20-EN8"

    # version 1.1.0 renames GR as well, drops SH, adds a category and changes a material
    gm = packs_copy / "general-machinery"
    _edit(gm / "pack.yaml", "version: 1.0.0", "version: 1.1.0")
    _edit(gm / "pack.yaml", "{code: GR, name: Gears}", "{code: GR, name: Gear wheels}")
    _edit(gm / "pack.yaml", "  - {code: SH, name: Shafts}\n", "")
    _edit(gm / "pack.yaml", "  - {code: FR, name: Frames and weldments}", "  - {code: FR, name: Frames and weldments}\n"
                                                                           "  - {code: SP, name: Springs}")
    _edit(gm / "pack.yaml", 'name: "C45 (1.0503)"', 'name: "C45 unalloyed steel (1.0503)"')
    _edit(gm / "spec-codes/shaft.yaml", "category: SH", "category: FR")
    plan = m.plan("general-machinery")
    assert plan["action"] == "update" and plan["from_version"] == "1.0.0" and plan["to_version"] == "1.1.0"
    assert {x["key"] for x in plan["added"]} == {"SP"}
    assert {x["key"] for x in plan["removed"]} == {"SH"}
    assert {x["key"] for x in plan["kept_local"]} == {"GR"}
    r = m.install("general-machinery", 7)
    assert "1 of your own changes were kept" in r["message"]
    cats = _names()
    assert cats["GR"] == "Gears and pinions" and cats["SP"] == "Springs" and "SH" not in cats
    gr = PackConfig.current().get("category", "GR")
    assert gr.origin == "changed" and gr.conflict and gr.pack_value["name"] == "Gear wheels"
    assert "C45 unalloyed steel (1.0503)" in {c.name for c in PackConfig.current().dictionary("materials")}
    assert "EN8" in {c.code for c in PackConfig.current().dictionary("materials")}
    assert "Set" not in _names("unit")
    m.keep_mine("category", "GR", 7)
    assert not PackConfig.current().get("category", "GR").conflict
    assert any(c["key"] == "GR" and c["origin"] == "changed" for c in m.local_changes())

    # an older pack is refused
    _edit(gm / "pack.yaml", "version: 1.1.0", "version: 0.9.0")
    with pytest.raises(DataError, match=r"older than the installed one \(1.1.0\). Nothing was changed"):
        m.install("general-machinery", 7)
    _edit(gm / "pack.yaml", "version: 0.9.0", "version: 1.1.0")

    # removal switches the pack's data off, but items, numbers and the company's own records stay
    r = m.remove("general-machinery", 7)
    assert r["items_kept"] == 1 and "stay, with their part numbers" in r["message"]
    assert db.one("SELECT * FROM items WHERE id = ?", (gear["id"],))["part_number"] == "GR-2-30-20-EN8"
    assert db.scalar("SELECT COUNT(*) FROM number_allocations WHERE item_id = ?", (gear["id"],)) == 1
    cfg = PackConfig.current()
    assert set(_names()) == {"GR"}                          # only the company's renamed category
    assert {c.code for c in cfg.dictionary("materials")} == {"EN8"}
    assert not cfg.records("spec_template")
    with pytest.raises(DataError, match="not installed"):
        m.remove("general-machinery", 7)

    # installing again brings everything back, with the company's changes still on top
    r = m.install("general-machinery", 7)
    assert r["action"] == "reinstall"
    assert _names()["GR"] == "Gears and pinions" and "SP" in _names()
    m.revert("category", "GR", 7)
    assert _names()["GR"] == "Gear wheels"
    m.revert("unit", "Set", 7)
    assert "Set" in _names("unit")
    history = [h["action"] for h in db.rows("SELECT action FROM pack_history ORDER BY id")]
    assert history == ["install", "update", "remove", "reinstall"]


def test_records_shared_by_two_packs(kdb):
    m = PackManager()
    m.install("general-machinery", None)
    m.install("standard-parts", None)
    fst = PackConfig.current().get("category", "FST")
    assert fst.pack_id == "general-machinery" and fst.providers == ["general-machinery", "standard-parts"]
    m.remove("general-machinery", None)
    fst = PackConfig.current().get("category", "FST")
    assert fst and fst.pack_id == "standard-parts"            # still there: the other pack provides it
    assert "GR" not in {c["code"] for c in PackConfig.current().categories()}
    detail = m.detail("general-machinery")
    assert detail["status"] == "removed"
    assert {r["status"] for r in detail["records"]} == {"not_installed"}


def test_overview_reports_broken_and_missing_packs(kdb, packs_copy):
    m = PackManager(PackLoader(packs_copy))
    m.install("india-localisation", None)
    _edit(packs_copy / "spares-and-service/pack.yaml", "id: spares-and-service", "id: spares")
    import shutil
    shutil.rmtree(packs_copy / "india-localisation")
    by_id = {p["id"]: p for p in m.overview()}
    assert "must be the same as the folder name" in by_id["spares-and-service"]["problem"]
    assert by_id["india-localisation"]["status"] == "installed"
    assert "no longer on the server" in by_id["india-localisation"]["problem"]
    assert _names("unit")["Nos"] == "Numbers (pieces)"    # what it installed is kept
