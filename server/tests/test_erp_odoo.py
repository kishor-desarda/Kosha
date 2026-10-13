# SPDX-License-Identifier: AGPL-3.0-or-later
"""A second ERP behind the same interface: the admin chooses the Odoo connector, Kosha pushes items and BOMs to
the simulated Odoo (respecting dry run), reads stock and cost back, and reconciles."""
import pytest

from erp_helpers import drain_erp, folder, released_bom, released_part, run_check, serve

import fake_odoo as FO  # noqa: E402  (found through erp_helpers)

ODOO = "org.kosha.odoo"


@pytest.fixture(scope="module")
def odoo():
    FO.reset()
    server, url = serve(FO.app)
    yield url
    server.should_exit = True


def _product(code):
    return next((p for p in FO.STATE["product.product"].values() if p.get("default_code") == code), None)


def test_odoo_as_the_erp(kosha, odoo):
    admin = kosha.client()
    cfg = admin.call("GET", "/api/erp/settings")
    assert cfg["connector"] == "org.kosha.erpnext"                   # the first one stays the default
    assert {c["id"] for c in cfg["connectors"]} >= {"org.kosha.erpnext", ODOO}
    r = admin.call("PUT", "/api/erp/settings", {"connector": "org.kosha.nothing"}, expect=400)
    assert "installed" in r["error"]

    cfg = admin.call("PUT", "/api/erp/settings", {"connector": ODOO})
    assert cfg["connector"] == ODOO and cfg["connector_name"] == "Odoo"
    assert "database" in {f["key"] for f in cfg["connector_fields"]}
    assert admin.call("GET", "/api/auth/me")["erp_name"] == "Odoo"
    cfg = admin.call("PUT", "/api/erp/settings", {
        "enabled": True, "dry_run": True, "base_url": odoo, "database": FO.DB, "username": FO.LOGIN, "api_key": FO.KEY,
        "revision_field": "x_kosha_revision", "material_field": "x_kosha_material",
        "uom_map": {"NOS": "Units", "EA": "Units", "KG": "kg"}})
    assert cfg["api_key"] != FO.KEY                                  # the key is masked
    erpnext = admin.call("GET", "/api/addins/org.kosha.erpnext")["settings"]
    assert not erpnext.get("base_url")                               # the ERPNext settings were not touched
    t = admin.call("POST", "/api/erp/test")
    assert t["ok"] and "Odoo 17.0" in t["message"], t

    f = folder(admin, "Conveyors")
    parts = {pn: released_part(admin, f, pn, desc, mat) for pn, desc, mat in (
        ("DE-300-001", "ROLLER", "S235"), ("DE-300-002", "IDLER", ""), ("DE-300-000", "CONVEYOR ASSY", ""))}
    item = {pn: p["item"]["id"] for pn, p in parts.items()}
    released_bom(admin, item["DE-300-000"], [(item["DE-300-001"], 6), (item["DE-300-002"], 2)])
    jobs = drain_erp(admin)
    assert {j["status"] for j in jobs} == {"dry_run"} and not FO.STATE["product.product"]   # dry run: nothing sent

    admin.call("PUT", "/api/erp/settings", {"dry_run": False})
    for j in jobs:
        admin.call("POST", f"/api/erp/jobs/{j['id']}/retry")
    jobs = drain_erp(admin)
    assert all(j["status"] == "sent" for j in jobs), jobs
    roller = _product("DE-300-001")
    assert roller["name"] == "ROLLER" and roller["x_kosha_material"] == "S235" and roller["uom_id"] == 1
    bom = next(iter(FO.STATE["mrp.bom"].values()))
    assert bom["code"].startswith("Rev ") and len([ln for ln in FO.STATE["mrp.bom.line"].values()
                                                   if ln["bom_id"] == bom["id"]]) == 2
    r = admin.call("POST", f"/api/boms/{admin.call('GET', '/api/boms')['boms'][0]['id']}/push")
    assert "unchanged" in r["job"]["response_text"], r                # the same BOM again changes nothing

    FO.add_stock("DE-300-001", 12)
    roller["standard_price"] = 3.5
    r = admin.call("POST", "/api/erp/push", {"kind": "refresh", "id": item["DE-300-001"]})
    assert r["job"]["status"] == "sent"
    it = admin.call("GET", f"/api/items/{item['DE-300-001']}")["item"]
    assert it["erp_stock"] == 12 and it["erp_cost"] == 3.5

    d = run_check(admin)
    assert d["run"]["status"] == "done" and d["run"]["differences"] == 0, d["rows"]
    assert d["run"]["connector_name"] == "Odoo"

    roller["name"] = "ROLLER (CHANGED IN ODOO)"
    _product("DE-300-002")["active"] = False
    d = run_check(admin)
    rows = {(r["part_number"], r["check_code"]): r for r in d["rows"]}
    assert set(rows) == {("DE-300-001", "description"), ("DE-300-002", "inactive_in_erp")}, sorted(rows)
    admin.call("POST", f"/api/erp/reconcile/rows/{rows[('DE-300-001', 'description')]['id']}/resend")
    drain_erp(admin)
    assert _product("DE-300-001")["name"] == "ROLLER"
