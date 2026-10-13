# SPDX-License-Identifier: AGPL-3.0-or-later
"""Standard-parts library: table checks, browse and filter, search by designation, and "Use this part"."""
import pytest
from packs_fixtures import config_of

from kosha import db
from kosha.library import LibraryTable, StandardPartsLibrary, normalise
from kosha.numbering import DataError, NumberingError
from kosha.packs import PackManager


@pytest.fixture(scope="module")
def lib():
    return StandardPartsLibrary(config_of("standard-parts"))


def _pns(result):
    return [h["part_number"] for h in result["hits"]]


def test_search_by_designation(lib, monkeypatch):
    monkeypatch.setattr("kosha.library.NumberingService.find_item", staticmethod(lambda code: None))
    assert "FST-ISO4017-M16X60-8.8" in _pns(lib.search("M16x60 8.8"))
    assert set(_pns(lib.search("M16x60 8.8"))) == {"FST-ISO4017-M16X60-8.8", "FST-ISO4014-M16X60-8.8",
                                                     "FST-ISO4762-M16X60-8.8"}
    assert _pns(lib.search("6205-2RS")) == ["BRG-6205-2RS"]
    assert set(_pns(lib.search("6205"))) == {"BRG-6205", "BRG-6205-ZZ", "BRG-6205-2RS"}
    din = lib.search("DIN 912 M8", limit=200)
    assert din["total"] == 81 and all(p.startswith("FST-ISO4762-M8X") for p in _pns(din))
    assert _pns(lib.search("m 16 x 60 iso 4017")) == ["FST-ISO4017-M16X60-8.8", "FST-ISO4017-M16X60-10.9"]
    assert not any("M16" in p for p in _pns(lib.search("ISO 4032 M1", limit=200)))     # M1 is not M16
    assert _pns(lib.search("ISO 7089 M16 300HV")) == ["FST-ISO7089-16-300HV"]
    assert _pns(lib.search("key 28x16x100 form A")) == ["KEY-ISO773-A-28X16X100"]
    exact = lib.search("ISO 4017 M16x60 8.8")
    assert exact["hits"][0]["designation"] == "ISO 4017 M16x60 8.8"
    assert lib.search("   ") == {"hits": [], "total": 0}
    assert normalise("m16 × 60") == "M16X60"


def test_browse_and_filter_by_size(lib):
    r = lib.browse("deep-groove-ball-bearings", size="25")
    assert [row["key"] for row in r["rows"]] == ["6005", "6205", "6305"]
    assert r["rows"][1]["values"] == {"bearing": "6205", "series": "62", "bore": "25", "od": "52", "width": "15"}
    assert r["table"]["sizes"][:3] == ["10", "12", "15"]
    assert r["table"]["rows"] == 63 and r["table"]["verified"] == 0
    bolts = lib.browse("hex-screws-iso4017", size="16")
    assert bolts["rows"][0]["values"]["s"] == "24" and bolts["rows"][0]["standard"] == "ISO 4017"
    assert [o["name"] for o in bolts["options"]] == ["l", "grade"]
    with pytest.raises(LookupError):
        lib.browse("no-such-table")


def test_options_are_checked_in_plain_words(lib):
    t = lib.table("hex-bolts-iso4014")
    row = lib.row(t, "m16")
    short = t.build(row, {"l": 30, "grade": "8.8"})
    assert not short["ok"] and "longer than its thread length b (38 mm)" in short["errors"]["l"]
    assert "Property class '12.9' is not in the list" in t.build(row, {"l": 80, "grade": "12.9"})["errors"]["grade"]
    ok = t.build(row, {"l": "80", "grade": "8.8"})
    assert ok["part_number"] == "FST-ISO4014-M16X80-8.8" and ok["description"] == "HEX BOLT ISO 4014 M16 X 80 8.8"
    brg = lib.table("deep-groove-ball-bearings")
    r = brg.build(lib.row(brg, "6205"), {"seal": "open"})
    assert (r["part_number"], r["designation"]) == ("BRG-6205", "6205")


def test_table_definition_checks():
    base = {"id": "t", "category": "FST", "label": "T", "standard": "ISO 1", "key": "M{d}", "size": "d",
            "columns": {"d": {"label": "Thread"}}, "options": {"l": {"type": "int", "choices": [10, 20]}},
            "designation": "ISO 1 M{d}x{l}", "part_number": "FST-ISO1-M{d}X{l}", "description": "SCREW M{d} X {l}"}
    LibraryTable(base, "pack.yaml", {"FST"})
    for change, message in (({"part_number": "FST-ISO1-M{d}"}, "'part_number' must use {l}"),
                            ({"size": "x"}, "size 'x' must be one of the columns"),
                            ({"options": {"l": {"type": "int", "min": 1}}}, "library options need a list of choices"),
                            ({"category": "BRG"}, "category 'BRG' is not defined"),
                            ({"key": "M{dia}"}, "'{dia}' is not one of the fields")):
        with pytest.raises(DataError, match=message.replace("{", r"\{").replace("}", r"\}")):
            LibraryTable({**base, **change}, "pack.yaml", {"FST"})


def test_use_this_part_creates_or_finds_the_item(kdb):
    PackManager().install("standard-parts", 3)
    lib = StandardPartsLibrary()
    first = lib.use("hex-screws-iso4017", "M16", {"l": 60, "grade": "8.8"}, 3)
    item = first["item"]
    assert not first["existing"] and item["part_number"] == "FST-ISO4017-M16X60-8.8"
    assert item["description"] == "HEX SCREW ISO 4017 M16 X 60 8.8" and item["make_buy"] == "buy" and item["uom"] == "Nos"
    assert '"standard": "ISO 4017"' in item["props_json"] and '"verified": false' in item["props_json"]
    again = lib.use("hex-screws-iso4017", "m16", {"l": "60", "grade": "8.8"}, 3)
    assert again["existing"] and again["item"]["id"] == item["id"]
    assert lib.preview("hex-screws-iso4017", "M16", {"l": 60, "grade": "8.8"})["existing"]["id"] == item["id"]
    assert lib.search("M16x60 8.8 ISO 4017")["hits"][0]["existing"]["id"] == item["id"]
    assert db.scalar("SELECT COUNT(*) FROM items WHERE part_number LIKE 'FST-%'") == 1
    with pytest.raises(NumberingError):
        lib.use("hex-screws-iso4017", "M16", {"l": 61, "grade": "8.8"}, 3)
    with pytest.raises(LookupError, match="no size 'M17'"):
        lib.use("hex-screws-iso4017", "M17", {"l": 60, "grade": "8.8"}, 3)
    # a company-added row is usable like any other
    PackManager().set_local("library_row", "washers-iso7089/42",
                            {"key": "42", "values": {"size": "42", "d1": "43", "d2": "78", "h": "8"},
                             "standard": "ISO 7089", "edition": "", "verified": False}, 3)
    assert StandardPartsLibrary().use("washers-iso7089", "42", {"hardness": "200HV"}, 3)["part_number"] == \
        "FST-ISO7089-42-200HV"
