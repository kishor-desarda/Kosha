# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end API test. Starts Kosha server and a fake ERPNext, then walks
through the main engineering workflows.

    python tests/test_api.py          (or: python -m pytest tests)
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "addins", "erpnext", "tests"))

DATA = tempfile.mkdtemp(prefix="kosha_test_")
os.environ["KOSHA_DATA_DIR"] = DATA
os.environ["KOSHA_DISABLE_WORKER"] = "1"
os.environ["KOSHA_API_RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["KOSHA_ADMIN_PASSWORD"] = "admin-pass-1"

import uvicorn  # noqa: E402

from kosha.cad import dxfwriter as W  # noqa: E402
from kosha.cad import mesh as M  # noqa: E402

from harness import free_port  # noqa: E402

PORT, ERP_PORT = free_port(), free_port()     # free ports, so two runs (or another program) never collide
BASE = f"http://127.0.0.1:{PORT}"


def serve(app_path, port):
    cfg = uvicorn.Config(app_path, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("server did not start")


class Client:
    def __init__(self, token=None):
        self.token = token

    def call(self, method, path, body=None, files=None, fields=None, expect=200, raw=False, raw_body=None, headers=None):
        headers = dict(headers or {})
        data = None
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if files is not None or fields is not None:
            boundary = uuid.uuid4().hex
            buf = io.BytesIO()
            for k, v in (fields or {}).items():
                buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
            for k, (name, content) in (files or {}).items():
                for nm, ct in ([(name, content)] if not isinstance(name, list) else zip(name, content)):
                    buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{nm}\"\r\n"
                              f"Content-Type: application/octet-stream\r\n\r\n".encode())
                    buf.write(ct)
                    buf.write(b"\r\n")
            buf.write(f"--{boundary}--\r\n".encode())
            data = buf.getvalue()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif raw_body is not None:
            data = raw_body
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                status, payload, ctype = r.status, r.read(), r.headers.get("content-type", "")
        except urllib.error.HTTPError as e:
            status, payload, ctype = e.code, e.read(), e.headers.get("content-type", "")
        assert status == expect, f"{method} {path}: expected {expect}, got {status}: {payload[:400]!r}"
        if raw:
            return payload, ctype
        return json.loads(payload) if payload else {}


def login(user, pw):
    r = Client().call("POST", "/api/auth/login", {"username": user, "password": pw})
    return Client(r["token"]), r["user"]


def main():
    serve("kosha.app:app", PORT)
    serve("fake_erpnext:app", ERP_PORT)
    import fake_erpnext as FE

    admin, _ = login("admin", "admin-pass-1")
    Client().call("POST", "/api/auth/login", {"username": "admin", "password": "nope"}, expect=400)
    Client().call("GET", "/api/folders", expect=401)

    # users
    for u, n, r in (("asha", "Asha Menon", "engineer"), ("daniel", "Daniel Okafor", "approver"),
                    ("tom", "Tom Becker", "viewer")):
        admin.call("POST", "/api/users", {"username": u, "full_name": n, "role": r, "password": "password-123"})
    eng, eng_u = login("asha", "password-123")
    appr, appr_u = login("daniel", "password-123")
    viewer, _ = login("tom", "password-123")

    root = admin.call("GET", "/api/folders")["folders"][0]["id"]
    f = eng.call("POST", "/api/folders", {"parent_id": root, "name": "Mixers"})["folder"]["id"]
    viewer.call("POST", "/api/folders", {"parent_id": root, "name": "Nope"}, expect=403)

    # --- check-in a new drawing
    dxf_a = W.bearing_housing(80, 220, values={"PART_NO": "MX-200-001", "DESCRIPTION": "BEARING HOUSING", "MATERIAL": "EN-GJL-250"})
    r = eng.call("POST", "/api/documents/upload", files={"file": ("MX-200-001 Bearing Housing.dxf", dxf_a)},
                 fields={"folder_id": f, "comment": "first"})
    assert not r["errors"], r
    doc = r["documents"][0]
    assert doc["revision"] == "A" and doc["state"] == "in_work" and doc["version"] == 1
    assert doc["part_number"] == "MX-200-001"
    svg, ctype = eng.call("GET", f"/api/documents/{doc['id']}/preview", raw=True)
    assert ctype.startswith("image/svg") and b"<svg" in svg
    detail = eng.call("GET", f"/api/documents/{doc['id']}")
    assert detail["meta"]["title_block"]["material"] == "EN-GJL-250"
    assert detail["item"]["material"] == "EN-GJL-250"

    # upload again without check-out -> refused
    r = eng.call("POST", "/api/documents/upload", files={"file": ("MX-200-001 Bearing Housing.dxf", dxf_a + b"\n")},
                 fields={"folder_id": f})
    assert r["errors"] and r["errors"][0]["code"] == "not_checked_out", r

    # check-out locking
    eng.call("POST", f"/api/documents/{doc['id']}/checkout")
    appr.call("POST", f"/api/documents/{doc['id']}/checkout", expect=409)
    dxf_b = W.bearing_housing(80, 230, values={"PART_NO": "MX-200-001", "DESCRIPTION": "BEARING HOUSING", "MATERIAL": "EN-GJL-250"})
    d2 = eng.call("POST", f"/api/documents/{doc['id']}/checkin", files={"file": ("x.dxf", dxf_b)}, fields={"comment": "wider base"})
    assert d2["version"] == 2 and d2["checked_out_by"] is None
    body, _ = eng.call("GET", f"/api/documents/{doc['id']}/file?version=1", raw=True)
    assert body == dxf_a

    # lifecycle
    eng.call("POST", f"/api/documents/{doc['id']}/state", {"to": "in_review"})
    eng.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released"}, expect=403)
    rel = appr.call("POST", f"/api/documents/{doc['id']}/state", {"to": "released"})
    assert rel["state"] == "released"
    eng.call("POST", f"/api/documents/{doc['id']}/checkout", expect=409)
    eng.call("DELETE", f"/api/documents/{doc['id']}", expect=409)
    rv = eng.call("POST", f"/api/documents/{doc['id']}/revise")
    assert rv["revision"] == "B" and rv["state"] == "in_work"
    jobs = admin.call("GET", "/api/erp/jobs")["jobs"]
    assert any(j["kind"] == "item" for j in jobs)

    # --- BOM import (indented, SolidWorks style)
    asm_item = eng.call("POST", "/api/items", {"part_number": "MX-200-000", "description": "ROTOR ASSY"})["item"]
    bom_id = eng.call("POST", "/api/boms", {"item_id": asm_item["id"]})["bom_id"]
    csv_text = ("ITEM NO.,PART NUMBER,DESCRIPTION,QTY.,MATERIAL\n"
                "1,MX-200-001,BEARING HOUSING,2,EN-GJL-250\n"
                "2,MX-200-010,ROTOR SUB ASSY,1,\n"
                "2.1,MX-200-011,ROTOR DISC,1,1.4401\n"
                "2.2,STD-BLT-M16X60,HEX BOLT M16X60,8,\n"
                "3,MX-200-020,MAIN SHAFT,1,42CrMo4\n")
    r = eng.call("POST", f"/api/boms/{bom_id}/import", files={"file": ("bom.csv", csv_text.encode())})
    assert r["lines"] == 3 and r["sub_assemblies"] == ["MX-200-010"], r
    b = eng.call("GET", f"/api/boms/{bom_id}")
    assert len(b["tree"]) == 5 and b["tree"][2]["level"] == 2 and b["tree"][3]["ext_qty"] == 8
    assert any("Not released" in i["message"] for i in b["issues"])
    bolt = next(x for x in b["tree"] if x["part_number"] == "STD-BLT-M16X60")
    assert bolt["make_buy"] == "buy"
    xlsx, ctype = eng.call("GET", f"/api/boms/{bom_id}/export?format=xlsx", raw=True)
    assert xlsx[:2] == b"PK"

    # edit lines
    lines = [{"item_id": ln["item_id"], "qty": ln["qty"]} for ln in b["lines"]]
    lines[0]["qty"] = 4
    b = eng.call("PUT", f"/api/boms/{bom_id}/lines", {"lines": lines})
    assert b["lines"][0]["qty"] == 4

    # --- ERPNext push through the fake server
    admin.call("PUT", "/api/erp/settings", {"enabled": True, "dry_run": False, "base_url": f"http://127.0.0.1:{ERP_PORT}",
                                            "api_key": FE.KEY, "api_secret": FE.SECRET, "company": "Demo Engineering"})
    t = admin.call("POST", "/api/erp/test")
    assert t["ok"], t
    masked = admin.call("GET", "/api/erp/settings")
    assert masked["api_secret"] != FE.SECRET
    appr.call("POST", f"/api/boms/{bom_id}/release")
    appr.call("POST", "/api/erp/process")
    jobs = admin.call("GET", "/api/erp/jobs")["jobs"]
    bom_job = next(j for j in jobs if j["kind"] == "bom" and j["entity_id"] == bom_id)
    assert bom_job["status"] == "sent", bom_job
    assert "MX-200-000" in FE.STATE["items"] and "MX-200-010" in FE.STATE["items"], FE.STATE["items"].keys()
    erp_bom = next(v for v in FE.STATE["boms"].values() if v["item"] == "MX-200-000")
    assert erp_bom["docstatus"] == 1 and len(erp_bom["items"]) == 3
    # pushing the same BOM again is a no-op
    r = appr.call("POST", f"/api/boms/{bom_id}/push")
    assert r["job"]["status"] == "sent" and "unchanged" in r["job"]["response_text"], r
    # a failure is captured with ERPNext's message
    admin.call("PUT", "/api/erp/settings", {"api_secret": "wrong"})
    r = appr.call("POST", "/api/erp/push", {"kind": "item", "id": asm_item["id"]})
    assert r["job"]["status"] == "failed" and "Not permitted" in r["job"]["error"], r
    admin.call("PUT", "/api/erp/settings", {"api_secret": FE.SECRET})
    FE.STATE["bins"]["MX-200-001"] = [{"warehouse": "Stores - DE", "actual_qty": 6}]
    item1 = eng.call("GET", "/api/search?q=MX-200-001")["items"][0]
    r = appr.call("POST", "/api/erp/push", {"kind": "refresh", "id": item1["id"]})
    assert r["job"]["status"] == "sent"
    assert eng.call("GET", f"/api/items/{item1['id']}")["item"]["erp_stock"] == 6

    # --- ERPNext tells Kosha that stock moved: only a correctly signed call is accepted
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "addins", "erpnext"))
    from kosha_erpnext import signing
    admin.call("PUT", "/api/erp/settings", {"webhook_secret": "hook-secret-1"})
    FE.STATE["bins"]["MX-200-001"] = [{"warehouse": "Stores - DE", "actual_qty": 9}]
    body = json.dumps({"events": [{"kind": "stock_changed", "code": "MX-200-001"}]}).encode()
    hook = Client()
    bad_sig = signing.sign("wrong-secret", body)
    hook.call("POST", "/api/erp/webhook", raw_body=body, headers=bad_sig, expect=401)
    r = hook.call("POST", "/api/erp/webhook", raw_body=body, headers=signing.sign("hook-secret-1", body))
    assert r == {"accepted": 1, "queued": 1}, r
    appr.call("POST", "/api/erp/process")
    assert eng.call("GET", f"/api/items/{item1['id']}")["item"]["erp_stock"] == 9

    # --- change order
    co = eng.call("POST", "/api/changes", {"title": "Wider housing base", "reason": "Foundation bolts clash",
                                          "document_ids": [doc["id"]], "approver_ids": [appr_u["id"]]})
    err = eng.call("POST", f"/api/changes/{co['id']}/submit", expect=409)
    assert "interchangeability" in err["error"], err
    eng.call("PATCH", f"/api/changes/{co['id']}", {"ic_fit": "yes", "ic_mix": "yes", "ic_function": "yes"})
    eng.call("POST", f"/api/changes/{co['id']}/submit")
    appr.call("POST", f"/api/changes/{co['id']}/decide", {"decision": "rejected"}, expect=400)
    c = appr.call("POST", f"/api/changes/{co['id']}/decide", {"decision": "approved", "comment": "OK"})
    assert c["state"] == "approved"
    c = appr.call("POST", f"/api/changes/{co['id']}/implement")
    assert c["state"] == "implemented"
    d = eng.call("GET", f"/api/documents/{doc['id']}")
    assert d["state"] == "released" and d["revision"] == "B"
    assert any(e["change_number"] == co["number"] for e in d["events"])

    # --- SolidWorks add-in check-in with assembly structure
    stl = M.write_stl(M.shaft_mesh((100, 200), (60, 80)))
    r = eng.call("POST", "/api/addin/checkin",
                 files={"file": ("MX-300-000 Drive Assembly.SLDASM", b"fake-solidworks-bytes"), "mesh": ("m.stl", stl)},
                 fields={"folder": "Kosha/Products/Drive", "props": json.dumps({"PartNo": "MX-300-000",
                                                                                "Description": "DRIVE ASSEMBLY"}),
                         "structure": json.dumps([{"file": "MX-300-010 Pulley.SLDPRT", "part_number": "MX-300-010",
                                                   "description": "PULLEY", "qty": 2},
                                                  {"file": "MX-300-020 Belt.SLDPRT", "part_number": "MX-300-020",
                                                   "description": "V BELT", "qty": 4}])})
    assert r["doc_type"] == "assembly" and r["bom"]["lines"] == 2 and r["bom"]["quantities_verified"], r
    st = eng.call("GET", "/api/addin/status?folder=Kosha/Products/Drive&filename=MX-300-000%20Drive%20Assembly.SLDASM")
    assert st["exists"] and st["actions"]["checkout"]["ok"]
    sim = eng.call("GET", "/api/addin/similar?x=300&y=80&z=80&volume=1300000&area=90000&threshold=0.5")
    assert "similar" in sim

    # --- common parts
    legacy = eng.call("POST", "/api/folders", {"parent_id": root, "name": "Legacy"})["folder"]["id"]
    for pn, bore, base in (("OLD-1", 80, 220), ("OLD-2", 80.5, 221), ("OLD-3", 80, 224)):
        eng.call("POST", "/api/documents/upload", fields={"folder_id": legacy},
                 files={"file": (f"{pn} Housing.dxf", W.bearing_housing(bore, base, values={"PART_NO": pn,
                                                                                            "DESCRIPTION": "HOUSING"}))})
    eng.call("POST", "/api/documents/upload", fields={"folder_id": legacy},
             files={"file": ("OLD-9 Flange.dxf", W.flange(300, 250, 8, 22, 150, values={"PART_NO": "OLD-9"}))})
    eng.call("POST", "/api/common-parts/analyze", {"folder_id": legacy, "threshold": 0.9})
    for _ in range(100):
        run = eng.call("GET", "/api/common-parts/runs/latest")["run"]
        if run["status"] != "running":
            break
        time.sleep(0.1)
    groups = eng.call("GET", "/api/common-parts/groups")["groups"]
    assert len(groups) == 1 and groups[0]["n"] == 3, groups
    g = eng.call("GET", f"/api/common-parts/groups/{groups[0]['id']}")
    ref = next(m for m in g["members"] if m["is_reference"])
    eng.call("POST", f"/api/common-parts/groups/{g['id']}/decide",
             {"decision": "standardized", "standard_document_id": ref["document"]["id"]})
    dup = next(m for m in g["members"] if not m["is_reference"])
    it = eng.call("GET", f"/api/items/{dup['document']['item_id']}")
    assert it["standard_part"]["part_number"] == ref["document"]["part_number"]

    # --- search / dashboard / permissions
    s = viewer.call("GET", "/api/search?q=housing")
    assert len(s["documents"]) >= 4
    dash = eng.call("GET", "/api/dashboard")
    assert "checked_out" in dash
    viewer.call("POST", f"/api/documents/{doc['id']}/revise", expect=403)
    admin.call("GET", "/api/audit")
    print("ALL API TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
