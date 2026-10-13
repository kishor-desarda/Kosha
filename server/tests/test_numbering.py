# SPDX-License-Identifier: AGPL-3.0-or-later
"""Numbering service: template checks, code building, find-before-create and allocation under concurrency."""
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from packs_fixtures import config_of

from kosha import boms, db
from kosha.numbering import (Choice, DataError, FieldError, FieldSpec, NumberingError, NumberingService,
                             SequenceScheme, SpecTemplate)
from kosha.packs import PackManager

GEAR = {"id": "spur-gear", "category": "GR", "label": "Spur gear",
        "code": "GR-{module}-{teeth}-{face_width_mm}-{material}",
        "fields": {"module": {"type": "decimal", "unit": "mm", "choices": [1, 1.5, 2]},
                   "teeth": {"type": "int", "min": 8, "max": 300},
                   "face_width_mm": {"type": "int", "min": 5, "max": 400},
                   "material": {"type": "choice", "dictionary": "materials"}},
        "description": "Spur gear m{module} z{teeth} b{face_width_mm} {material.name}"}
DICTS = {"materials": [Choice("C45", "C45 (1.0503)"), Choice("1.4401", "1.4401 stainless (316)")]}


def broken(**changes):
    d = {**GEAR, **changes}
    return {k: v for k, v in d.items() if v is not None}


# ------------------------------------------------------------ template checks --

@pytest.mark.parametrize("data, message", [
    (broken(code="GR-{module}-{teeth}-{material}"), "every field must be part of the code"),
    (broken(code="GR-{module}-{teeth}-{face_width_mm}-{colour}"), "'{colour}' is not one of the fields"),
    (broken(fields={**GEAR["fields"], "teeth": {"type": "int", "min": 300, "max": 8}}), "min (300) is greater than max (8)"),
    (broken(fields={**GEAR["fields"], "material": {"type": "choice", "dictionary": "metals"}}), "dictionary 'metals' is not defined"),
    (broken(fields={**GEAR["fields"], "teeth": {"type": "integer"}}), "type must be one of"),
    (broken(fields={**GEAR["fields"], "teeth": {"type": "int", "minimum": 3}}), "unknown setting 'minimum'"),
    (broken(description="Spur gear {module"), "has a brace that is not a placeholder"),
    (broken(description="Gear {material.grade}"), "the entries of 'material' have no 'grade'"),
    (broken(id="Spur Gear"), "small letters, digits and dashes"),
    (broken(label=None), "'label' is missing"),
    (broken(category="XX"), "category 'XX' is not defined"),
])
def test_template_errors_are_plain_and_name_the_file(data, message):
    with pytest.raises(DataError) as e:
        SpecTemplate(data, "packs/demo/spec-codes/gear.yaml", DICTS, {"GR"})
    assert message in str(e.value)
    assert str(e.value).startswith("packs/demo/spec-codes/gear.yaml")


def test_sequence_scheme_checks():
    with pytest.raises(DataError, match=r"exactly one \{seq:N\}"):
        SequenceScheme({"id": "x", "label": "X", "pattern": "P-{yyyy}"}, "pack.yaml")
    with pytest.raises(DataError, match="is not used in 'pattern'"):
        SequenceScheme({"id": "x", "label": "X", "pattern": "P-{seq:3}",
                        "fields": {"product": {"type": "text"}}}, "pack.yaml")
    s = SequenceScheme({"id": "ga", "label": "GA", "pattern": "{product}-GA-{seq:3}",
                        "fields": {"product": {"type": "text", "pattern": "^[A-Z0-9]{1,8}$"}}}, "pack.yaml")
    before, after, _, errors = s.parts({"product": "mx"}, None)
    assert (before, after, errors) == ("MX-GA-", "", {})
    assert s.parts({"product": "mixer-1"}, None)[3]["product"].startswith("Product 'MIXER-1' is not in the expected form")


# ------------------------------------------------------------------- fields --

def test_field_values_are_checked_in_plain_words():
    teeth = FieldSpec("teeth", {"type": "int", "min": 8, "max": 300}, "t")
    assert teeth.parse("40") == 40 and teeth.parse(40.0) == 40
    for raw, msg in (("40.5", "must be a whole number"), ("4", "between 8 and 300"), ("", "enter a value"),
                     ("forty", "must be a number")):
        with pytest.raises(FieldError, match=msg):
            teeth.parse(raw)
    module = FieldSpec("module", {"type": "decimal", "unit": "mm", "choices": [1, 1.5, 2]}, "t")
    assert module.parse("1,5") == Decimal("1.5") and module.parse("2.00") == Decimal("2")
    with pytest.raises(FieldError, match=r"Module must be one of 1, 1.5, 2 mm."):
        module.parse("1.7")
    dia = FieldSpec("diameter_mm", {"type": "decimal", "min": 3, "places": 1}, "t")
    assert dia.label == "Diameter"
    with pytest.raises(FieldError, match="at most 1 decimal place"):
        dia.parse("12.25")


# ------------------------------------------------------------- code building --

def test_spec_codes_from_the_general_machinery_pack():
    cfg = config_of("general-machinery")
    svc = NumberingService(cfg)
    gear = svc.scheme("spec:spur-gear")
    r = gear.build({"module": "2.0", "teeth": "40", "face_width_mm": 30, "material": "c45"}, cfg)
    assert r.ok and r.code == "GR-2-40-30-C45"
    assert r.description == "Spur gear m2 z40 b30 C45 (1.0503)"
    assert r.item == {"material": "C45 (1.0503)"}
    assert gear.build({"module": "2", "teeth": 40, "face_width_mm": 30, "material": "C45 (1.0503)"}, cfg).code == r.code
    bad = gear.build({"module": "7", "teeth": 3, "face_width_mm": 30, "material": "brass"}, cfg)
    assert not bad.ok and set(bad.errors) == {"module", "teeth", "material"}
    assert "not in the materials list" in bad.errors["material"]
    shaft = svc.scheme("spec:shaft").build({"diameter_mm": "50", "length_mm": 500, "material": "42CrMo4"}, cfg)
    assert shaft.code == "SH-50X500-42CRMO4" and shaft.description.startswith("Shaft Ø50 x 500 42CrMo4")
    flange = svc.scheme("spec:flange").build({"dn": 150, "pn": 16, "type": "EN11", "material": "S235JR"}, cfg)
    assert flange.code == "FL-DN150-PN16-EN11-S235JR"
    assert "weld-neck" in flange.description
    fe_cfg = config_of("spares-and-service")
    fe = NumberingService(fe_cfg).scheme("spec:filter-element").build(
        {"media": "GF", "micron": 10, "od_mm": 90, "length_mm": 250}, fe_cfg)
    assert fe.code == "FE-GF-10-90X250" and fe.description == "Filter element Glass fibre 10 µm Ø90 x 250"


# ------------------------------------------------- allocation and duplicates --

def _install(*ids):
    for pid in ids:
        PackManager().install(pid, None)


def test_same_spec_code_gives_the_same_item(kdb):
    _install("general-machinery")
    svc = NumberingService()
    values = {"module": "2", "teeth": 40, "face_width_mm": 30, "material": "C45"}
    first = svc.allocate("spec:spur-gear", values, None)
    assert not first["existing"] and first["item"]["part_number"] == "GR-2-40-30-C45"
    assert first["item"]["material"] == "C45 (1.0503)" and first["item"]["make_buy"] == "make"
    again = svc.allocate("spec:spur-gear", {**values, "module": "2.0", "material": "c45"}, None)
    assert again["existing"] and again["item"]["id"] == first["item"]["id"]
    assert db.scalar("SELECT COUNT(*) FROM items WHERE part_number LIKE 'GR-%'") == 1
    # an item typed in by hand earlier is found too, and recorded in the ledger
    with db.tx():
        boms.ensure_item("GR-3-20-25-C45", "OLD GEAR", None)
    found = svc.allocate("spec:spur-gear", {"module": 3, "teeth": 20, "face_width_mm": 25, "material": "C45"}, None)
    assert found["existing"] and found["item"]["description"] == "OLD GEAR"
    assert db.scalar("SELECT item_id FROM number_allocations WHERE part_number = 'GR-3-20-25-C45'") == found["item"]["id"]
    preview = svc.preview("spec:spur-gear", values)
    assert preview["existing"]["id"] == first["item"]["id"]
    with pytest.raises(NumberingError) as e:
        svc.allocate("spec:spur-gear", {**values, "teeth": 2}, None)
    assert "teeth" in e.value.errors


def test_series_numbers_skip_taken_ones_and_share_the_cad_counter(kdb):
    _install("general-machinery")
    svc = NumberingService()
    with db.tx():
        boms.ensure_item("MX-5-001", "EXISTING", None)
    assert svc.preview("prefix", {"prefix": "mx-5-"})["part_number"] == "MX-5-002"
    a = svc.allocate("prefix", {"prefix": "MX-5-"}, None, "ROTOR")
    assert a["part_number"] == "MX-5-002" and a["item"]["description"] == "ROTOR"
    assert svc.next_in_series("MX-5-", 3, None) == "MX-5-003"          # the CAD add-ins' series
    assert svc.allocate("prefix", {"prefix": "MX-5-"}, None)["part_number"] == "MX-5-004"
    assert svc.allocate("category", {"category": "GR"}, None)["part_number"] == "GR-000001"
    proj = svc.allocate("project", {}, None, "Customer job")
    assert proj["item"] is None and proj["part_number"].startswith("P-20") and proj["part_number"].endswith("-001")
    assert not db.scalar("SELECT 1 FROM items WHERE part_number = ?", (proj["part_number"],))
    ga = svc.allocate("general-arrangement", {"product": "mx"}, None)
    assert ga["part_number"] == "MX-GA-001" and ga["item"]["item_type"] == "assembly"
    with pytest.raises(NumberingError, match="no number scheme 'nope'"):
        svc.allocate("nope", {}, None)


def test_concurrent_allocation_never_duplicates(kdb):
    _install("general-machinery")
    results, errors = [], []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def worker(n):
        try:
            start.wait()
            svc = NumberingService()
            for i in range(15):
                if i % 5 == 4:
                    pn = svc.next_in_series("CC-1-", 3, None)            # the CAD add-ins' way, interleaved
                else:
                    pn = svc.allocate("prefix", {"prefix": "CC-1-"}, None, f"T{n}-{i}")["part_number"]
                gear = svc.allocate("spec:spur-gear", {"module": 1, "teeth": 8 + n % 2, "face_width_mm": 10,
                                                       "material": "C45"}, None)
                with lock:
                    results.append((pn, gear["item"]["id"], gear["part_number"]))
        except Exception as e:  # pragma: no cover - reported below
            errors.append(repr(e))
        finally:
            db.close_thread_connection()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    numbers = [r[0] for r in results]
    assert len(numbers) == 120 and len(set(numbers)) == 120
    assert sorted(numbers) == [f"CC-1-{n:03d}" for n in range(1, 121)]       # no gaps, no repeats
    gear_ids = {}
    for _pn, iid, code in results:
        gear_ids.setdefault(code, set()).add(iid)
    assert set(gear_ids) == {"GR-1-8-10-C45", "GR-1-9-10-C45"}
    assert all(len(ids) == 1 for ids in gear_ids.values())                    # one item per spec code
    assert db.scalar("SELECT COUNT(*) FROM number_allocations WHERE part_number LIKE 'CC-1-%'") == 120


# ------------------------------------------- number formats, reservations, voiding, capacity --

def test_spec_number_formats(kdb):
    _install("general-machinery")
    svc = NumberingService()
    assert svc.allocate("category", {"category": "SH"}, None)["part_number"] == "SH-000001"
    with db.tx():                                  # a series started before 6 digits keeps its width
        db.execute("INSERT INTO numbering(prefix, next_value, width) VALUES ('FL-', 13, 5)")
    assert svc.allocate("category", {"category": "FL"}, None)["part_number"] == "FL-00013"
    assert svc.allocate("document", {}, None, "Pump test procedure")["part_number"] == "DOC-000001"
    assert svc.allocate("kit", {}, None, "Shutdown kit")["part_number"] == "KIT-000001"
    yy = f"{datetime.now(timezone.utc).year % 100:02d}"
    build = svc.allocate("machine-build", {"family": "mx"}, None, "Mixer for an order")
    assert build["part_number"] == f"MX{yy}-0001" and build["item"]["description"] == "Mixer for an order"
    with pytest.raises(NumberingError):
        svc.allocate("machine-build", {"family": "MIX"}, None)


def test_change_orders_carry_the_year(kdb):
    from kosha import changes
    yy = f"{datetime.now(timezone.utc).year % 100:02d}"
    with db.tx():
        db.insert("change_orders", {"number": "ECO-0007", "title": "older style", "created_at": db.now()})
    assert changes._next_number() == f"ECO-{yy}-0001"
    with db.tx():
        db.insert("change_orders", {"number": f"ECO-{yy}-0041", "title": "t", "created_at": db.now()})
    assert changes._next_number() == f"ECO-{yy}-0042"


def test_bulk_reservation_and_voiding_unused_numbers(kdb):
    _install("general-machinery")
    svc = NumberingService()
    r = svc.reserve("prefix", {"prefix": "RV-"}, 4, None, "copy of MX-100")
    assert r["numbers"] == ["RV-001", "RV-002", "RV-003", "RV-004"]
    assert not db.scalar("SELECT 1 FROM items WHERE part_number LIKE 'RV-%'")      # numbers only, no parts yet
    with pytest.raises(NumberingError, match="between 1 and 500"):
        svc.reserve("prefix", {"prefix": "RV-"}, 501, None)
    with pytest.raises(NumberingError, match="cannot be reserved"):
        svc.reserve("spec:spur-gear", {}, 2, None)
    project = svc.allocate("project", {}, None, "Customer job")["part_number"]
    with db.tx():
        boms.ensure_item("RV-002", "USED FOR A PART", None)                         # something was made from it
    assert svc.void_unused(now=datetime.now(timezone.utc)) == 0                     # not 30 days yet
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert svc.void_unused(now=later) == 3
    voided = {r["part_number"] for r in db.rows("SELECT part_number FROM number_allocations WHERE voided_at IS NOT NULL")}
    assert voided == {"RV-001", "RV-003", "RV-004"} and project not in voided
    assert svc.void_unused(now=later) == 0                                          # once only
    assert svc.allocate("prefix", {"prefix": "RV-"}, None)["part_number"] == "RV-005"  # never reused
    assert "voided after 30 days" in db.scalar("SELECT details FROM audit WHERE action = 'number_void'")


def test_near_matches_are_offered_before_a_new_spec_code(kdb):
    _install("general-machinery")
    svc = NumberingService()
    values = {"module": "2", "teeth": 40, "face_width_mm": 30, "material": "C45"}
    first = svc.allocate("spec:spur-gear", values, None)
    assert first["existing_matches"] == []
    svc.allocate("spec:spur-gear", {**values, "module": "3", "teeth": 20}, None)     # two values differ: not near
    p = svc.preview("spec:spur-gear", {**values, "teeth": 41})
    assert p["existing"] is None and [n["part_number"] for n in p["near"]] == ["GR-2-40-30-C45"]
    assert p["near"][0]["differs"] == "Teeth: 40 instead of 41" and "differ in one value" in p["message"]
    again = svc.allocate("spec:spur-gear", values, None)
    assert [m["part_number"] for m in again["existing_matches"]] == ["GR-2-40-30-C45"]


def test_a_series_warns_at_80_percent_once(kdb):
    _install("general-machinery")
    svc = NumberingService()
    with db.tx():
        db.execute("INSERT INTO numbering(prefix, next_value, width) VALUES ('ZZ-', 700, 3)")
        db.insert("users", {"username": "boss", "full_name": "Boss", "email": "", "role": "admin",
                            "password_hash": "x", "created_at": db.now()})
    assert svc.allocate("prefix", {"prefix": "ZZ-"}, None)["warning"] == ""
    with db.tx():
        db.execute("UPDATE numbering SET next_value = 820 WHERE prefix = 'ZZ-'")
    w = svc.allocate("prefix", {"prefix": "ZZ-"}, None)["warning"]
    assert "ZZ-NNN has used 820 of its 999 numbers (82%)" in w
    svc.allocate("prefix", {"prefix": "ZZ-"}, None)
    assert db.scalar("SELECT COUNT(*) FROM audit WHERE action = 'number_capacity'") == 1
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE title LIKE 'Number series ZZ-NNN%'") == 1
    cap = {c["series"]: c for c in NumberingService.capacity()}
    assert cap["ZZ-NNN"]["warning"] and cap["ZZ-NNN"]["used"] == 821
