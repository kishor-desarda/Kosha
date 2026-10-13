# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP extended, end to end against the simulated ERPNext (specification 12): HSN and item group from the category
mapping, OEM references, the stamped PDF attached to the ERP item (older revision taken off), obsolete disabling the
item, automatic retries that wait longer each time, and purchase orders, customers and price lists coming in."""
import io
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from erp_helpers import REPO, drain_erp, serve

import fake_erpnext as FE  # noqa: E402  (found through erp_helpers)

pdfium = pytest.importorskip("pypdfium2")
sys.path.insert(0, str(REPO / "addins" / "erpnext"))
from kosha_erpnext import signing  # noqa: E402


@pytest.fixture(scope="module")
def erpnext():
    FE.STATE.update({"items": {}, "boms": {}, "bins": {}, "files": {}, "calls": []})
    server, url = serve(FE.app)
    yield url
    server.should_exit = True


def _pdf() -> bytes:
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(842, 595)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _connect(admin, url, **extra):
    admin.call("PUT", "/api/erp/settings", {
        "enabled": True, "dry_run": False, "base_url": url, "api_key": FE.KEY, "api_secret": FE.SECRET,
        "hsn_field": "gst_hsn_code", "oem_field": "custom_kosha_oem_refs", "webhook_secret": "s3cret", **extra})


def _root(admin) -> int:
    return next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])


def _attached(code):
    return sorted(f["file_name"] for f in FE.STATE["files"].values() if f["attached_to_name"] == code)


def _webhook(client, events, expect=200):
    body = json.dumps({"events": events}).encode()
    return client.call("POST", "/api/erp/webhook", raw_body=body, expect=expect,
                       headers={"Content-Type": "application/json", **signing.sign("s3cret", body)})


def test_items_attachments_status_and_retries(kosha, erpnext):
    admin = kosha.client()
    eng = kosha.add_user("ex-eng", "engineer")
    appr = kosha.add_user("ex-appr", "approver")
    _connect(admin, erpnext)
    r = admin.call("PUT", "/api/erp/settings", {"category_map": {"ex": {"item_group": "Pumps", "hsn": "84"}}}, expect=400)
    assert "4, 6 or 8 digits" in r["error"]
    admin.call("PUT", "/api/erp/settings", {"category_map": {"ex": {"item_group": "Pumps", "hsn": "84139190"},
                                                             "zz": {"item_group": "", "hsn": ""}}})
    m = admin.call("GET", "/api/erp/mapping")
    assert next(c for c in m["categories"] if c["code"] == "EX")["hsn"] == "84139190"
    assert not any(c["code"] == "ZZ" for c in m["categories"])           # empty rows are not kept

    name = "EX-100-001 Impeller.pdf"
    doc = eng.call("POST", "/api/documents/upload", files={"file": (name, _pdf())},
                   fields={"folder_id": _root(admin), "part_number": "EX-100-001", "description": "IMPELLER",
                           "material": "S235"})["documents"][0]
    admin.call("POST", "/api/oems", {"code": "ABC", "name": "Example Pumps Maker"})
    item_id = eng.call("GET", "/api/items?q=EX-100-001")["items"][0]["id"]
    eng.call("POST", f"/api/items/{item_id}/oem-refs", {"oem": "ABC", "oem_part_number": "12-345"})
    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released"})
    jobs = drain_erp(admin)
    assert {j["kind"]: j["status"] for j in jobs} == {"item": "sent", "attach": "sent"}, jobs
    it = FE.STATE["items"]["EX-100-001"]
    assert (it["item_group"], it["gst_hsn_code"], it["custom_kosha_oem_refs"]) == ("Pumps", "84139190", "ABC 12-345")
    assert _attached("EX-100-001") == ["EX-100-001 Rev A.pdf"]
    assert FE.STATE["files"][next(iter(FE.STATE["files"]))]["content"].startswith(b"%PDF")   # the stamped PDF

    # revision B: its PDF goes on, A's comes off
    eng.call("POST", f"/api/documents/{doc['id']}/revise")
    eng.call("POST", f"/api/documents/{doc['id']}/checkout")
    eng.call("POST", f"/api/documents/{doc['id']}/checkin", files={"file": (name, _pdf())}, fields={"comment": "vanes"})
    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released", "reason": "vanes"})
    drain_erp(admin)
    assert _attached("EX-100-001") == ["EX-100-001 Rev B.pdf"]

    # obsolete disables the ERP item
    with kosha.db() as c:
        c.execute("INSERT INTO settings(key, value_json) VALUES ('obsolete_needs_change_order', 'false') "
                  "ON CONFLICT(key) DO UPDATE SET value_json = 'false'")
    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "obsolete", "reason": "pump range dropped"})
    jobs = drain_erp(admin)
    assert FE.STATE["items"]["EX-100-001"]["disabled"] == 1
    assert jobs[0]["kind"] == "item" and "made obsolete" in jobs[0]["trigger_ref"]

    # a failure is tried again by itself, later; the wait grows
    _connect(admin, erpnext, api_secret="wrong")
    job = admin.call("POST", "/api/erp/push", {"kind": "item", "id": item_id})["job"]
    assert job["status"] == "failed" and job["attempts"] == 1 and job["next_try_at"]
    wait = datetime.fromisoformat(job["next_try_at"]) - datetime.now(timezone.utc)
    assert timedelta(seconds=30) < wait <= timedelta(seconds=61)
    admin.call("POST", "/api/erp/process")
    assert admin.call("GET", "/api/erp/jobs?status=failed")["jobs"][0]["attempts"] == 1   # not before its time
    with kosha.db() as c:
        c.execute("UPDATE erp_jobs SET next_try_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job["id"],))
    admin.call("POST", "/api/erp/process")
    again = next(j for j in admin.call("GET", "/api/erp/jobs")["jobs"] if j["id"] == job["id"])
    assert again["attempts"] == 2 and again["status"] == "failed"
    wait = datetime.fromisoformat(again["next_try_at"]) - datetime.now(timezone.utc)
    assert timedelta(minutes=3) < wait <= timedelta(minutes=4, seconds=1)
    _connect(admin, erpnext)
    with kosha.db() as c:
        c.execute("UPDATE erp_jobs SET next_try_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job["id"],))
    admin.call("POST", "/api/erp/process")
    done = next(j for j in admin.call("GET", "/api/erp/jobs")["jobs"] if j["id"] == job["id"])
    assert done["status"] == "sent" and not done["next_try_at"]


def test_orders_parties_and_price_lists_come_in(kosha, erpnext):
    admin = kosha.client()
    eng = kosha.add_user("po-eng", "engineer")
    appr = kosha.add_user("po-appr", "approver")
    _connect(admin, erpnext)
    doc = eng.call("POST", "/api/documents/upload", files={"file": ("PO-200-001 Shaft.pdf", _pdf())},
                   fields={"folder_id": _root(admin), "part_number": "PO-200-001", "description": "SHAFT",
                           "material": "C45"})["documents"][0]
    appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released"})
    drain_erp(admin)

    body = [{"kind": "purchase_order", "code": "PO-26-0007", "detail": "Example Castings|PO-200-001,PO-999-999"},
            {"kind": "customer", "code": "Example Foods"}, {"kind": "supplier", "code": "Example Castings"},
            {"kind": "customer", "code": "Old Example Mills", "detail": "disabled"},
            {"kind": "price_list", "code": "Standard Buying"}]
    r = _webhook(_anon(admin), body)
    assert r["accepted"] == 5 and r["queued"] >= 1                     # price list: stock and cost read again
    orders = eng.call("GET", "/api/erp/orders")["orders"]
    o = orders[0]
    assert (o["number"], o["party"], o["kind_label"], o["status"]) == ("PO-26-0007", "Example Castings",
                                                                        "Purchase order", "open")
    lines = {ln["code"]: ln for ln in o["lines"]}
    assert lines["PO-200-001"]["revision"] == "A" and lines["PO-200-001"]["note"] == ""
    assert lines["PO-999-999"]["note"] == "Not in Kosha"
    with kosha.db() as c:
        bell = c.execute("SELECT COUNT(*) FROM notifications WHERE title LIKE '%PO-26-0007%'").fetchone()[0]
    assert bell >= 1                                                    # approvers are told

    customers = eng.call("GET", "/api/erp/parties?kind=customer")["parties"]
    assert [(p["name"], p["active"]) for p in customers] == [("Example Foods", 1), ("Old Example Mills", 0)]
    assert eng.call("GET", "/api/erp/parties?kind=supplier&q=cast")["parties"][0]["name"] == "Example Castings"

    # the vendor package for the order: a transmittal with the revision the order came in with
    t = eng.call("POST", f"/api/erp/orders/{o['id']}/package")["transmittal"]
    assert t["order_ref"] == "PO-26-0007" and t["recipient"] == "Example Castings"
    assert [f["revision"] for f in t["files"]] == ["A"]
    assert eng.call("GET", "/api/erp/orders")["orders"][0]["transmittal_number"] == t["number"]

    # the same order again keeps its revisions; a cancellation closes it and no package is made
    _webhook(_anon(admin), [{"kind": "purchase_order", "code": "PO-26-0007", "detail": "Example Castings||cancelled"}])
    o = eng.call("GET", "/api/erp/orders")["orders"][0]
    assert o["status"] == "cancelled" and len(o["lines"]) == 2
    r = eng.call("POST", f"/api/erp/orders/{o['id']}/package", expect=409)
    assert "cancelled" in r["error"]


def _anon(client):
    from harness import Client
    return Client(client.base)          # the ERP signs its calls; it does not sign in
