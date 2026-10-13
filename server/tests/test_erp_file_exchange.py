# SPDX-License-Identifier: AGPL-3.0-or-later
"""An ERP without an API, through an exchange folder: Kosha writes released conveyor parts and their BOM as CSV and
JSON files (respecting dry run, children first, never the same content twice), a simulated ERP imports them and
writes stock, acknowledgements and exports back, and Kosha reads stock and cost and reconciles from those files."""
import json

import pytest

from erp_helpers import drain_erp, folder, released_bom, released_part, run_check

from fake_erp_folder import SETTINGS, FakeFolderErp  # noqa: E402  (found through erp_helpers)

EXCHANGE = "org.kosha.file-exchange"


@pytest.fixture(scope="module")
def exchange(tmp_path_factory):
    return tmp_path_factory.mktemp("erp-exchange")


def _transfers(path):
    out = path / "to-erp"
    return sorted(p.name for p in out.iterdir() if p.is_file()) if out.is_dir() else []


def test_file_exchange_as_the_erp(kosha, exchange):
    admin = kosha.client()
    cfg = admin.call("PUT", "/api/erp/settings", {"connector": EXCHANGE})
    assert cfg["connector"] == EXCHANGE and cfg["connector_name"] == "File exchange"
    assert {"folder", "formats", "stock_file", "items_export_file"} <= {f["key"] for f in cfg["connector_fields"]}
    r = admin.call("PUT", "/api/erp/settings", {"enabled": True, "dry_run": True, "folder": str(exchange / "missing")})
    t = admin.call("POST", "/api/erp/test")
    assert not t["ok"] and "does not exist" in t["message"]
    r = admin.call("PUT", "/api/erp/settings", {"folder": str(exchange), **SETTINGS})
    assert r["folder"] == str(exchange)
    t = admin.call("POST", "/api/erp/test")
    assert t["ok"] and str(exchange / "to-erp") in t["message"], t

    f = folder(admin, "Conveyors")
    parts = {pn: released_part(admin, f, pn, desc, mat) for pn, desc, mat in (
        ("DE-300-001", "ROLLER", "S235"), ("DE-300-002", "IDLER", ""), ("DE-300-000", "CONVEYOR ASSY", ""))}
    item = {pn: p["item"]["id"] for pn, p in parts.items()}
    released_bom(admin, item["DE-300-000"], [(item["DE-300-001"], 6), (item["DE-300-002"], 2)])
    jobs = drain_erp(admin)
    assert {j["status"] for j in jobs} == {"dry_run"} and _transfers(exchange) == []   # dry run: nothing written

    admin.call("PUT", "/api/erp/settings", {"dry_run": False})
    for j in jobs:
        admin.call("POST", f"/api/erp/jobs/{j['id']}/retry")
    jobs = drain_erp(admin)
    assert all(j["status"] == "sent" for j in jobs), jobs
    files = _transfers(exchange)
    assert len(files) == 8 and all(n.endswith((".csv", ".json")) for n in files)      # 3 items and 1 BOM, 2 files each
    docs = [json.loads((exchange / "to-erp" / n).read_text(encoding="utf-8")) for n in files if n.endswith(".json")]
    bom_doc = next(d for d in docs if d["kind"] == "bom")
    assert bom_doc["sequence"] > max(d["sequence"] for d in docs if d["kind"] == "item")   # children first
    assert [(ln["code"], ln["qty"]) for ln in bom_doc["bom"]["lines"]] == [("DE-300-001", 6.0), ("DE-300-002", 2.0)]
    roller = next(d["item"] for d in docs if d["kind"] == "item" and d["item"]["code"] == "DE-300-001")
    assert (roller["description"], roller["material"], roller["uom"]) == ("ROLLER", "S235", "Nos")
    r = admin.call("POST", f"/api/boms/{admin.call('GET', '/api/boms')['boms'][0]['id']}/push")
    assert "unchanged" in r["job"]["response_text"] and len(_transfers(exchange)) == 8, r   # the same BOM: no new file

    erp = FakeFolderErp(exchange)
    assert len(erp.run()) == 4 and _transfers(exchange) == []
    erp.add_stock("DE-300-001", 12, cost=3.5)
    erp.add_stock("DE-300-001", 2, warehouse="Line 2")
    r = admin.call("POST", "/api/erp/push", {"kind": "refresh", "id": item["DE-300-001"]})
    assert r["job"]["status"] == "sent", r
    it = admin.call("GET", f"/api/items/{item['DE-300-001']}")["item"]
    assert it["erp_stock"] == 14 and it["erp_cost"] == 3.5

    d = run_check(admin)
    assert d["run"]["status"] == "done" and d["run"]["differences"] == 0, d["rows"]
    assert d["run"]["connector_name"] == "File exchange"

    erp.items["DE-300-001"]["description"] = "ROLLER (CHANGED IN THE ERP)"
    erp.items["DE-300-002"]["active"] = "no"
    erp.write_back()
    d = run_check(admin)
    rows = {(r["part_number"], r["check_code"]): r for r in d["rows"]}
    assert set(rows) == {("DE-300-001", "description"), ("DE-300-002", "inactive_in_erp")}, sorted(rows)
    admin.call("POST", f"/api/erp/reconcile/rows/{rows[('DE-300-001', 'description')]['id']}/resend")
    jobs = drain_erp(admin)
    assert "written again" in jobs[0]["response_text"], jobs[0]
    assert erp.run() and erp.items["DE-300-001"]["description"] == "ROLLER"
    erp.items["DE-300-002"]["active"] = "yes"
    erp.write_back()
    assert run_check(admin)["run"]["differences"] == 0


def test_rejected_transfer_is_written_again(kosha, exchange):
    admin = kosha.client()
    admin.call("PUT", "/api/erp/settings", {"connector": EXCHANGE, "enabled": True, "dry_run": False,
                                            "folder": str(exchange), **SETTINGS, "uom_map": {"NOS": "Each"}})
    erp = FakeFolderErp(exchange)
    erp.reject_units = {"Each"}
    f = folder(admin, "Mixers")
    released_part(admin, f, "DE-200-001", "MIXER PADDLE", "S355")
    drain_erp(admin)
    erp.run()
    t = admin.call("POST", "/api/erp/test")
    assert t["ok"] and "The ERP rejected 1" in t["message"] and "Unit Each does not exist" in t["message"], t
    erp.reject_units = set()
    pid = admin.call("GET", "/api/items?q=DE-200-001")["items"][0]["id"]
    r = admin.call("POST", "/api/erp/push", {"kind": "item", "id": pid})
    assert r["job"]["status"] == "sent" and "the ERP rejected" in r["job"]["response_text"], r
    erp.run()
    assert "DE-200-001" in erp.items
