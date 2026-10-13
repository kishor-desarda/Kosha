# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measured changes and change logs through the API, on the Demo Engineering data.

The demo agitator shaft MX-100-020 has released revisions A, B and C (hub seat Ø100 → Ø105 → Ø110,
material C45 → 42CrMo4) and is at revision D in work for the first change order (ECO-YY-0001).
"""
import json

import pytest

from kosha.cad import dxfwriter as W
from kosha.cad import mesh as M

SHAFT_L = (60, 80, 220, 80, 80)


def tb(rev, mat="42CrMo4"):
    return {"PART_NO": "MX-100-020", "DESCRIPTION": "AGITATOR SHAFT", "MATERIAL": mat, "REV": rev}


@pytest.fixture(scope="module")
def ctx(demo_kosha):
    asha = demo_kosha.client("asha")
    shaft = next(d for d in asha.call("GET", "/api/search?q=MX-100-020")["documents"] if d["ext"] == "dxf")
    return {"asha": asha, "daniel": demo_kosha.client("daniel"), "tom": demo_kosha.client("tom"), "shaft": shaft["id"]}


def texts(r):
    return [c["text"] for c in r["changes"]]


def test_default_compares_latest_version_with_previous_released_revision(ctx):
    r = ctx["asha"].call("GET", f"/api/documents/{ctx['shaft']}/changes")
    assert (r["from"]["revision"], r["from"]["version"], r["to"]["revision"], r["to"]["version"]) == ("B", 2, "C", 3)
    assert r["basis"] == "previous released revision"
    assert "Diameter Ø105 changed to Ø110" in texts(r)
    dim = next(c for c in r["changes"] if c["kind"] == "dimension")
    assert dim["severity"] == "major" and len(dim["zone"]) == 4 and dim["before"] == "Ø105"
    assert r["counts"]["major"] >= 1 and "dxf" in r["file_types"]


def test_versions_or_revisions_can_be_chosen(ctx):
    a = ctx["tom"].call("GET", f"/api/documents/{ctx['shaft']}/changes?from=A&to=C")
    b = ctx["tom"].call("GET", f"/api/documents/{ctx['shaft']}/changes?from=v1&to=v3")
    assert texts(a) == texts(b)
    assert {"Material changed from C45 to 42CrMo4", "Diameter Ø100 changed to Ø110", "Revision changed from A to C"} <= set(texts(a))
    assert texts(a).count("Diameter Ø90 changed to Ø95") == 2
    ctx["tom"].call("GET", f"/api/documents/{ctx['shaft']}/changes?from=Q", expect=404)
    ctx["tom"].call("GET", f"/api/documents/{ctx['shaft']}/changes?to=v9", expect=404)
    first = ctx["tom"].call("GET", f"/api/documents/{ctx['shaft']}/changes?to=v1")
    assert first["changes"] == [] and "first version" in first["notes"][0]


def test_change_log_of_a_revision_without_a_version_says_so(ctx):
    r = ctx["asha"].call("GET", f"/api/documents/{ctx['shaft']}/changelog")
    assert r["revision"] == "D" and r["saved"] is None and r["can_edit"]["ok"]
    assert [s["key"] for s in r["sections"]] == ["what", "why", "effect", "todo"]
    assert "Revision D has no checked-in version yet" in r["draft"]["what"]
    assert "MX-100-000" in r["draft"]["effect"]
    assert r["revisions"][:2] == ["D", "C"]


def test_change_log_for_revision_d_after_check_in(ctx):
    asha, shaft = ctx["asha"], ctx["shaft"]
    asha.call("POST", f"/api/documents/{shaft}/checkout")
    dxf = W.shaft(SHAFT_L, (80, 95, 115, 95, 80), values=tb("D"))
    d = asha.call("POST", f"/api/documents/{shaft}/checkin", files={"file": ("x.dxf", dxf)}, fields={"comment": "Hub seat Ø115"})
    assert d["version"] == 4 and d["revision"] == "D"
    ch = asha.call("GET", f"/api/documents/{shaft}/changes")
    assert ch["from"]["revision"] == "C" and ch["to"]["revision"] == "D"
    assert "Diameter Ø110 changed to Ø115" in texts(ch)
    assert "Revision changed from C to D" in texts(ch)

    r = asha.call("GET", f"/api/documents/{shaft}/changelog")
    assert r["draft"]["what"].startswith("Compared with revision C (v3):\n- Diameter Ø110 changed to Ø115")
    assert r["draft"]["why"].startswith("Reason not given yet")
    drafted = asha.call("POST", f"/api/documents/{shaft}/changelog/draft", {"note": "Fretting at the impeller hub seat."})["draft"]
    assert drafted["why"] == "Fretting at the impeller hub seat."
    assert "Replace printed copies of MX-100-020 Agitator Shaft.dxf revision C with revision D" in drafted["todo"]

    saved = asha.call("PUT", f"/api/documents/{shaft}/changelog", {"note": "Fretting at the impeller hub seat.", "sections": drafted})
    assert saved["saved"]["source"] == "rules" and saved["saved"]["updated_by"] == "Asha Menon"
    assert saved["saved"]["sections"] == drafted and saved["saved"]["stale"] is False
    edited = dict(drafted, what=drafted["what"] + "\n- Keyway position checked against the hub.")
    saved = asha.call("PUT", f"/api/documents/{shaft}/changelog", {"note": "Fretting at the impeller hub seat.", "sections": edited})
    assert saved["saved"]["source"] == "edited" and saved["saved"]["sections"]["what"].endswith("checked against the hub.")
    # the facts are stored apart from the text, and the rule draft is kept for a later AI draft to compare with
    assert saved["input"]["groups"][0]["changes"][0]["text"] == "Diameter Ø110 changed to Ø115"
    asha.call("PUT", f"/api/documents/{shaft}/changelog", {"note": "x", "sections": {"what": " "}}, expect=400)

    # a new version changes the measured facts: the saved change log is marked as out of date
    asha.call("POST", f"/api/documents/{shaft}/checkout")
    asha.call("POST", f"/api/documents/{shaft}/checkin",
              files={"file": ("x.dxf", W.shaft(SHAFT_L, (80, 95, 116, 95, 80), values=tb("D")))})
    again = asha.call("GET", f"/api/documents/{shaft}/changelog")
    assert again["saved"]["stale"] is True and "Ø116" in again["draft"]["what"]


def test_change_log_permissions(ctx):
    shaft = ctx["shaft"]
    tom = ctx["tom"].call("GET", f"/api/documents/{shaft}/changelog")
    assert tom["can_edit"] == {"ok": False, "why": "You have view-only access."}
    ctx["tom"].call("PUT", f"/api/documents/{shaft}/changelog", {"sections": {"what": "x"}}, expect=403)
    released = ctx["asha"].call("GET", f"/api/documents/{shaft}/changelog?revision=C")
    assert not released["can_edit"]["ok"] and "only an approver" in released["can_edit"]["why"]
    assert released["draft"]["what"].startswith("Compared with revision B (v2):")
    ctx["asha"].call("PUT", f"/api/documents/{shaft}/changelog?revision=C", {"sections": {"what": "x"}}, expect=403)
    r = ctx["daniel"].call("PUT", f"/api/documents/{shaft}/changelog?revision=C",
                           {"note": "Hub seat enlarged", "sections": released["draft"]})
    assert r["revision"] == "C" and r["saved"]["updated_by"] == "Daniel Okafor"
    ctx["asha"].call("GET", f"/api/documents/{shaft}/changelog?revision=K", expect=400)


def test_change_order_change_log(ctx):
    asha = ctx["asha"]
    co = next(c for c in asha.call("GET", "/api/changes")["changes"] if c["number"].endswith("-0001"))
    r = asha.call("GET", f"/api/changes/{co['id']}/changelog")
    groups = {g["document"]["filename"]: g for g in r["input"]["groups"]}
    shaft = groups["MX-100-020 Agitator Shaft.dxf"]
    assert (shaft["from"]["revision"], shaft["to"]["revision"]) == ("C", "D")
    assert any(c["text"].startswith("Diameter Ø110 changed to Ø11") for c in shaft["changes"])
    assert groups["MX-100-010 Impeller Disc.dxf"]["notes"] == ["No version of revision B has been checked in yet."]
    assert r["draft"]["why"].startswith("Field report: fretting at impeller hub seat")
    assert "MX-100-020 Agitator Shaft.dxf, revision C (v3) → revision D (v5):" in r["draft"]["what"]
    assert r["can_edit"]["ok"]
    note = asha.call("POST", f"/api/changes/{co['id']}/changelog/draft", {"note": "Hub seat to Ø115 h6."})["draft"]
    assert note["why"] == "Hub seat to Ø115 h6."
    saved = asha.call("PUT", f"/api/changes/{co['id']}/changelog", {"note": "Hub seat to Ø115 h6.", "sections": note})
    assert saved["saved"]["note"] == "Hub seat to Ø115 h6." and saved["subject"] == "change_order"
    ctx["tom"].call("PUT", f"/api/changes/{co['id']}/changelog", {"sections": note}, expect=403)
    assert ctx["tom"].call("GET", f"/api/changes/{co['id']}/changelog")["saved"]["sections"] == note


def test_released_revision_properties_are_kept_and_compared(ctx):
    """Item properties are copied at each release, so a later change to the part shows up as a measured change."""
    daniel, shaft = ctx["daniel"], ctx["shaft"]
    item = daniel.call("GET", f"/api/documents/{shaft}")["item"]
    daniel.call("PATCH", f"/api/items/{item['id']}", {"material": "34CrNiMo6"})
    r = daniel.call("GET", f"/api/documents/{shaft}/changes")
    prop = [c for c in r["changes"] if c["kind"] == "property" and c["detail"]["label"] == "Material"]
    assert prop and prop[0]["after"] == "34CrNiMo6" and prop[0]["source"] == "properties"


def test_cad_addin_properties_mass_and_size(demo_kosha):
    eng = demo_kosha.client("asha")

    def checkin(mat, mass, height):
        stl = M.write_stl(M.box_mesh(200, 100, height))
        return eng.call("POST", "/api/addin/checkin",
                        files={"file": ("MX-400-010 Bracket.SLDPRT", b"sw-part-" + mat.encode()), "mesh": ("m.stl", stl)},
                        fields={"folder": "Kosha/Products/Brackets",
                                "props": json.dumps({"PartNo": "MX-400-010", "Material": mat, "Mass": mass, "Finish": "Painted"})})

    d = checkin("C45", "12.5", 20)
    eng.call("POST", f"/api/documents/{d['id']}/checkout")
    checkin("42CrMo4", "13.1", 25)
    r = eng.call("GET", f"/api/documents/{d['id']}/changes?from=v1&to=v2")
    t = texts(r)
    assert "Custom property Material changed from C45 to 42CrMo4" in t
    assert "Mass changed from 12.5 kg to 13.1 kg (+4.8 %)" in t
    assert "Model size changed from 200 × 100 × 20 to 200 × 100 × 25 mm" in t
    assert any(x.startswith("Volume changed from 400 cm³ to 500 cm³") for x in t)
    assert r["comparers"][:1] == ["model"]
