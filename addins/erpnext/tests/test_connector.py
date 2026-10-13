# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""ERPNext connector against the in-memory ERPNext, through the SDK conformance kit."""
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

import fake_erpnext as FE  # noqa: E402
from kosha_erpnext.connector import ERPNextConnector, frappe_message, plain_text  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.erp import ErpBom, ErpBomLine, ErpError, ErpItem, ErpNotSupported  # noqa: E402
from kosha_sdk.testing import check_erp_connector, check_manifest  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def erp_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(FE.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def connector(url, **overrides):
    settings = {"base_url": url, "api_key": FE.KEY, "api_secret": FE.SECRET, "company": "Demo Engineering",
                "submit_boms": True, "verify_tls": True, "timeout_s": 5, **overrides}
    return ERPNextConnector(AddinContext("org.kosha.erpnext", settings=settings, log=logging.getLogger("test")))


def test_manifest_is_valid():
    assert check_manifest(HERE.parent) == []


def test_conformance(erp_url):
    assert check_erp_connector(connector(erp_url)) == []


def test_conformance_with_kosha_fields(erp_url):
    c = connector(erp_url, revision_field="custom_kosha_revision", material_field="custom_kosha_material")
    assert check_erp_connector(c, prefix="KOSHA-TEST2") == []


def test_reading_items_back_for_reconciliation(erp_url):
    c = connector(erp_url, revision_field="custom_kosha_revision", material_field="custom_kosha_material")
    c.upsert_item(ErpItem(code="DE-300", description="GEAR HOUSING", revision="B", uom="Nos", material="EN-GJL-250"))
    connector(erp_url).upsert_item(ErpItem(code="DE-301", description="NOT FROM KOSHA", uom="Nos"))
    FE.STATE["items"]["DE-300"]["description"] = "<div><p>GEAR&nbsp;HOUSING</p></div>"   # edited in ERPNext's editor
    FE.STATE["items"]["DE-300"]["disabled"] = 1
    got = c.fetch_items(["DE-300", "DE-301", "DE-NOPE", "DE-300"])
    assert set(got) == {"DE-300", "DE-301"}
    r = got["DE-300"]
    assert (r.description, r.uom, r.revision, r.material, r.active, r.from_kosha) == \
        ("GEAR HOUSING", "Nos", "B", "EN-GJL-250", False, True)
    assert got["DE-301"].from_kosha is False and got["DE-301"].revision == ""
    plain = connector(erp_url).fetch_items(["DE-300"])["DE-300"]
    assert plain.revision is None and plain.material is None and plain.from_kosha is None   # not set up: not compared
    listed = c.list_items_from_kosha()
    assert "DE-300" in listed and "DE-301" not in listed
    with pytest.raises(ErpNotSupported, match="revision custom field"):
        connector(erp_url).list_items_from_kosha()


def test_reading_boms_back_for_reconciliation(erp_url):
    c = connector(erp_url, revision_field="custom_kosha_revision")
    for code in ("DE-400", "DE-401", "DE-402"):
        c.upsert_item(ErpItem(code=code, description=f"PART {code}", uom="Nos"))
    assert c.fetch_bom("DE-400") is None
    c.upsert_bom(ErpBom(ErpItem("DE-400", "ASSY"), "C", (ErpBomLine("DE-401", 2, "Nos"), ErpBomLine("DE-402", 1.5, "Nos"))))
    b = c.fetch_bom("DE-400")
    assert b.revision == "C" and b.reference.startswith("BOM-DE-400")
    assert [(ln.code, ln.qty) for ln in b.lines] == [("DE-401", 2.0), ("DE-402", 1.5)]
    assert connector(erp_url).fetch_bom("DE-400").revision is None


def test_plain_text():
    assert plain_text("<div>HEX &amp; BOLT\n  M16</div>") == "HEX & BOLT M16"


def test_bom_is_submitted_and_children_listed(erp_url):
    c = connector(erp_url)
    for code in ("DE-100", "DE-101", "DE-102"):
        c.upsert_item(ErpItem(code=code, description=f"PART {code}", uom="Nos"))
    r = c.upsert_bom(ErpBom(ErpItem("DE-100", "ASSY"), "B", (ErpBomLine("DE-101", 2, "Nos"), ErpBomLine("DE-102", 1, "Nos"))))
    bom = FE.STATE["boms"][r.external_id]
    assert bom["docstatus"] == 1 and len(bom["items"]) == 2 and bom["company"] == "Demo Engineering"


def test_wrong_secret_gives_erpnext_message(erp_url):
    c = connector(erp_url, api_secret="wrong")
    assert not c.health().ok
    with pytest.raises(ErpError, match="Not permitted"):
        c.upsert_item(ErpItem(code="DE-200", description="X"))


def test_unreachable_server_is_a_plain_message():
    c = connector(f"http://127.0.0.1:{_free_port()}", timeout_s=1)
    h = c.health()
    assert not h.ok and "Cannot reach ERPNext" in h.message


def test_missing_address():
    c = ERPNextConnector(AddinContext("org.kosha.erpnext", settings={}))
    with pytest.raises(ErpError, match="address is not set"):
        c.upsert_item(ErpItem(code="X", description="X"))


def test_frappe_messages_are_readable():
    assert frappe_message('{"exception": "frappe.exceptions.ValidationError: Item Group is mandatory"}') \
        .endswith("Item Group is mandatory")
    assert frappe_message("<html>bad gateway</html>").startswith("<html>")


# ------------------------------------------------------------------ calls from ERPNext to Kosha
import json  # noqa: E402

from kosha_erpnext import signing  # noqa: E402


def test_signing_files_are_identical_on_both_sides():
    server_side = (HERE.parent / "kosha_erpnext" / "signing.py").read_bytes()
    frappe_side = (HERE.parent / "frappe" / "kosha_connector" / "kosha_connector" / "signing.py").read_bytes()
    assert server_side == frappe_side


def test_signature_checks():
    body = b'{"events": []}'
    h = signing.sign("s3cret", body, timestamp=1_000_000)
    assert signing.verify("s3cret", h, body, now=1_000_010) == ""
    assert "does not match" in signing.verify("other", h, body, now=1_000_010)
    assert "does not match" in signing.verify("s3cret", h, body + b" ", now=1_000_010)
    assert "too old" in signing.verify("s3cret", h, body, now=1_000_000 + 301)
    assert "not signed" in signing.verify("s3cret", {}, body)
    assert "No webhook secret" in signing.verify("", h, body)


def test_webhook_reads_only_known_events(erp_url):
    c = connector(erp_url, webhook_secret="s3cret")
    body = json.dumps({"events": [{"kind": "stock_changed", "code": "DE-100"}, {"kind": "delete_everything", "code": "X"},
                                  {"kind": "item_changed", "code": ""},
                                  {"kind": "sales_order", "code": "SO-26-0042", "detail": "Example Foods|2026-12-15"}]}).encode()
    events = c.handle_webhook(signing.sign("s3cret", body), body)
    assert [(e.kind, e.code) for e in events] == [("stock_changed", "DE-100"), ("sales_order", "SO-26-0042")]
    assert events[1].detail == "Example Foods|2026-12-15"
    with pytest.raises(ErpError, match="does not match"):
        c.handle_webhook(signing.sign("wrong", body), body)
    with pytest.raises(ErpError, match="No webhook secret"):
        connector(erp_url).handle_webhook(signing.sign("s3cret", body), body)


def test_hsn_oem_customer_and_status_go_to_the_named_fields(erp_url):
    c = connector(erp_url, hsn_field="gst_hsn_code", oem_field="custom_kosha_oem_refs",
                  customer_field="custom_kosha_customer", spares_only_field="custom_kosha_spares_only")
    item = ErpItem("DE-700", "IMPELLER 200 DIA", "A", "Nos", hsn="84139190", oem_refs=("ABC 12-345",),
                   customer="Example Foods")
    assert c.upsert_item(item).action == "created"
    it = FE.STATE["items"]["DE-700"]
    assert (it["gst_hsn_code"], it["custom_kosha_oem_refs"], it["custom_kosha_customer"]) == \
        ("84139190", "ABC 12-345", "Example Foods")
    assert it["custom_kosha_spares_only"] == 0 and not it["disabled"]
    assert c.upsert_item(item).action == "unchanged"
    from dataclasses import replace
    assert c.upsert_item(replace(item, status="spares_only")).action == "updated"
    assert FE.STATE["items"]["DE-700"]["custom_kosha_spares_only"] == 1 and not FE.STATE["items"]["DE-700"]["disabled"]
    assert c.upsert_item(replace(item, status="obsolete")).action == "updated"
    assert FE.STATE["items"]["DE-700"]["disabled"] == 1
    # without the settings nothing extra is sent, so an ERPNext without those fields is not disturbed
    connector(erp_url).upsert_item(ErpItem("DE-701", "SHAFT", "A", "Nos", hsn="8483"))
    assert "gst_hsn_code" not in FE.STATE["items"]["DE-701"]


def test_released_pdf_is_attached_and_the_older_one_taken_off(erp_url):
    c = connector(erp_url)
    c.upsert_item(ErpItem("DE-710", "BEARING HOUSING", "A", "Nos"))
    FE.STATE["files"]["HAND-1"] = {"name": "HAND-1", "file_name": "supplier quote.pdf", "file_size": 3,
                                   "attached_to_doctype": "Item", "attached_to_name": "DE-710"}
    a = c.attach_file("DE-710", "A", "DE-710 Rev A.pdf", b"%PDF-A")
    assert a.action == "created"
    assert c.attach_file("DE-710", "A", "DE-710 Rev A.pdf", b"%PDF-A").action == "unchanged"
    b = c.attach_file("DE-710", "B", "DE-710 Rev B.pdf", b"%PDF-BB")
    assert b.action == "created"
    on_item = sorted(f["file_name"] for f in FE.STATE["files"].values() if f["attached_to_name"] == "DE-710")
    assert on_item == ["DE-710 Rev B.pdf", "supplier quote.pdf"]       # files attached by hand stay
    assert FE.STATE["files"][b.external_id]["content"] == b"%PDF-BB"
    with pytest.raises(ErpError, match="not in ERPNext yet"):
        c.attach_file("DE-799", "A", "DE-799 Rev A.pdf", b"%PDF")


def test_webhook_takes_orders_and_parties(erp_url):
    c = connector(erp_url, webhook_secret="s3cret")
    body = json.dumps({"events": [{"kind": "purchase_order", "code": "PO-26-0007", "detail": "Example Castings|DE-100"},
                                  {"kind": "customer", "code": "Example Foods"},
                                  {"kind": "price_list", "code": "Standard Buying"}]}).encode()
    assert [e.kind for e in c.handle_webhook(signing.sign("s3cret", body), body)] == \
        ["purchase_order", "customer", "price_list"]
