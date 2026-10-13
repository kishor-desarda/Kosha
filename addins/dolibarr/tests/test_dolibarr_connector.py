# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Dolibarr connector against the in-memory Dolibarr, through the SDK conformance kit."""
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(HERE.parent), str(ROOT / "sdk" / "python")]

uvicorn = pytest.importorskip("uvicorn")

import fake_dolibarr as FD  # noqa: E402
from kosha_dolibarr.connector import DolibarrConnector, dolibarr_message  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.erp import ErpBom, ErpBomLine, ErpError, ErpItem, ErpNotSupported  # noqa: E402
from kosha_sdk.testing import check_erp_connector, check_manifest  # noqa: E402

CUSTOM = {"revision_field": "kosha_revision", "material_field": "kosha_material"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def dolibarr_url():
    FD.reset()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(FD.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def connector(url, **overrides):
    settings = {"base_url": url, "api_key": FD.KEY, "use_units": False, "validate_boms": True, "set_default_bom": True,
                "cost_source": "Weighted average price (WAP)", "verify_tls": True, "timeout_s": 5, **overrides}
    return DolibarrConnector(AddinContext("org.kosha.dolibarr", settings=settings, log=logging.getLogger("test")))


def part(c, code, desc=None, **kw):
    return c.upsert_item(ErpItem(code=code, description=desc or f"PART {code}", uom=kw.pop("uom", "P"), **kw))


def test_manifest_is_valid():
    assert check_manifest(HERE.parent) == []


def test_conformance(dolibarr_url):
    assert check_erp_connector(connector(dolibarr_url), uom="P") == []


def test_conformance_with_units_and_extra_fields(dolibarr_url):
    c = connector(dolibarr_url, use_units=True, **CUSTOM)
    assert check_erp_connector(c, prefix="KOSHA-TEST2", uom="P") == []


def test_conformance_when_lists_answer_empty(dolibarr_url):
    FD.STATE["lists_404_when_empty"] = False               # newer Dolibarr versions answer [] instead of 404
    try:
        assert check_erp_connector(connector(dolibarr_url), prefix="KOSHA-TEST3", uom="P") == []
    finally:
        FD.STATE["lists_404_when_empty"] = True


def test_health_says_who_and_where(dolibarr_url):
    h = connector(dolibarr_url).health()
    assert h.ok and "Dolibarr 20.0.2" in h.message and FD.LOGIN in h.message and dolibarr_url in h.message


def test_address_with_the_api_path_is_accepted(dolibarr_url):
    assert connector(dolibarr_url + "/api/index.php/").health().ok


def test_items_are_created_once_and_updated_only_when_changed(dolibarr_url):
    c = connector(dolibarr_url, use_units=True, **CUSTOM)
    item = ErpItem(code="DE-500", description="PUMP CASING", revision="A", uom="kg", material="EN-GJL-250",
                   weight_kg=4.5, make_or_buy="make")
    assert c.upsert_item(item).action == "created"
    p = FD.product("DE-500")
    assert (p["label"], p["weight"], p["weight_units"], p["fk_unit"], p["finished"], p["status"], p["status_buy"]) == \
        ("PUMP CASING", 4.5, 0, 2, "1", "1", "1")
    assert p["array_options"] == {"options_kosha_revision": "A", "options_kosha_material": "EN-GJL-250"}
    assert c.upsert_item(item).action == "unchanged"
    calls = len(FD.STATE["calls"])
    assert c.upsert_item(item).action == "unchanged"
    assert not [x for x in FD.STATE["calls"][calls:] if x[0] in ("POST", "PUT")]
    r = c.upsert_item(ErpItem(code="DE-500", description="PUMP CASING", revision="B", uom="kg",
                              material="EN-GJL-250", weight_kg=4.5))
    assert r.action == "updated" and r.payload == {"options_kosha_revision": "B"}
    assert FD.product("DE-500")["array_options"]["options_kosha_material"] == "EN-GJL-250"   # not cleared


def test_extra_fields_kosha_does_not_know_are_kept(dolibarr_url):
    c = connector(dolibarr_url, revision_field="kosha_revision")
    part(c, "DE-505", revision="A")
    FD.product("DE-505")["array_options"]["options_kosha_material"] = "SET IN DOLIBARR"
    part(c, "DE-505", revision="B")
    assert FD.product("DE-505")["array_options"] == {"options_kosha_revision": "B",
                                                     "options_kosha_material": "SET IN DOLIBARR"}


def test_product_not_for_sale_is_found_not_duplicated(dolibarr_url):
    c = connector(dolibarr_url)
    part(c, "DE-510", "SEAL")
    FD.product("DE-510").update(status="0", status_buy="0")
    assert part(c, "DE-510", "SEAL").action == "unchanged"
    assert sum(1 for p in FD.STATE["products"].values() if p["ref"] == "DE-510") == 1
    assert c.fetch_items(["DE-510"])["DE-510"].active is False


def test_codes_with_quotes_are_looked_up_one_by_one(dolibarr_url):
    c = connector(dolibarr_url)
    assert part(c, "DE-515(X)", "ODD CODE").action == "created"
    assert part(c, "DE-515(X)", "ODD CODE").action == "unchanged"
    assert set(c.fetch_items(["DE-515(X)", "DE-515"])) == {"DE-515(X)"}


def test_unknown_unit_is_a_plain_message(dolibarr_url):
    with pytest.raises(ErpError, match="no unit called 'Nos'"):
        connector(dolibarr_url, use_units=True).upsert_item(ErpItem(code="DE-530", description="X", uom="Nos"))
    assert FD.product("DE-530") is None
    assert connector(dolibarr_url).upsert_item(ErpItem(code="DE-530", description="X", uom="Nos")).action == "created"


def test_bom_unchanged_and_new_bom_becomes_default(dolibarr_url):
    c = connector(dolibarr_url)
    for code in ("DE-600", "DE-601", "DE-602"):
        part(c, code)
    bom = ErpBom(ErpItem("DE-600", "ASSY"), "A", (ErpBomLine("DE-601", 2, "P"), ErpBomLine("DE-602", 1, "P")))
    first = c.upsert_bom(bom)
    assert first.action == "created" and "validated" in first.message
    b1 = FD.STATE["boms"][int(first.external_id)]
    assert (b1["ref"], b1["label"], b1["status"]) == ("DE-600-A", "Rev A", 1)
    assert FD.product("DE-600")["fk_default_bom"] == b1["id"]
    assert FD.lines_of(b1["id"]) == [("DE-601", 2.0), ("DE-602", 1.0)]
    calls = len(FD.STATE["calls"])
    assert c.upsert_bom(bom).action == "unchanged"
    assert not [x for x in FD.STATE["calls"][calls:] if x[0] in ("POST", "PUT", "DELETE")]
    changed = ErpBom(ErpItem("DE-600", "ASSY"), "B", (ErpBomLine("DE-601", 3, "P"),))
    second = c.upsert_bom(changed)
    assert second.action == "created" and second.external_id != first.external_id
    b2 = FD.STATE["boms"][int(second.external_id)]
    assert b2["ref"] == "DE-600-B" and FD.product("DE-600")["fk_default_bom"] == b2["id"]
    assert b1["status"] == 1                                       # the old BOM is kept for orders already planned
    rb = c.fetch_bom("DE-600")
    assert rb.revision == "B" and rb.reference == "DE-600-B" and [(ln.code, ln.qty) for ln in rb.lines] == [("DE-601", 3.0)]
    # the same revision again with other lines gets a ref of its own
    third = c.upsert_bom(ErpBom(ErpItem("DE-600", "ASSY"), "B", (ErpBomLine("DE-601", 4, "P"),)))
    assert FD.STATE["boms"][int(third.external_id)]["ref"] == "DE-600-B.2"


def test_bom_left_half_way_is_finished_not_duplicated(dolibarr_url):
    c = connector(dolibarr_url)
    for code in ("DE-650", "DE-651", "DE-652"):
        part(c, code)
    bom = ErpBom(ErpItem("DE-650", "ASSY"), "A", (ErpBomLine("DE-651", 2), ErpBomLine("DE-652", 1)))
    # Kosha stopped after creating the draft and one wrong line (a crash or a lost answer)
    pid = FD.product("DE-650")["id"]
    bid = max(FD.STATE["boms"]) + 1000
    FD.STATE["boms"][bid] = {"id": bid, "ref": "DE-650-A", "label": "Rev A", "fk_product": pid, "bomtype": 0,
                             "qty": 1.0, "status": 0}
    FD.STATE["lines"][bid + 1] = {"id": bid + 1, "fk_bom": bid, "fk_product": FD.product("DE-651")["id"], "qty": 2.0,
                                  "position": 1}
    FD.STATE["lines"][bid + 2] = {"id": bid + 2, "fk_bom": bid, "fk_product": FD.product("DE-652")["id"], "qty": 9.0,
                                  "position": 2}
    r = c.upsert_bom(bom)
    assert r.action == "created" and r.external_id == str(bid) and r.message.startswith("finished BOM DE-650-A")
    assert len(FD.boms_of("DE-650")) == 1 and FD.STATE["boms"][bid]["status"] == 1
    assert sorted(FD.lines_of(bid)) == [("DE-651", 2.0), ("DE-652", 1.0)]
    assert c.upsert_bom(bom).action == "unchanged"


def test_bom_complete_but_not_validated_is_validated(dolibarr_url):
    c = connector(dolibarr_url, validate_boms=False, set_default_bom=False)
    for code in ("DE-660", "DE-661"):
        part(c, code)
    bom = ErpBom(ErpItem("DE-660", "ASSY"), "A", (ErpBomLine("DE-661", 5),))
    r = c.upsert_bom(bom)
    assert r.action == "created" and "left as a draft" in r.message
    b = FD.boms_of("DE-660")[0]
    assert b["status"] == 0 and not FD.product("DE-660")["fk_default_bom"]
    assert c.upsert_bom(bom).action == "unchanged"                # validating is switched off: nothing to do
    r = connector(dolibarr_url).upsert_bom(bom)
    assert r.action == "updated" and "validated" in r.message and b["status"] == 1
    assert FD.product("DE-660")["fk_default_bom"] == b["id"] and len(FD.boms_of("DE-660")) == 1


def test_bom_with_missing_parts_is_refused(dolibarr_url):
    c = connector(dolibarr_url)
    part(c, "DE-700", "ASSY")
    with pytest.raises(ErpError, match="not in Dolibarr yet: DE-799"):
        c.upsert_bom(ErpBom(ErpItem("DE-700", "ASSY"), "A", (ErpBomLine("DE-799", 1),)))
    with pytest.raises(ErpError, match="DE-798 is not in Dolibarr yet"):
        c.upsert_bom(ErpBom(ErpItem("DE-798", "ASSY"), "A", (ErpBomLine("DE-700", 1),)))
    assert not FD.boms_of("DE-700")


def test_bom_module_switched_off_is_a_plain_message(dolibarr_url, monkeypatch):
    c = connector(dolibarr_url)
    for code in ("DE-710", "DE-711"):
        part(c, code)
    real = c._call

    def no_bom_module(method, path, *a, **kw):
        if method == "POST" and path == "/boms":
            raise ErpError("Dolibarr refused the request: Not Found: API not found", status=404)
        return real(method, path, *a, **kw)
    monkeypatch.setattr(c, "_call", no_bom_module)
    with pytest.raises(ErpError, match="Switch on the Bills of Materials module"):
        c.upsert_bom(ErpBom(ErpItem("DE-710", "ASSY"), "A", (ErpBomLine("DE-711", 1),)))


def test_stock_and_cost(dolibarr_url):
    c = connector(dolibarr_url)
    part(c, "DE-800", "BOLT")
    assert c.stock_and_cost("DE-800").stock == 0                  # Dolibarr answers 404 when there is no stock
    FD.add_stock("DE-800", 6)
    FD.add_stock("DE-800", 4, warehouse="2")
    FD.product("DE-800").update(pmp=1.25, cost_price=1.4)
    s = c.stock_and_cost("DE-800")
    assert s.exists and s.stock == 10 and s.cost == 1.25
    assert {(w["warehouse"], w["actual_qty"]) for w in s.warehouses} == {("Main store", 6.0), ("Assembly", 4.0)}
    assert connector(dolibarr_url, cost_source="Cost price").stock_and_cost("DE-800").cost == 1.4
    assert not c.stock_and_cost("DE-NOPE").exists


def test_reading_back_for_reconciliation(dolibarr_url):
    c = connector(dolibarr_url, use_units=True, **CUSTOM)
    c.upsert_item(ErpItem(code="DE-900", description="COVER", revision="C", uom="P", material="S235"))
    connector(dolibarr_url, use_units=True).upsert_item(ErpItem(code="DE-901", description="MADE IN DOLIBARR", uom="kg"))
    got = c.fetch_items(["DE-900", "DE-901", "DE-NONE"])
    assert set(got) == {"DE-900", "DE-901"}
    r = got["DE-900"]
    assert (r.description, r.uom, r.revision, r.material, r.active, r.from_kosha) == ("COVER", "P", "C", "S235", True, True)
    assert got["DE-901"].uom == "KG" and got["DE-901"].from_kosha is False
    plain = connector(dolibarr_url).fetch_items(["DE-900"])["DE-900"]
    assert (plain.uom, plain.revision, plain.material, plain.from_kosha) == (None, None, None, None)
    listed = c.list_items_from_kosha()
    assert "DE-900" in listed and "DE-901" not in listed
    assert c.fetch_bom("DE-900") is None
    with pytest.raises(ErpNotSupported, match="revision extra field"):
        connector(dolibarr_url).list_items_from_kosha()


def test_wrong_key_and_unreachable_server_are_plain_messages(dolibarr_url):
    bad = connector(dolibarr_url, api_key="wrong")
    h = bad.health()
    assert not h.ok and "refused the API key" in h.message and "Access denied" in h.message
    with pytest.raises(ErpError, match="refused the API key"):
        bad.upsert_item(ErpItem(code="DE-999", description="X"))
    down = connector(f"http://127.0.0.1:{_free_port()}", timeout_s=1)
    assert "Cannot reach Dolibarr" in down.health().message
    nowhere = connector(dolibarr_url + "/not-dolibarr")
    assert "no REST API" in nowhere.health().message


def test_dolibarr_refusal_keeps_its_own_message(dolibarr_url):
    c = connector(dolibarr_url, revision_field="no_such_field")
    with pytest.raises(ErpError, match="Unknown extrafield options_no_such_field") as e:
        c.upsert_item(ErpItem(code="DE-950", description="X", revision="A"))
    assert e.value.status == 400 and "Bad Request" in str(e.value)


def test_missing_settings():
    c = DolibarrConnector(AddinContext("org.kosha.dolibarr", settings={}))
    with pytest.raises(ErpError, match="address is not set"):
        c.upsert_item(ErpItem(code="X", description="X"))
    c = DolibarrConnector(AddinContext("org.kosha.dolibarr", settings={"base_url": "http://127.0.0.1:9"}))
    with pytest.raises(ErpError, match="API key"):
        c.upsert_item(ErpItem(code="X", description="X"))
    assert not c.health().ok


def test_dolibarr_messages_are_readable():
    assert dolibarr_message('{"error": {"code": 400, "message": "Bad Request:  label\\nfield missing"}}') == \
        "Bad Request: label field missing"
    assert dolibarr_message("<html><body><h1>502 Bad Gateway</h1></body></html>") == "502 Bad Gateway"
    assert dolibarr_message('{"message": "Something"}') == "Something"
