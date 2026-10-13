# SPDX-License-Identifier: AGPL-3.0-or-later
"""Change-log drafts without AI: fixed sections, built from measured facts only."""
from kosha.diff.changelog import SECTIONS, draft, input_hash


def change(text, kind="dimension", severity="major"):
    return {"kind": kind, "severity": severity, "text": text}


def doc_input(changes, used=(), stock=None):
    return {
        "kind": "document",
        "subject": {"id": 1, "label": "MX-100-020 Agitator Shaft.dxf", "revision": "D"},
        "groups": [{"document": {"id": 1, "filename": "MX-100-020 Agitator Shaft.dxf"}, "revision": "D",
                    "from": {"version": 3, "revision": "C", "label": "revision C (v3)"},
                    "to": {"version": 4, "revision": "D", "label": "revision D (v4)"},
                    "changes": changes, "notes": []}],
        "where_used": list(used),
        "stock": [{"part_number": "MX-100-020", "stock": stock, "uom": "Nos", "synced_at": None}],
        "reason": "", "erp_enabled": True,
    }


USED = [{"part_number": "MX-100-000", "description": "AGITATOR ASSEMBLY", "bom_revision": "A", "bom_state": "released",
         "qty": 1, "child": "MX-100-020"}]


def test_draft_has_the_four_sections_in_order_built_from_facts_and_note():
    inp = doc_input([change("Diameter Ø110 h6 changed to Ø115 h6"),
                     change("Material changed from C45 to 42CrMo4", "attribute"),
                     change("Revision changed from C to D", "attribute", "info")], USED, stock=6)
    d = draft(inp, "Field report: fretting at the hub seat.")
    assert list(d) == [k for k, _ in SECTIONS] == ["what", "why", "effect", "todo"]
    assert [label for _, label in SECTIONS] == ["What changed", "Why", "Effect on parts in stock and work in progress",
                                                "What to do"]
    assert d["what"].splitlines() == ["Compared with revision C (v3):", "- Diameter Ø110 h6 changed to Ø115 h6",
                                      "- Material changed from C45 to 42CrMo4", "- Revision changed from C to D"]
    assert d["why"] == "Field report: fretting at the hub seat."
    assert "- MX-100-000 AGITATOR ASSEMBLY, BOM revision A (released), qty 1" in d["effect"]
    assert "Stock of MX-100-020 in the ERP: 6 Nos." in d["effect"]
    todo = d["todo"].splitlines()
    assert todo[0] == ("- Replace printed copies of MX-100-020 Agitator Shaft.dxf revision C with revision D once it "
                       "is released.")
    assert "- Check the assemblies that use it: MX-100-000." in todo
    assert "- Decide what happens to the 6 Nos of MX-100-020 in stock: use as is, rework or scrap." in todo
    assert "- Kosha sends the new revision to the ERP when it is released." in todo


def test_draft_without_a_note_asks_for_the_reason_and_says_what_is_unknown():
    d = draft(doc_input([change("2 holes Ø18 added on layer 0 (8 → 10)", "holes")]))
    assert d["why"].startswith("Reason not given yet.")
    assert "Not used in any assembly BOM in Kosha." in d["effect"]
    assert "not known (not read from the ERP yet)" in d["effect"]
    assert "- Decide what happens to parts in stock and in production: use as is, rework or scrap." in d["todo"]


def test_change_order_draft_lists_each_file_and_uses_the_reason():
    inp = doc_input([change("Diameter Ø110 changed to Ø115")], USED)
    inp["kind"] = "change_order"
    inp["reason"] = "Field report: fretting at impeller hub seat"
    inp["description"] = "Increase the hub seat."
    inp["groups"].append({"document": {"id": 2, "filename": "MX-100-010 Impeller Disc.dxf"}, "revision": "B",
                          "from": None, "to": None, "changes": [], "notes": ["No version of revision B has been checked in yet."]})
    d = draft(inp)
    lines = d["what"].splitlines()
    assert lines[0] == "MX-100-020 Agitator Shaft.dxf, revision C (v3) → revision D (v4):"
    assert "MX-100-010 Impeller Disc.dxf, revision B:" in lines
    assert "- No version of revision B has been checked in yet." in lines
    assert d["why"] == "Field report: fretting at impeller hub seat\n\nIncrease the hub seat."
    assert "qty 1 of MX-100-020" in d["effect"]


def test_long_lists_are_cut_and_empty_ones_say_so():
    d = draft(doc_input([change(f"Dimension {k} changed to {k + 1}") for k in range(20)]))
    assert d["what"].count("\n- Dimension") == 15 and d["what"].endswith("- … and 5 more measured changes (see the change list).")
    empty = draft(doc_input([]))
    assert "- No measured differences." in empty["what"]
    assert empty["todo"] == "- Nothing measurable has changed yet: check that the changed files are checked in."


def test_input_hash_follows_the_facts_not_the_text():
    a = doc_input([change("Diameter Ø110 changed to Ø115")], USED)
    b = doc_input([change("Diameter Ø110 changed to Ø115")], USED)
    b["erp_enabled"] = False
    assert input_hash(a) == input_hash(b)
    b["groups"][0]["changes"].append(change("Material changed from C45 to 42CrMo4"))
    assert input_hash(a) != input_hash(b)
