# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Odoo connector against the in-memory Odoo, through the SDK conformance kit."""
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

import fake_odoo as FO  # noqa: E402
from kosha_odoo.connector import OdooConnector, odoo_message  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.erp import ErpBom, ErpBomLine, ErpError, ErpItem, ErpNotSupported  # noqa: E402
from kosha_sdk.testing import check_erp_connector, check_manifest  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def odoo_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(FO.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def connector(url, **overrides):
    settings = {"base_url": url, "database": FO.DB, "username": FO.LOGIN, "api_key": FO.KEY, "company_id": None,
                "product_setup": "Odoo 15 to 17", "verify_tls": True, "timeout_s": 5, **overrides}
    return OdooConnector(AddinContext("org.kosha.odoo", settings=settings, log=logging.getLogger("test")))


def product(code):
    return next(p for p in FO.STATE["product.product"].values() if p.get("default_code") == code)


def test_manifest_is_valid():
    assert check_manifest(HERE.parent) == []


def test_conformance(odoo_url):
    assert check_erp_connector(connector(odoo_url), uom="Units") == []


def test_conformance_with_custom_fields(odoo_url):
    c = connector(odoo_url, revision_field="x_kosha_revision", material_field="x_kosha_material")
    assert check_erp_connector(c, prefix="KOSHA-TEST2", uom="Units") == []


def test_health_says_who_and_where(odoo_url):
    h = connector(odoo_url).health()
    assert h.ok and "Odoo 17.0" in h.message and FO.LOGIN in h.message and FO.DB in h.message


def test_items_are_created_once_and_updated_only_when_changed(odoo_url):
    c = connector(odoo_url, revision_field="x_kosha_revision", material_field="x_kosha_material")
    item = ErpItem(code="DE-500", description="PUMP CASING", revision="A", uom="Units", material="EN-GJL-250", weight_kg=4.5)
    assert c.upsert_item(item).action == "created"
    p = product("DE-500")
    assert (p["name"], p["x_kosha_revision"], p["x_kosha_material"], p["weight"], p["uom_id"], p["detailed_type"]) == \
        ("PUMP CASING", "A", "EN-GJL-250", 4.5, 1, "product")
    assert c.upsert_item(item).action == "unchanged"
    r = c.upsert_item(ErpItem(code="DE-500", description="PUMP CASING", revision="B", uom="Units", material="EN-GJL-250",
                              weight_kg=4.5))
    assert r.action == "updated" and r.payload == {"x_kosha_revision": "B"}


def test_archived_product_is_found_not_duplicated(odoo_url):
    c = connector(odoo_url)
    c.upsert_item(ErpItem(code="DE-510", description="SEAL", uom="Units"))
    product("DE-510")["active"] = False
    assert c.upsert_item(ErpItem(code="DE-510", description="SEAL", uom="Units")).action == "unchanged"
    assert sum(1 for p in FO.STATE["product.product"].values() if p.get("default_code") == "DE-510") == 1
    assert c.fetch_items(["DE-510"])["DE-510"].active is False


def test_odoo_18_products_are_storable(odoo_url):
    connector(odoo_url, product_setup="Odoo 18 or later").upsert_item(ErpItem(code="DE-520", description="SHAFT", uom="Units"))
    p = product("DE-520")
    assert p["type"] == "consu" and p["is_storable"] is True


def test_unknown_unit_is_a_plain_message(odoo_url):
    with pytest.raises(ErpError, match="no unit of measure called 'Nos'"):
        connector(odoo_url).upsert_item(ErpItem(code="DE-530", description="X", uom="Nos"))


def test_bom_unchanged_and_new_bom_goes_first(odoo_url):
    c = connector(odoo_url)
    for code in ("DE-600", "DE-601", "DE-602"):
        c.upsert_item(ErpItem(code=code, description=f"PART {code}", uom="Units"))
    bom = ErpBom(ErpItem("DE-600", "ASSY"), "A", (ErpBomLine("DE-601", 2, "Units"), ErpBomLine("DE-602", 1, "Units")))
    first = c.upsert_bom(bom)
    assert first.action == "created"
    assert c.upsert_bom(bom).action == "unchanged"
    calls_before = len(FO.STATE["calls"])
    assert c.upsert_bom(bom).action == "unchanged"
    assert ("mrp.bom", "create") not in FO.STATE["calls"][calls_before:]
    changed = ErpBom(ErpItem("DE-600", "ASSY"), "B", (ErpBomLine("DE-601", 3, "Units"),))
    second = c.upsert_bom(changed)
    assert second.action == "created" and second.external_id != first.external_id
    old, new = FO.STATE["mrp.bom"][int(first.external_id)], FO.STATE["mrp.bom"][int(second.external_id)]
    assert new["sequence"] < old["sequence"] and old["active"]          # the old BOM is kept, the new one is used
    rb = c.fetch_bom("DE-600")
    assert rb.revision == "B" and rb.reference == "Rev B" and [(ln.code, ln.qty) for ln in rb.lines] == [("DE-601", 3.0)]


def test_bom_with_missing_parts_is_refused(odoo_url):
    c = connector(odoo_url)
    c.upsert_item(ErpItem(code="DE-700", description="ASSY", uom="Units"))
    with pytest.raises(ErpError, match="not in Odoo yet: DE-799"):
        c.upsert_bom(ErpBom(ErpItem("DE-700", "ASSY"), "A", (ErpBomLine("DE-799", 1),)))
    with pytest.raises(ErpError, match="DE-798 is not in Odoo yet"):
        c.upsert_bom(ErpBom(ErpItem("DE-798", "ASSY"), "A", (ErpBomLine("DE-700", 1),)))


def test_stock_and_cost(odoo_url):
    c = connector(odoo_url)
    c.upsert_item(ErpItem(code="DE-800", description="BOLT", uom="Units"))
    FO.add_stock("DE-800", 6)
    FO.add_stock("DE-800", 4, location=9)
    FO.add_stock("DE-800", 100, location=5)          # already delivered to a customer: not on hand
    product("DE-800")["standard_price"] = 1.25
    s = c.stock_and_cost("DE-800")
    assert s.exists and s.stock == 10 and s.cost == 1.25 and {w["warehouse"] for w in s.warehouses} == {"WH/Stock", "WH/Stock/Shelf 1"}
    assert not c.stock_and_cost("DE-NOPE").exists


def test_reading_back_for_reconciliation(odoo_url):
    c = connector(odoo_url, revision_field="x_kosha_revision", material_field="x_kosha_material")
    c.upsert_item(ErpItem(code="DE-900", description="COVER", revision="C", uom="Units", material="S235"))
    connector(odoo_url).upsert_item(ErpItem(code="DE-901", description="MADE IN ODOO", uom="kg"))
    got = c.fetch_items(["DE-900", "DE-901", "DE-NONE"])
    assert set(got) == {"DE-900", "DE-901"}
    r = got["DE-900"]
    assert (r.description, r.uom, r.revision, r.material, r.active, r.from_kosha) == ("COVER", "Units", "C", "S235", True, True)
    assert got["DE-901"].uom == "kg" and got["DE-901"].from_kosha is False
    listed = c.list_items_from_kosha()
    assert "DE-900" in listed and "DE-901" not in listed
    assert c.fetch_bom("DE-900") is None
    with pytest.raises(ErpNotSupported, match="revision custom field"):
        connector(odoo_url).list_items_from_kosha()


def test_wrong_key_and_unreachable_server_are_plain_messages(odoo_url):
    bad = connector(odoo_url, api_key="wrong")
    h = bad.health()
    assert not h.ok and "refused the user" in h.message
    with pytest.raises(ErpError, match="refused the user"):
        bad.upsert_item(ErpItem(code="DE-999", description="X"))
    down = connector(f"http://127.0.0.1:{_free_port()}", timeout_s=1)
    assert "Cannot reach Odoo" in down.health().message


def test_missing_settings():
    c = OdooConnector(AddinContext("org.kosha.odoo", settings={}))
    with pytest.raises(ErpError, match="address is not set"):
        c.upsert_item(ErpItem(code="X", description="X"))
    c = OdooConnector(AddinContext("org.kosha.odoo", settings={"base_url": "http://127.0.0.1:9"}))
    with pytest.raises(ErpError, match="database, user and API key"):
        c.upsert_item(ErpItem(code="X", description="X"))


def test_odoo_messages_are_readable():
    assert odoo_message({"data": {"name": "odoo.exceptions.AccessDenied", "message": "Access Denied"}}) == \
        "Odoo refused the user name or API key."
    assert odoo_message({"data": {"name": "odoo.exceptions.ValidationError", "message": "The  product\nneeds a name."}}) == \
        "The product needs a name."
    assert odoo_message({"message": "Odoo Server Error"}) == "Odoo Server Error"
