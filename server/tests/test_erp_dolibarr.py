# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dolibarr behind the same interface: the admin chooses the Dolibarr connector, Kosha pushes released pump parts and
their BOM to the simulated Dolibarr (respecting dry run, children first), reads stock and cost back, and reconciles."""
import pytest

from erp_helpers import drain_erp, folder, released_bom, released_part, run_check, serve

import fake_dolibarr as FD  # noqa: E402  (found through erp_helpers)

DOLIBARR = "org.kosha.dolibarr"


@pytest.fixture(scope="module")
def dolibarr():
    FD.reset()
    server, url = serve(FD.app)
    yield url
    server.should_exit = True


def test_dolibarr_as_the_erp(kosha, dolibarr):
    admin = kosha.client()
    cfg = admin.call("GET", "/api/erp/settings")
    assert cfg["connector"] == "org.kosha.erpnext"                   # ERPNext stays the default until one is chosen
    assert {c["id"] for c in cfg["connectors"]} >= {"org.kosha.erpnext", DOLIBARR, "org.kosha.file-exchange"}
    assert next(c for c in cfg["connectors"] if c["id"] == DOLIBARR)["status"] == "enabled"

    cfg = admin.call("PUT", "/api/erp/settings", {"connector": DOLIBARR})
    assert cfg["connector"] == DOLIBARR and cfg["connector_name"] == "Dolibarr"
    fields = {f["key"]: f for f in cfg["connector_fields"]}
    assert fields["api_key"]["type"] == "secret" and fields["cost_source"]["choices"]
    assert admin.call("GET", "/api/auth/me")["erp_name"] == "Dolibarr"
    cfg = admin.call("PUT", "/api/erp/settings", {
        "enabled": True, "dry_run": True, "base_url": dolibarr, "api_key": FD.KEY, "use_units": True,
        "revision_field": "kosha_revision", "material_field": "kosha_material",
        "uom_map": {"NOS": "P", "EA": "P", "KG": "KG"}})
    assert cfg["api_key"] != FD.KEY                                  # the key is masked
    t = admin.call("POST", "/api/erp/test")
    assert t["ok"] and "Dolibarr 20.0.2" in t["message"] and FD.LOGIN in t["message"], t

    f = folder(admin, "Pumps")
    parts = {pn: released_part(admin, f, pn, desc, mat) for pn, desc, mat in (
        ("DE-400-001", "PUMP CASING", "EN-GJL-250"), ("DE-400-002", "IMPELLER", ""), ("DE-400-000", "PUMP ASSY", ""))}
    item = {pn: p["item"]["id"] for pn, p in parts.items()}
    released_bom(admin, item["DE-400-000"], [(item["DE-400-001"], 1), (item["DE-400-002"], 2)])
    jobs = drain_erp(admin)
    assert {j["status"] for j in jobs} == {"dry_run"} and not FD.STATE["products"]   # dry run: nothing sent

    admin.call("PUT", "/api/erp/settings", {"dry_run": False})
    for j in jobs:
        admin.call("POST", f"/api/erp/jobs/{j['id']}/retry")
    jobs = drain_erp(admin)
    assert all(j["status"] == "sent" for j in jobs), jobs
    casing = FD.product("DE-400-001")
    assert casing["label"] == "PUMP CASING" and casing["fk_unit"] == 1
    assert casing["array_options"] == {"options_kosha_revision": "A", "options_kosha_material": "EN-GJL-250"}
    (bom,) = FD.boms_of("DE-400-000")
    assert bom["status"] == 1 and bom["label"].startswith("Rev ") and FD.product("DE-400-000")["fk_default_bom"] == bom["id"]
    assert sorted(FD.lines_of(bom["id"])) == [("DE-400-001", 1.0), ("DE-400-002", 2.0)]
    r = admin.call("POST", f"/api/boms/{admin.call('GET', '/api/boms')['boms'][0]['id']}/push")
    assert "unchanged" in r["job"]["response_text"], r                # the same BOM again changes nothing
    assert len(FD.boms_of("DE-400-000")) == 1

    FD.add_stock("DE-400-001", 12)
    FD.add_stock("DE-400-001", 3, warehouse="2")
    casing["pmp"] = 41.5
    r = admin.call("POST", "/api/erp/push", {"kind": "refresh", "id": item["DE-400-001"]})
    assert r["job"]["status"] == "sent"
    it = admin.call("GET", f"/api/items/{item['DE-400-001']}")["item"]
    assert it["erp_stock"] == 15 and it["erp_cost"] == 41.5

    d = run_check(admin)
    assert d["run"]["status"] == "done" and d["run"]["differences"] == 0, d["rows"]
    assert d["run"]["connector_name"] == "Dolibarr"

    casing["label"] = "PUMP CASING (CHANGED IN DOLIBARR)"
    FD.product("DE-400-002").update(status="0", status_buy="0")
    d = run_check(admin)
    rows = {(r["part_number"], r["check_code"]): r for r in d["rows"]}
    assert set(rows) == {("DE-400-001", "description"), ("DE-400-002", "inactive_in_erp")}, sorted(rows)
    admin.call("POST", f"/api/erp/reconcile/rows/{rows[('DE-400-001', 'description')]['id']}/resend")
    drain_erp(admin)
    assert FD.product("DE-400-001")["label"] == "PUMP CASING"


def test_dolibarr_refusal_is_shown_in_plain_words(kosha, dolibarr):
    admin = kosha.client()
    admin.call("PUT", "/api/erp/settings", {"connector": DOLIBARR, "enabled": True, "dry_run": False,
                                            "base_url": dolibarr, "api_key": "not-the-key"})
    t = admin.call("POST", "/api/erp/test")
    assert not t["ok"] and "refused the API key" in t["message"] and "Access denied" in t["message"]
    item = admin.call("GET", "/api/items?q=DE-400-002")["items"][0]
    r = admin.call("POST", "/api/erp/push", {"kind": "item", "id": item["id"]})
    assert r["job"]["status"] == "failed" and "refused the API key" in r["job"]["error"]
    admin.call("PUT", "/api/erp/settings", {"api_key": FD.KEY})
    r = admin.call("POST", f"/api/erp/jobs/{r['job']['id']}/retry")
    assert r["job"]["status"] == "sent"
