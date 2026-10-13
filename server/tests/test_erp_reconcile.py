# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP reconciliation end to end, against the simulated ERPNext: differences in plain words, "Send again",
the Excel export, and that the check itself never changes anything in the ERP."""
import io

import pytest

from erp_helpers import drain_erp, folder, released_bom, released_part, run_check, serve
from harness import free_port

import fake_erpnext as FE  # noqa: E402  (found through erp_helpers)


@pytest.fixture(scope="module")
def erpnext():
    FE.STATE.update({"items": {}, "boms": {}, "bins": {}, "calls": []})
    server, url = serve(FE.app)
    yield url
    server.should_exit = True


def _connect(admin, url, **extra):
    admin.call("PUT", "/api/erp/settings", {"enabled": True, "dry_run": False, "base_url": url, "api_key": FE.KEY,
                                            "api_secret": FE.SECRET, "revision_field": "custom_kosha_revision",
                                            "material_field": "custom_kosha_material", **extra})


def _by_check(d):
    out = {}
    for r in d["rows"]:
        out.setdefault((r["part_number"], r["check_code"]), r)
    return out


def _default_bom(code):
    return next(b for b in FE.STATE["boms"].values() if b["item"] == code and b.get("is_default"))


def test_reconciliation(kosha, erpnext):
    admin = kosha.client()
    viewer = kosha.add_user("vera", "viewer")
    viewer.call("POST", "/api/erp/reconcile", expect=403)
    r = admin.call("POST", "/api/erp/reconcile", expect=409)
    assert "Connect to ERPNext" in r["error"]                       # says what to do first
    assert admin.call("GET", "/api/erp/reconcile")["run"] is None

    _connect(admin, erpnext)
    f = folder(admin, "Frames")
    parts = {pn: released_part(admin, f, pn, desc, mat) for pn, desc, mat in (
        ("DE-100-001", "BRACKET", "S235"), ("DE-100-002", "PLATE", "S355"), ("DE-100-003", "SPACER", ""),
        ("DE-100-004", "OLD COVER", ""), ("DE-100-000", "FRAME ASSY", ""))}
    item = {pn: p["item"]["id"] for pn, p in parts.items()}
    released_bom(admin, item["DE-100-000"], [(item["DE-100-001"], 2), (item["DE-100-002"], 1), (item["DE-100-003"], 4)])
    jobs = drain_erp(admin)
    assert all(j["status"] == "sent" for j in jobs), jobs
    assert set(FE.STATE["items"]) == set(parts)

    # 1. everything was sent: Kosha and ERPNext match
    d = run_check(admin)
    run = d["run"]
    assert run["status"] == "done" and run["differences"] == 0 and run["message"] == "Kosha and ERPNext match.", run
    assert run["items_checked"] == 5 and run["boms_checked"] == 1 and run["done"] == run["total"]

    # 2. people change things in ERPNext; an item is made obsolete in Kosha
    FE.STATE["items"]["DE-100-001"].update({"description": "BRACKET, OLD", "custom_kosha_revision": "Z"})
    FE.STATE["items"]["DE-100-002"]["stock_uom"] = "Kg"
    del FE.STATE["items"]["DE-100-003"]
    bom = _default_bom("DE-100-000")
    bom["items"] = [{"item_code": "DE-100-001", "qty": 3, "uom": "Nos"}, {"item_code": "DE-100-003", "qty": 4, "uom": "Nos"},
                    {"item_code": "DE-100-099", "qty": 1, "uom": "Nos"}]
    FE.STATE["items"]["DE-999-999"] = {"item_code": "DE-999-999", "description": "STRAY", "stock_uom": "Nos",
                                       "custom_kosha_revision": "A", "disabled": 0}
    admin.call("PUT", "/api/settings", {"obsolete_needs_change_order": False})   # a small shop: no change order
    admin.call("POST", f"/api/documents/{parts['DE-100-004']['doc']['id']}/state", {"to": "obsolete"})
    writes_before = [c for c in FE.STATE["calls"] if c[0] in ("POST", "PUT")]

    d = run_check(admin)
    assert [c for c in FE.STATE["calls"] if c[0] in ("POST", "PUT")] == writes_before   # the check only reads
    rows = _by_check(d)
    assert set(rows) == {("DE-100-001", "description"), ("DE-100-001", "revision"), ("DE-100-002", "uom"),
                         ("DE-100-003", "missing_in_erp"), ("DE-100-004", "obsolete_active"),
                         ("DE-100-000", "bom_qty"), ("DE-100-000", "bom_line_missing"),
                         ("DE-100-000", "bom_line_extra"), ("DE-999-999", "erp_only")}, sorted(rows)
    assert d["run"]["differences"] == 9 and d["run"]["fixable"] == 6
    desc = rows[("DE-100-001", "description")]
    assert (desc["kosha_value"], desc["erp_value"], desc["fix_kind"]) == ("BRACKET", "BRACKET, OLD", "item")
    assert rows[("DE-100-000", "bom_qty")]["message"] == "The quantity of DE-100-001 differs: Kosha 2, ERPNext 3."
    assert "DE-100-002 × 1 is missing" in rows[("DE-100-000", "bom_line_missing")]["message"]
    assert "DE-100-099 × 1" in rows[("DE-100-000", "bom_line_extra")]["message"]
    assert rows[("DE-100-002", "uom")]["fix_kind"] == "" and "by hand" in rows[("DE-100-002", "uom")]["advice"]
    assert "marked as coming from Kosha" in rows[("DE-999-999", "erp_only")]["message"]

    # 3. the Excel export lists the same differences
    data, ctype = admin.call("GET", f"/api/erp/reconcile/{d['run']['id']}/export", raw=True)
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data))
    sheet = wb["Differences"]
    assert sheet.max_row == 10 and [c.value for c in sheet[1]][:5] == ["Part number", "Where", "Difference", "In Kosha",
                                                                         "In ERPNext"]
    assert wb["Summary"]["A1"].value.startswith("Kosha and ERPNext")

    # 4. a difference that sending cannot fix is refused with the reason
    r = admin.call("POST", f"/api/erp/reconcile/rows/{rows[('DE-100-002', 'uom')]['id']}/resend", expect=409)
    assert "cannot fix" in r["error"]

    # 5. "Send again" on one row queues an ordinary, idempotent job; both rows of that item are marked
    r = admin.call("POST", f"/api/erp/reconcile/rows/{desc['id']}/resend")
    assert r["job"]["kind"] == "item" and r["job"]["trigger_ref"] == f"Reconciliation #{d['run']['id']}"
    marked = _by_check(r)
    assert marked[("DE-100-001", "revision")]["resend_job_id"] == r["job"]["id"]
    drain_erp(admin)
    assert FE.STATE["items"]["DE-100-001"]["description"] == "BRACKET"
    assert FE.STATE["items"]["DE-100-001"]["custom_kosha_revision"] == parts["DE-100-001"]["item"]["revision"]

    # 6. "Send all fixable again": the missing item first, then the BOM
    r = admin.call("POST", f"/api/erp/reconcile/{d['run']['id']}/resend")
    assert r["queued"] == 2
    drain_erp(admin)
    again = admin.call("POST", f"/api/erp/reconcile/{d['run']['id']}/resend")
    assert again["queued"] == 0                                    # already queued once: nothing twice

    # 7. checking again leaves only what people must fix in ERPNext by hand (the obsolete part's queued status job
    # has disabled it in ERPNext meanwhile)
    assert FE.STATE["items"]["DE-100-004"]["disabled"] == 1
    d = run_check(admin)
    assert set(_by_check(d)) == {("DE-100-002", "uom"), ("DE-999-999", "erp_only")}
    assert d["run"]["fixable"] == 0 and len(d["runs"]) == 3


def test_dry_run_and_unreachable_erp(kosha, erpnext):
    admin = kosha.client()
    _connect(admin, erpnext, dry_run=True)
    f = folder(admin, "Pumps")
    p = released_part(admin, f, "DE-200-001", "IMPELLER", "CF8M")
    drain_erp(admin)
    d = run_check(admin)                                           # reading is allowed while Dry run is on
    row = _by_check(d)[("DE-200-001", "missing_in_erp")]
    r = admin.call("POST", f"/api/erp/reconcile/rows/{row['id']}/resend")
    jobs = drain_erp(admin)
    assert next(j for j in jobs if j["id"] == r["job"]["id"])["status"] == "dry_run"
    assert "DE-200-001" not in FE.STATE["items"]                   # dry run: nothing was sent
    assert p["item"]["part_number"] == "DE-200-001"

    _connect(admin, f"http://127.0.0.1:{free_port()}", timeout_s=2)
    d = run_check(admin)
    assert d["run"]["status"] == "failed"
    assert "could not be read" in d["run"]["message"] and "Nothing was changed" in d["run"]["message"]
    _connect(admin, erpnext)
