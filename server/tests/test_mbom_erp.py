# SPDX-License-Identifier: AGPL-3.0-or-later
"""Manufacturing BOMs and the ERP, against the simulated ERPNext: a planned assembly reaches the ERP as its
manufacturing BOM with its operations, a sub-assembly nobody planned still goes as designed, and the reconciliation
compares each with what the ERP should hold."""
import pytest

from erp_helpers import drain_erp, folder, released_bom, released_part, run_check, serve

import fake_erpnext as FE  # noqa: E402  (found through erp_helpers)


@pytest.fixture(scope="module")
def erpnext():
    FE.STATE.update({"items": {}, "boms": {}, "bins": {}, "calls": []})
    server, url = serve(FE.app)
    yield url
    server.should_exit = True


def _default_bom(code):
    return next(b for b in FE.STATE["boms"].values() if b["item"] == code and b.get("is_default"))


def test_planned_assembly_reaches_the_erp_as_built(kosha, erpnext):
    admin = kosha.client()
    admin.call("PUT", "/api/erp/settings", {"enabled": True, "dry_run": False, "base_url": erpnext, "api_key": FE.KEY,
                                            "api_secret": FE.SECRET, "bom_source": "mbom", "send_operations": True})
    f = folder(admin, "Pumps")
    item = {pn: released_part(admin, f, pn, desc)["item"]["id"] for pn, desc in (
        ("PM-100-001", "CASING"), ("PM-100-002", "IMPELLER"), ("PM-100-011", "SHAFT"), ("PM-100-012", "BEARING"),
        ("PM-100-010", "ROTOR"), ("PM-100-000", "PUMP ASSY"))}
    released_bom(admin, item["PM-100-010"], [(item["PM-100-011"], 1), (item["PM-100-012"], 2)])
    top = admin.call("POST", "/api/boms", {"item_id": item["PM-100-000"]})["bom_id"]
    admin.call("PUT", f"/api/boms/{top}/lines", {"lines": [{"item_id": item[pn], "qty": 1} for pn in (
        "PM-100-001", "PM-100-002", "PM-100-010")]})
    mid = admin.call("POST", "/api/mboms", {"item_id": item["PM-100-000"]})["mbom_id"]   # planned before release
    admin.call("POST", f"/api/boms/{top}/release")
    d = admin.call("PUT", f"/api/mboms/{mid}/plan", {"ops": [
        {"op_no": 10, "work_centre": "Bench 1", "description": "Fit impeller", "run_min": 20},
        {"op_no": 20, "work_centre": "Bench 2", "description": "Close casing", "run_min": 30}]})
    admin.call("POST", f"/api/mboms/{mid}/flatten", {"item_id": item["PM-100-010"]})
    d = admin.call("GET", f"/api/mboms/{mid}")
    admin.call("PUT", f"/api/mboms/{mid}/plan", {"lines": [
        {"item_id": ln["item_id"], "qty": ln["qty"], "op_no": 20 if ln["part_number"] == "PM-100-001" else 10}
        for ln in d["lines"]]})
    admin.call("POST", f"/api/mboms/{mid}/release")
    jobs = drain_erp(admin)
    assert all(j["status"] == "sent" for j in jobs), [j for j in jobs if j["status"] != "sent"]
    assert not any(j["kind"] == "bom" and j["entity_id"] == top for j in jobs)    # the design's BOM is not sent

    pump = _default_bom("PM-100-000")
    assert sorted((i["item_code"], i["qty"], i.get("operation")) for i in pump["items"]) == [
        ("PM-100-001", 1, "Close casing"), ("PM-100-002", 1, "Fit impeller"), ("PM-100-011", 1, "Fit impeller"),
        ("PM-100-012", 2, "Fit impeller")]
    assert [o["workstation"] for o in pump["operations"]] == ["Bench 1", "Bench 2"]
    rotor = _default_bom("PM-100-010")                       # nobody planned the rotor: it goes as designed
    assert sorted((i["item_code"], i["qty"]) for i in rotor["items"]) == [("PM-100-011", 1), ("PM-100-012", 2)]

    d = run_check(admin)
    assert d["run"]["status"] == "done" and d["run"]["differences"] == 0, d["rows"]

    # someone changes the pump's BOM in the ERP: the difference is against the manufacturing BOM, and sending again
    # sends the manufacturing BOM
    pump["items"] = [i for i in pump["items"] if i["item_code"] != "PM-100-012"]
    d = run_check(admin)
    row = next(r for r in d["rows"] if r["check_code"] == "bom_line_missing")
    assert row["part_number"] == "PM-100-000" and row["fix_kind"] == "mbom" and row["fix_entity_id"] == mid
    admin.call("PUT", "/api/erp/settings", {"bom_source": "ebom", "enabled": False})
