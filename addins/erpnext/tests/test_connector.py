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
from kosha_erpnext.connector import ERPNextConnector, frappe_message  # noqa: E402
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.erp import ErpBom, ErpBomLine, ErpError, ErpItem  # noqa: E402
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
                                  {"kind": "item_changed", "code": ""}]}).encode()
    events = c.handle_webhook(signing.sign("s3cret", body), body)
    assert [(e.kind, e.code) for e in events] == [("stock_changed", "DE-100")]
    with pytest.raises(ErpError, match="does not match"):
        c.handle_webhook(signing.sign("wrong", body), body)
    with pytest.raises(ErpError, match="No webhook secret"):
        connector(erp_url).handle_webhook(signing.sign("s3cret", body), body)
