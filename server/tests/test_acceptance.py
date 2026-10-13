# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release acceptance tests A1 to A14 (specification 19.7), automated as end-to-end scenarios against real Kosha
server processes, a simulated ERPNext and, where a scenario needs one, add-ins written into a temporary folder.

Each test is one connected flow named after its scenario; its docstring quotes the "passes when" line and says
plainly which part of the scenario is not built in Kosha (those parts are not faked, only named).

Servers:
* ``k``           an empty installation with the simulated ERPNext connected and a deliberately faulty add-in
                  waiting for approval (A1, A2, A4 to A6, A8, A10 to A14);
* ``demo_kosha``  the Demo Engineering data set (A3, A9), from conftest.py;
* A7 and A8 start their own servers (one killed hard and started again, one restored from a backup).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import types
import urllib.parse

import pytest

from erp_helpers import REPO, drain_erp, serve
from harness import SERVER_DIR, TEST_DATABASE_URL, Client, KoshaServer, free_port

import fake_erpnext as FE  # noqa: E402  (found through erp_helpers)
from kosha.cad import dxfwriter as W  # noqa: E402

sys.path.insert(0, str(REPO / "addins" / "erpnext"))
from kosha_erpnext import signing  # noqa: E402

WEBHOOK_SECRET = "acceptance-hook-1"
FAULTY = "com.example.faulty-notifier"
AI_PROVIDER = "org.kosha.openai-compatible"
UNREACHABLE = "The AI assistant could not be reached; your work is safe. Try again later or ask your administrator."

FAULTY_CODE = '''
from kosha_sdk import Health
from kosha_sdk.events import on_event


class Main:
    def __init__(self, ctx):
        self.ctx = ctx

    def health(self):
        return Health.up("loaded")


@on_event("item.created")
def tell_someone(ctx, event):
    raise RuntimeError("deliberately broken for acceptance test A14")
'''


# ------------------------------------------------------------------------------------------------ helpers --

def _make_faulty_addin(root) -> None:
    folder = root / "faulty-notifier"
    (folder / "faulty_notifier_addin").mkdir(parents=True)
    (folder / "kosha-addin.yaml").write_text(textwrap.dedent(f"""\
        id: {FAULTY}
        name: Faulty notifier
        version: 1.0.0
        publisher: Example
        licence: Apache-2.0
        kosha: ">=0.5,<2.0"
        kind: [notifier]
        entry_points: {{server: "faulty_notifier_addin.main:Main"}}
        permissions: [notify:send]
        events: [item.created]
        """))
    (folder / "faulty_notifier_addin" / "__init__.py").write_text("")
    (folder / "faulty_notifier_addin" / "main.py").write_text(FAULTY_CODE)


def _tb(pn, desc, material, rev="A"):
    return {"PART_NO": pn, "DESCRIPTION": desc, "MATERIAL": material, "REV": rev}


def _root(c) -> int:
    return next(f["id"] for f in c.call("GET", "/api/folders")["folders"] if not f["parent_id"])


def _folder(c, parent, name) -> int:
    return c.call("POST", "/api/folders", {"parent_id": parent, "name": name})["folder"]["id"]


def _upload(c, folder_id, name, data, **fields) -> dict:
    r = c.call("POST", "/api/documents/upload", files={"file": (name, data)}, fields={"folder_id": folder_id, **fields})
    assert not r["errors"], r
    return r["documents"][0]


def _release(approver, doc_id, reason="") -> dict:
    return approver.call("POST", f"/api/documents/{doc_id}/state", {"to": "released", "reason": reason})


def _item(c, pn) -> dict:
    return next(i for i in c.call("GET", f"/api/items?q={urllib.parse.quote(pn)}")["items"] if i["part_number"] == pn)


def _uid(c) -> int:
    return c.call("GET", "/api/auth/me")["user"]["id"]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _webhook(base, events, expect=200):
    """A call from the ERP: signed with the shared secret; the ERP does not sign in."""
    body = json.dumps({"events": events}).encode()
    return Client(base).call("POST", "/api/erp/webhook", raw_body=body, expect=expect,
                             headers={"Content-Type": "application/json", **signing.sign(WEBHOOK_SECRET, body)})


def _wait(check, timeout=30.0, what="the condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = check()
        if r:
            return r
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def _erp_boms(code) -> list[dict]:
    return sorted((b for b in FE.STATE["boms"].values() if b["item"] == code), key=lambda b: b["name"])


def _cli(server, *args, expect=0) -> str:
    env = dict(os.environ, KOSHA_DATA_DIR=str(server.data_dir / "data"))
    p = subprocess.run([sys.executable, "-m", *args], cwd=SERVER_DIR, env=env, capture_output=True, text=True,
                       timeout=300)
    assert p.returncode == expect, f"{args}: exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    return p.stdout + p.stderr


# ------------------------------------------------------------------------------------------------ servers --

@pytest.fixture(scope="module")
def erpnext():
    FE.STATE.update({"items": {}, "boms": {}, "bins": {}, "files": {}, "calls": []})
    server, url = serve(FE.app)
    yield url
    server.should_exit = True


@pytest.fixture(scope="module")
def k(tmp_path_factory, erpnext):
    """Empty installation, ERPNext (simulated) connected, the faulty add-in installed but not yet approved."""
    root = tmp_path_factory.mktemp("addins")
    _make_faulty_addin(root)
    s = KoshaServer(env={"KOSHA_ADDIN_DIR": str(root), "KOSHA_ADDIN_MAX_FAILURES": "3"}).start()
    admin = s.client()
    admin.call("PUT", "/api/erp/settings", {
        "enabled": True, "dry_run": False, "base_url": erpnext, "api_key": FE.KEY, "api_secret": FE.SECRET,
        "company": "Demo Engineering", "oem_field": "custom_kosha_oem_refs", "revision_field": "custom_kosha_revision",
        "webhook_secret": WEBHOOK_SECRET})
    assert admin.call("POST", "/api/erp/test")["ok"]
    ns = types.SimpleNamespace(
        server=s, base=s.base, admin=admin,
        asha=s.add_user("asha", "engineer", full_name="Asha Menon"),
        daniel=s.add_user("daniel", "approver", full_name="Daniel Okafor"),
        sofia=s.add_user("sofia", "approver", full_name="Sofia Lindqvist"),
        tom=s.add_user("tom", "viewer", full_name="Tom Becker"))
    ns.root = _root(admin)
    yield ns
    s.stop()


# --------------------------------------------------------------------------------------------------- A1 --

def test_a1_new_machine_for_a_customer_order(k):
    """A1 New machine for a customer order. Passes when: the project is created from the ERP sales order; the
    EBOM is built from CAD data; release creates the items and the BOM in the ERP.

    Not available here: SolidWorks itself. The CAD data are DXF drawings (Kosha reads their title blocks) and the
    BOM table as SolidWorks exports it (indented CSV), imported on the assembly. The ERP is the simulated ERPNext.
    """
    asha, daniel = k.asha, k.daniel
    # the sales order is booked in the ERP; the ERP tells Kosha, and the project appears with its folders
    r = _webhook(k.base, [{"kind": "sales_order", "code": "SO-26-0101", "detail": "Example Dairy|2027-01-20"}])
    assert r["accepted"] == 1
    prj = next(p for p in asha.call("GET", "/api/projects")["projects"] if p["sales_order"] == "SO-26-0101")
    assert prj["customer"] == "Example Dairy" and prj["due_date"] == "2027-01-20" and prj["code"].startswith("PRJ-")
    _webhook(k.base, [{"kind": "sales_order", "code": "SO-26-0101", "detail": "Example Dairy|2027-01-20"}])
    assert sum(p["sales_order"] == "SO-26-0101" for p in asha.call("GET", "/api/projects")["projects"]) == 1
    design = next(f["id"] for f in asha.call("GET", "/api/folders")["folders"]
                  if f["parent_id"] == prj["folder_id"] and f["name"] == "Design")

    # drawings checked in: the parts come from the title blocks
    housing = _upload(asha, design, "A1-100-001 Bearing Housing.dxf",
                      W.bearing_housing(80, 220, values=_tb("A1-100-001", "BEARING HOUSING", "EN-GJL-250")))
    shaft = _upload(asha, design, "A1-100-002 Drive Shaft.dxf",
                    W.shaft(values=_tb("A1-100-002", "DRIVE SHAFT", "42CrMo4")))
    assert (housing["part_number"], shaft["part_number"]) == ("A1-100-001", "A1-100-002")
    assert _item(asha, "A1-100-001")["material"] == "EN-GJL-250"

    # the EBOM from the CAD BOM export, on the machine's assembly, linked to the project
    asm = asha.call("POST", "/api/items", {"part_number": "A1-100-000", "description": "MIXER 500 L"})["item"]
    asha.call("POST", f"/api/projects/{prj['id']}/items", {"item_id": asm["id"]})
    bom_id = asha.call("POST", "/api/boms", {"item_id": asm["id"]})["bom_id"]
    csv_text = ("ITEM NO.,PART NUMBER,DESCRIPTION,QTY.,MATERIAL\n"
                "1,A1-100-001,BEARING HOUSING,2,EN-GJL-250\n"
                "2,A1-100-002,DRIVE SHAFT,1,42CrMo4\n"
                "3,STD-BLT-M16X60,HEX BOLT M16X60,8,\n")
    r = asha.call("POST", f"/api/boms/{bom_id}/import", files={"file": ("A1-100-000 BOM.csv", csv_text.encode())})
    assert r["lines"] == 3, r
    bom = asha.call("GET", f"/api/boms/{bom_id}")
    assert [(ln["part_number"], ln["qty"]) for ln in bom["lines"]] == [
        ("A1-100-001", 2), ("A1-100-002", 1), ("STD-BLT-M16X60", 8)]
    project = asha.call("GET", f"/api/projects/{prj['id']}")["project"]
    assert "A1-100-000" in [i["part_number"] for i in project["items"]]

    # release: the drawings, then the BOM; the ERP gets every item and the BOM
    for d in (housing, shaft):
        assert _release(daniel, d["id"])["state"] == "released"
    daniel.call("POST", f"/api/boms/{bom_id}/release")
    jobs = drain_erp(k.admin)
    assert not [j for j in jobs if j["status"] == "failed"], jobs
    assert {"A1-100-000", "A1-100-001", "A1-100-002", "STD-BLT-M16X60"} <= set(FE.STATE["items"])
    assert FE.STATE["items"]["A1-100-001"]["description"].upper().startswith("BEARING HOUSING")
    erp_bom = _erp_boms("A1-100-000")[-1]
    assert erp_bom["docstatus"] == 1 and erp_bom["company"] == "Demo Engineering"
    assert sorted((r["item_code"], r["qty"]) for r in erp_bom["items"]) == [
        ("A1-100-001", 2), ("A1-100-002", 1), ("STD-BLT-M16X60", 8)]


# --------------------------------------------------------------------------------------------------- A2 --

def test_a2_oem_spare_enquiry(k):
    """A2 OEM spare enquiry. Passes when: the part is found by its OEM number quickly; a new spare created with an
    OEM reference shows it in the ERP push."""
    admin, asha, daniel, tom = k.admin, k.asha, k.daniel, k.tom
    admin.call("POST", "/api/oems", {"code": "EXM", "name": "Example Mixer Works"})
    sleeve = _upload(asha, k.root, "A2-200-021 Shaft Sleeve.dxf",
                     W.flange(120, 100, 4, 10, 60, values=_tb("A2-200-021", "SHAFT SLEEVE", "1.4401")))
    sleeve_item = _item(asha, "A2-200-021")
    asha.call("POST", f"/api/items/{sleeve_item['id']}/oem-refs", {
        "oem": "EXM", "oem_model": "EM-500", "oem_part_number": "S44.10-21", "source": "reverse_engineered",
        "fit": "direct"})
    _release(daniel, sleeve["id"])

    # enquiry 1: the customer quotes the OEM number, typed without the dots and dashes
    t0 = time.monotonic()
    matches = tom.call("GET", "/api/oem-refs?q=S441021")["matches"]
    assert time.monotonic() - t0 < 2.0
    assert [(m["part_number"], m["oem"], m["oem_part_number"]) for m in matches] == [
        ("A2-200-021", "EXM", "S44.10-21")]
    assert [i["part_number"] for i in tom.call("GET", "/api/search?q=S44.10-21")["items"]] == ["A2-200-021"]

    # enquiry 2: an OEM number Kosha does not know yet; a new spare part is made with the OEM reference
    assert tom.call("GET", "/api/oem-refs?q=S44.10-35")["matches"] == []
    seal = _upload(asha, k.root, "A2-200-035 Lip Seal Carrier.dxf",
                   W.flange(140, 110, 6, 10, 70, values=_tb("A2-200-035", "LIP SEAL CARRIER", "1.4401")))
    seal_item = _item(asha, "A2-200-035")
    done = asha.call("PATCH", f"/api/items/{seal_item['id']}", {"spare": True, "wear_part": True})["item"]
    assert (done["spare"], done["wear_part"]) == (1, 1)
    asha.call("POST", f"/api/items/{seal_item['id']}/oem-refs", {
        "oem": "EXM", "oem_model": "EM-500", "oem_part_number": "S44.10-35", "source": "reverse_engineered",
        "fit": "direct"})
    _release(daniel, seal["id"])
    jobs = drain_erp(k.admin)
    assert any(j["kind"] == "item" and j["entity_id"] == seal_item["id"] and j["status"] == "sent" for j in jobs)
    erp_item = FE.STATE["items"]["A2-200-035"]
    assert "EXM S44.10-35" in erp_item["custom_kosha_oem_refs"] and "EM-500" in erp_item["custom_kosha_oem_refs"]
    assert [m["part_number"] for m in tom.call("GET", "/api/oem-refs?q=s44-10-35")["matches"]] == ["A2-200-035"]


# --------------------------------------------------------------------------------------------------- A3 --

def test_a3_configured_product(demo_kosha):
    """A3 Configured product. Passes when: the same spec is found and offered for reuse; a new item gets the correct
    spec code. (Spec codes from the general-machinery pack, installed in the Demo Engineering data.)"""
    asha, sofia = demo_kosha.client("asha"), demo_kosha.client("sofia")
    gear = {"module": "1.5", "teeth": 57, "face_width_mm": 25, "material": "C45"}
    p = asha.call("POST", "/api/numbering/preview", {"scheme": "spec:spur-gear", "values": gear})
    assert p["ok"] and p["existing"] is None and p["part_number"] == "GR-1.5-57-25-C45", p
    first = asha.call("POST", "/api/numbering/allocate", {"scheme": "spec:spur-gear", "values": gear})
    assert not first["existing"] and first["item"]["part_number"] == "GR-1.5-57-25-C45"

    # a colleague configures the same gear, typing the values differently: the existing part is offered, not a copy
    same = {"module": "1,5", "teeth": "57", "face_width_mm": "25", "material": "c45"}
    p = sofia.call("POST", "/api/numbering/preview", {"scheme": "spec:spur-gear", "values": same})
    assert p["existing"]["id"] == first["item"]["id"] and "already exists" in p["message"]
    again = sofia.call("POST", "/api/numbering/allocate", {"scheme": "spec:spur-gear", "values": same})
    assert again["existing"] and again["item"]["id"] == first["item"]["id"]
    assert [i["part_number"] for i in asha.call("GET", "/api/items?q=GR-1.5-57")["items"]] == ["GR-1.5-57-25-C45"]

    # one value different: the near match is offered first; the new item gets its own, correct spec code
    near = {**gear, "teeth": 58}
    p = sofia.call("POST", "/api/numbering/preview", {"scheme": "spec:spur-gear", "values": near})
    assert p["existing"] is None and [n["part_number"] for n in p["near"]] == ["GR-1.5-57-25-C45"]
    assert p["near"][0]["differs"] == "Teeth: 57 instead of 58" and "differ in one value" in p["message"]
    new = sofia.call("POST", "/api/numbering/allocate", {"scheme": "spec:spur-gear", "values": near})
    assert not new["existing"] and new["item"]["part_number"] == "GR-1.5-58-25-C45"
    assert new["item"]["material"] == "C45 (1.0503)" and new["item"]["description"].startswith("Spur gear m1.5 z58")
    assert "GR-1.5-57-25-C45" in [m["part_number"] for m in new["existing_matches"]]


# --------------------------------------------------------------------------------------------------- A4 --

def test_a4_change_to_a_released_part(k):
    """A4 Change to a released part. Passes when: the change log is drafted; interchangeability is recorded; the old
    revision is superseded; the ERP BOM is updated with effectivity.

    The change log here is the rule-based draft from the measured differences (no AI). Effectivity is recorded on
    the change order and the ERP transfer names the change order; the ERP BOM itself has no effectivity field in
    Kosha's ERP interface, so the effectivity date is not sent to the ERP (not built).
    """
    asha, daniel, admin = k.asha, k.daniel, k.admin
    f = _folder(admin, k.root, "A4 Agitator")
    shaft = _upload(asha, f, "A4-100-020 Agitator Shaft.dxf",
                    W.shaft(diameters=(80, 95, 110, 95, 80), values=_tb("A4-100-020", "AGITATOR SHAFT", "42CrMo4")))
    key = _upload(asha, f, "A4-100-050 Key.dxf",
                  W.flange(60, 40, 2, 6, 20, values=_tb("A4-100-050", "KEY 28X16X100", "C45")))
    ga = _upload(asha, f, "A4-100-000 Agitator Assembly.dxf",
                 W.flange(400, 340, 8, 18, 120, values=_tb("A4-100-000", "AGITATOR ASSEMBLY", "S235")))
    for d in (shaft, key, ga):
        _release(daniel, d["id"])
    asm_id = ga["item_id"]
    bom_a = asha.call("POST", "/api/boms", {"item_id": asm_id, "revision": "A"})["bom_id"]
    asha.call("PUT", f"/api/boms/{bom_a}/lines", {"lines": [
        {"part_number": "A4-100-020", "qty": 1}, {"part_number": "A4-100-050", "qty": 1}]})
    daniel.call("POST", f"/api/boms/{bom_a}/release")
    drain_erp(admin)
    assert len(_erp_boms("A4-100-000")) == 1

    # the change order: hub seat larger (with a new spacer ring), from a date, interchangeable both ways
    spacer = _upload(asha, f, "A4-100-030 Spacer Ring.dxf",
                     W.flange(130, 116, 0, 0, 115, values=_tb("A4-100-030", "SPACER RING", "S235")))
    co = asha.call("POST", "/api/changes", {
        "title": "Hub seat Ø110 to Ø115", "reason": "Fretting at the impeller hub seat",
        "document_ids": [shaft["id"], ga["id"], spacer["id"]], "approver_ids": [_uid(daniel)], "effectivity": "date",
        "effective_date": "2026-11-02", "ic_fit": "yes", "ic_mix": "yes", "ic_function": "yes"})
    asha.call("POST", f"/api/changes/{co['id']}/revise")
    asha.call("POST", f"/api/documents/{shaft['id']}/checkout")
    d = asha.call("POST", f"/api/documents/{shaft['id']}/checkin", fields={"comment": "Hub seat Ø115"}, files={
        "file": ("x.dxf", W.shaft(diameters=(80, 95, 115, 95, 80), values=_tb("A4-100-020", "AGITATOR SHAFT",
                                                                              "42CrMo4", "B")))})
    assert d["revision"] == "B"
    bom_b = asha.call("POST", "/api/boms", {"item_id": asm_id, "revision": "B"})["bom_id"]
    asha.call("PUT", f"/api/boms/{bom_b}/lines", {"lines": [
        {"part_number": "A4-100-020", "qty": 1}, {"part_number": "A4-100-050", "qty": 1},
        {"part_number": "A4-100-030", "qty": 1}]})
    daniel.call("POST", f"/api/boms/{bom_b}/release")

    # the change log is drafted from what was measured, and saved
    log = asha.call("GET", f"/api/changes/{co['id']}/changelog")
    shaft_group = next(g for g in log["input"]["groups"] if g["document"]["id"] == shaft["id"])
    assert "Diameter Ø110 changed to Ø115" in [c["text"] for c in shaft_group["changes"]]
    assert "Diameter Ø110 changed to Ø115" in log["draft"]["what"]
    draft = asha.call("POST", f"/api/changes/{co['id']}/changelog/draft",
                      {"note": "Fretting at the impeller hub seat."})["draft"]
    saved = asha.call("PUT", f"/api/changes/{co['id']}/changelog", {"note": "Fretting at the impeller hub seat.",
                                                                    "sections": draft})
    assert saved["saved"]["sections"]["what"] == draft["what"]

    # interchangeability and effectivity are recorded with the change
    page = asha.call("GET", f"/api/changes/{co['id']}")
    assert (page["ic_fit"], page["ic_mix"], page["ic_function"]) == ("yes", "yes", "yes")
    assert page["interchangeability"]["result"] == "same" and page["interchangeability"]["needed"]
    assert page["effectivity_text"] == "From 2026-11-02, for new work orders"

    asha.call("POST", f"/api/changes/{co['id']}/submit")
    daniel.call("POST", f"/api/changes/{co['id']}/decide", {"decision": "approved"})
    done = daniel.call("POST", f"/api/changes/{co['id']}/implement")
    assert done["state"] == "implemented"

    # the old revision is superseded
    doc = asha.call("GET", f"/api/documents/{shaft['id']}")
    assert (doc["state"], doc["revision"]) == ("released", "B")
    rev_a = [v for v in doc["versions"] if v["revision"] == "A"]
    assert rev_a and all(v["superseded_by"] == "B" for v in rev_a)
    assert any(e["change_number"] == co["number"] and e["to_state"] == "released" for e in doc["events"])

    # the ERP gets the new BOM revision, sent because of the change order; the old one stays as it was
    jobs = drain_erp(admin)
    bom_job = next(j for j in jobs if j["kind"] == "bom" and j["entity_id"] == bom_b)
    assert bom_job["status"] == "sent" and bom_job["trigger_ref"] == co["number"], bom_job
    old, new = _erp_boms("A4-100-000")
    assert old["is_default"] == 0 and old["custom_kosha_revision"] == "A"
    assert new["is_default"] == 1 and new["docstatus"] == 1 and new["custom_kosha_revision"] == "B"
    assert sorted(r["item_code"] for r in new["items"]) == ["A4-100-020", "A4-100-030", "A4-100-050"]


# --------------------------------------------------------------------------------------------------- A5 --

def _import(admin, src, target, **extra) -> dict:
    r = admin.call("POST", "/api/import/start", fields={"source": str(src), "target": target, "groups": "cad2d,pdf",
                                                         **extra})
    jid = r["job"]["id"]
    return _wait(lambda: next((j for j in admin.call("GET", "/api/import/jobs")["jobs"]
                               if j["id"] == jid and j["status"] != "running"), None), 60, "the import")


def test_a5_legacy_batch(k, tmp_path):
    """A5 Legacy batch. Passes when: the reconciliation adds up; the sample audit is at 98 % or better; undo of a test
    batch leaves no trace.

    "No trace" means no file, part, folder or search hit is left; by Kosha's no-data-loss rule the undone files
    wait in the recycle bin (restorable by an admin) instead of being destroyed."""
    admin, asha = k.admin, k.asha
    src = tmp_path / "Old server"
    for n, (desc, mat) in enumerate((("BEARING HOUSING", "EN-GJL-250"), ("SHAFT", "C45"), ("FLANGE", "S235"),
                                     ("COVER", "S235"), ("BRACKET", "S275JR"), ("GUSSET", "S235")), start=1):
        pn = f"A5-LB-{n:03d}"
        (src / "2014").mkdir(parents=True, exist_ok=True)
        (src / "2014" / f"{pn} {desc.title()}.dxf").write_bytes(
            W.flange(100 + 10 * n, 80 + 10 * n, 4, 10, 40, values=_tb(pn, desc, mat, "A")))
    (src / "2014" / "Copy of A5-LB-001 Bearing Housing.dxf").write_bytes(
        (src / "2014" / "A5-LB-001 Bearing Housing.dxf").read_bytes())
    (src / "2014" / "A5-LB-002 Shaft.dxf.bak").write_bytes(b"junk")

    j = _import(admin, src, "Legacy/A5 Old server")
    assert j["status"] == "done" and j["imported"] == 6 and j["duplicates"] == 1, j
    rec = asha.call("GET", f"/api/import/jobs/{j['id']}/reconcile")
    assert rec["found"] == 8 and rec["adds_up"] and sum(rec["by_status"].values()) == rec["found"], rec
    assert admin.call("POST", f"/api/import/jobs/{j['id']}/verify")["bad"] == []

    # the sample audit: below 98 % it cannot be signed off; corrected and checked again, it can
    sample = admin.call("POST", f"/api/import/jobs/{j['id']}/sample")["sample"]
    picked = [r for r in asha.call("GET", f"/api/import/jobs/{j['id']}/records?status=imported")["records"]
              if r["sample_result"] == "pending"]
    assert sample["size"] == len(picked) >= 5
    asha.call("PUT", f"/api/import/records/{picked[0]['id']}/sample", {"result": "wrong"})
    for r in picked[1:]:
        asha.call("PUT", f"/api/import/records/{r['id']}/sample", {"result": "ok"})
    rec = asha.call("GET", f"/api/import/jobs/{j['id']}/reconcile")
    assert rec["sample"]["accuracy"] < 98 and not rec["can_sign_off"]
    assert "98 % is needed" in admin.call("POST", f"/api/import/jobs/{j['id']}/sign-off", expect=409)["error"]
    asha.call("PATCH", f"/api/import/records/{picked[0]['id']}", {"description": "CORRECTED", "confirm": True})
    asha.call("PUT", f"/api/import/records/{picked[0]['id']}/sample", {"result": "ok"})
    done = admin.call("POST", f"/api/import/jobs/{j['id']}/sign-off")
    assert done["signed_off_at"] and done["sample"]["accuracy"] >= 98.0 and done["adds_up"]

    # a test batch, undone: nothing of it is left to find
    trial = tmp_path / "Trial"
    (trial / "Sub").mkdir(parents=True)
    (trial / "A5-TR-001 Plate.dxf").write_bytes(W.flange(100, 80, 4, 10, 40, values=_tb("A5-TR-001", "PLATE", "S235")))
    (trial / "Sub" / "A5-TR-002 Cover.dxf").write_bytes(
        W.flange(140, 110, 6, 10, 60, values=_tb("A5-TR-002", "COVER", "S235")))
    t = _import(admin, trial, "A5 Trial batch", state="in_work")
    assert t["imported"] == 2 and len(asha.call("GET", "/api/search?q=A5-TR")["documents"]) == 2
    plan = admin.call("GET", f"/api/import/jobs/{t['id']}/undo")
    assert len(plan["remove"]) == 2 and plan["keep"] == []
    r = admin.call("POST", f"/api/import/jobs/{t['id']}/undo")
    assert (r["removed"], r["parts_removed"], r["kept"]) == (2, 2, [])
    found = asha.call("GET", "/api/search?q=A5-TR")
    assert (found["documents"], found["items"]) == ([], [])
    assert asha.call("GET", "/api/items?q=A5-TR")["items"] == []
    names = {f["name"] for f in admin.call("GET", "/api/folders")["folders"]}
    assert "A5 Trial batch" not in names and "Sub" not in names
    assert len(asha.call("GET", "/api/search?q=A5-LB")["documents"]) == 6          # the signed-off batch stays


# --------------------------------------------------------------------------------------------------- A6 --

def test_a6_inputs(k):
    """A6 Inputs. Passes when: a benchmark PDF is dropped, tagged, and found by search.

    Built and tested: upload (the drop zone's call), kind and tags, the PDF's text layer read in the background,
    full-text search, the tag filter and the tag list. Not built in Kosha: search by meaning and image search, so
    they are not tested here."""
    from kosha.inputs import _simple_pdf
    asha, tom = k.asha, k.tom
    pdf = _simple_pdf("Example gearbox benchmark", ["Helical gearbox, ratio 14:1",
                                                    "Measured efficiency 96.5 percent at rated torque"])
    r = asha.call("POST", "/api/inputs", files={"file": ("Gearbox benchmark 2026.pdf", pdf, "application/pdf")},
                  fields={"kind": "competitor_benchmark", "source": "Trade fair 2026",
                          "tags": json.dumps(["benchmark", "gearbox"])})
    assert r["errors"] == [] and len(r["inputs"]) == 1
    iid = r["inputs"][0]["id"]
    files = _wait(lambda: [f for f in asha.call("GET", f"/api/inputs/{iid}")["files"]
                           if f["status"] not in ("pending", "running")], 120, "the PDF text")
    assert files[0]["status"] == "done"

    q = lambda **p: tom.call("GET", "/api/inputs?" + urllib.parse.urlencode(p))  # noqa: E731
    hit = q(q="efficiency")
    assert [i["id"] for i in hit["inputs"]] == [iid] and hit["inputs"][0]["kind"] == "competitor_benchmark"
    assert [i["id"] for i in q(tag="benchmark")["inputs"]] == [iid]
    assert [i["id"] for i in q(q="gearbox", tag="gearbox")["inputs"]] == [iid]
    assert q(q="efficiency", tag="pumps")["total"] == 0
    assert {"benchmark", "gearbox"} <= {t["tag"] if isinstance(t, dict) else t
                                        for t in tom.call("GET", "/api/tags")["tags"]}
    data, _ = tom.call("GET", f"/api/inputs/{iid}/files/1", raw=True)
    assert data == pdf


# --------------------------------------------------------------------------------------------------- A7 --

def test_a7_pull_the_plug():
    """A7 Pull the plug. Passes when: the server process is killed hard during check-ins, and after the restart no
    confirmed check-in (one that got a 200 answer) is lost and every stored file matches its fingerprint."""
    s = KoshaServer().start()
    try:
        admin = s.client()
        eng = s.add_user("asha", "engineer")
        root = _root(admin)
        first = b"pump test log, version 1\n"
        log_doc = _upload(eng, root, "A7-800-000 Test Log.txt", first)
        confirmed = [(log_doc["id"], 1, _sha(first))]
        lock, stop = threading.Lock(), threading.Event()

        def new_files(lane):
            n = 0
            while not stop.is_set():
                data = os.urandom(30000) + f"lane {lane} file {n}".encode()
                try:
                    d = _upload(eng, root, f"A7-8{lane}{n:02d}-001 Notes.txt", data)
                except Exception:
                    return                          # the server is gone
                with lock:
                    confirmed.append((d["id"], d["version"], _sha(data)))
                n += 1

        def new_versions():
            n = 1
            while not stop.is_set():
                n += 1
                data = first.replace(b"1", str(n).encode()) + os.urandom(20000)
                try:
                    eng.call("POST", f"/api/documents/{log_doc['id']}/checkout")
                    d = eng.call("POST", f"/api/documents/{log_doc['id']}/checkin",
                                 files={"file": ("A7-800-000 Test Log.txt", data)}, fields={"comment": f"run {n}"})
                except Exception:
                    return
                with lock:
                    confirmed.append((log_doc["id"], d["version"], _sha(data)))

        workers = [threading.Thread(target=new_files, args=(i,)) for i in (1, 2)]
        workers.append(threading.Thread(target=new_versions))
        for t in workers:
            t.start()
        _wait(lambda: len(confirmed) >= 30, 60, "30 confirmed check-ins")
        s.proc.kill()                                   # SIGKILL, in the middle of the check-ins
        s.proc.wait(timeout=20)
        stop.set()
        for t in workers:
            t.join(timeout=150)
        with lock:
            confirmed = list(confirmed)

        # start again on the same data folder (and the same database)
        s._logf.close()
        if s.database_url:
            s.env = {**s.env, "KOSHA_DATABASE_URL": s.database_url}
        s.start()
        admin = s.client()
        for doc_id, version, sha in confirmed:
            data, _ = admin.call("GET", f"/api/documents/{doc_id}/file?version={version}", raw=True)
            assert _sha(data) == sha, f"document {doc_id} version {version} lost or changed"
        r = admin.call("POST", "/api/admin/health/fingerprints", {"everything": True})
        assert r["damaged"] == 0 and r["checked"] >= len(confirmed), r
        assert r["status"]["fingerprints"]["checked_last_30_days"] == r["status"]["fingerprints"]["files"]
        c = admin.call("POST", "/api/admin/health/consistency")
        assert c["ok"], c["problems"]
        # and work goes on
        _upload(s.client("asha", "password-123"), root, "A7-899-001 After Restart.txt", b"still here\n")
    finally:
        s.stop()


# --------------------------------------------------------------------------------------------------- A8 --

@pytest.mark.skipif(bool(TEST_DATABASE_URL), reason="the automated restore covers SQLite database copies; a "
                    "PostgreSQL backup is restored with pg_restore as the README describes")
def test_a8_restore(k, tmp_path):
    """A8 Restore. Passes when: a backup is restored on a test set-up with every fingerprint matching."""
    admin, asha = k.admin, k.asha
    doc = _upload(asha, k.root, "A8-900-001 Pump Datasheet.txt", b"flow 120 m3/h, head 32 m\n")
    asha.call("POST", f"/api/documents/{doc['id']}/checkout")
    asha.call("POST", f"/api/documents/{doc['id']}/checkin",
              files={"file": ("A8-900-001 Pump Datasheet.txt", b"flow 125 m3/h, head 32 m\n")})
    _upload(asha, k.root, "A8-900-002 Impeller.dxf",
            W.flange(250, 200, 6, 12, 60, values=_tb("A8-900-002", "IMPELLER", "1.4408")))

    bk = tmp_path / "backup"
    assert "Database saved and checked" in _cli(k.server, "kosha.backup", str(bk))
    test_setup = KoshaServer()
    try:
        assert "Restore complete and checked" in _cli(k.server, "kosha.restore", str(bk),
                                                      str(test_setup.data_dir / "data"))
        test_setup.start()
        admin2 = test_setup.client()
        r = admin2.call("POST", "/api/admin/health/fingerprints", {"everything": True})
        assert r["damaged"] == 0 and r["checked"] == r["status"]["fingerprints"]["files"] > 0, r
        docs = admin.call("GET", "/api/documents?limit=1000")["documents"]
        docs2 = admin2.call("GET", "/api/documents?limit=1000")["documents"]
        assert {d["id"] for d in docs} == {d["id"] for d in docs2}
        for d in docs:
            for v in range(1, d["version"] + 1):
                a, _ = admin.call("GET", f"/api/documents/{d['id']}/file?version={v}", raw=True)
                b, _ = admin2.call("GET", f"/api/documents/{d['id']}/file?version={v}", raw=True)
                assert _sha(a) == _sha(b), (d["filename"], v)
        assert admin2.call("POST", "/api/admin/health/consistency")["ok"]
    finally:
        test_setup.stop()


# --------------------------------------------------------------------------------------------------- A9 --

def test_a9_permissions(demo_kosha):
    """A9 Permissions. Passes when: a vendor account cannot see anything outside its project; a bulk download
    triggers the alarm."""
    admin, tom = demo_kosha.client(), demo_kosha.client("tom")
    admin.call("POST", "/api/users", {"username": "vendor-a9", "full_name": "Example Design Services",
                                      "role": "engineer", "password": "password-123", "kind": "vendor",
                                      "company": "Example Design Services"})
    vid = next(u["id"] for u in admin.call("GET", "/api/users")["users"] if u["username"] == "vendor-a9")
    prj = next(p for p in admin.call("GET", "/api/projects")["projects"] if p["code"] == "PRJ-2026-014")
    admin.call("PATCH", f"/api/users/{vid}", {"projects": [prj["id"]]})
    vendor = demo_kosha.client("vendor-a9", "password-123")

    # only its project: the project's parts and their files, nothing else
    assert [p["code"] for p in vendor.call("GET", "/api/projects")["projects"]] == ["PRJ-2026-014"]
    allowed = {i["part_number"] for i in vendor.call("GET", "/api/items")["items"]}
    project_parts = {i["part_number"] for i in admin.call("GET", f"/api/projects/{prj['id']}")["project"]["items"]}
    assert allowed and allowed <= project_parts
    everything = tom.call("GET", "/api/search?q=MX")["documents"] + tom.call("GET", "/api/search?q=CP")["documents"]
    seen = vendor.call("GET", "/api/search?q=MX")["documents"] + vendor.call("GET", "/api/search?q=CP")["documents"]
    assert seen and {d["part_number"] for d in seen} <= allowed
    outside = [d for d in everything if d["part_number"] not in allowed]
    assert outside
    for d in outside[:5]:
        vendor.call("GET", f"/api/documents/{d['id']}", expect=404)
        vendor.call("GET", f"/api/documents/{d['id']}/file", expect=404)
    other = next(p for p in admin.call("GET", "/api/projects")["projects"] if p["id"] != prj["id"])
    vendor.call("GET", f"/api/projects/{other['id']}", expect=404)
    vendor.call("GET", "/api/folders", expect=403)
    vendor.call("GET", "/api/users", expect=403)
    vendor.call("GET", "/api/dashboard", expect=403)

    # many downloads in a short time: downloads stop and the admins are told
    admin.call("PUT", "/api/admin/security", {"bulk_download_per_hour": 3})
    try:
        own = seen[0]
        for _ in range(3):
            vendor.call("GET", f"/api/documents/{own['id']}/file", raw=True)
        r = vendor.call("GET", f"/api/documents/{own['id']}/file", expect=423)
        assert r["code"] == "downloads_paused" and "administrator" in r["error"]
        vendor.call("GET", f"/api/documents/{own['id']}/preview", raw=True)          # viewing still works
        user = next(u for u in admin.call("GET", "/api/users")["users"] if u["id"] == vid)
        assert user["downloads_paused_at"]
        with demo_kosha.db() as con:
            assert con.execute("SELECT COUNT(*) FROM notifications WHERE title = 'Downloads paused: Example Design "
                               "Services'").fetchone()[0] >= 1
        assert any(e["action"] == "downloads_paused" for e in admin.call("GET", "/api/audit?limit=50")["entries"])
    finally:
        admin.call("PUT", "/api/admin/security", {"bulk_download_per_hour": 200})
        admin.call("POST", f"/api/users/{vid}/downloads/resume")


# -------------------------------------------------------------------------------------------------- A10 --

def test_a10_ai_off(k):
    """A10 AI off. Passes when: with the AI provider unreachable, non-AI functions work and the change log still
    lists the measured differences. (The official OpenAI-compatible provider, pointed at an address where nothing
    answers.)"""
    admin, asha, daniel = k.admin, k.asha, k.daniel
    nowhere = f"http://127.0.0.1:{free_port()}/v1"
    admin.call("PUT", f"/api/addins/{AI_PROVIDER}/settings", {"base_url": nowhere, "model": "example-model",
                                                               "timeout_s": 5})
    admin.call("PUT", "/api/ai/settings", {"provider": AI_PROVIDER, "features": {"changelog": True,
                                                                                 "classify": True}})
    try:
        assert asha.call("GET", "/api/ai/status")["on"]
        assert not admin.call("POST", "/api/ai/test")["ok"]

        # the everyday work without AI
        f = _folder(asha, k.root, "A10 Shafts")
        shaft = _upload(asha, f, "A10-700-020 Drive Shaft.dxf",
                        W.shaft(values=_tb("A10-700-020", "DRIVE SHAFT", "42CrMo4")))
        _release(daniel, shaft["id"])
        assert asha.call("POST", f"/api/documents/{shaft['id']}/revise")["revision"] == "B"
        asha.call("POST", f"/api/documents/{shaft['id']}/checkout")
        asha.call("POST", f"/api/documents/{shaft['id']}/checkin", files={"file": ("x.dxf", W.shaft(
            diameters=(80, 95, 115, 95, 80), values=_tb("A10-700-020", "DRIVE SHAFT", "42CrMo4", "B")))})
        assert [d["id"] for d in asha.call("GET", "/api/search?q=A10-700-020")["documents"]] == [shaft["id"]]
        bom = asha.call("POST", "/api/boms", {"part_number": "A10-700-000", "description": "DRIVE UNIT"})["bom_id"]
        asha.call("PUT", f"/api/boms/{bom}/lines", {"lines": [{"part_number": "A10-700-020", "qty": 1}]})
        data, _ = asha.call("GET", f"/api/documents/{shaft['id']}/file", raw=True)
        assert data.startswith(b"  0") or b"SECTION" in data

        # the AI draft fails in plain words; the measured change log is there all the same
        sug = asha.call("POST", "/api/ai/changelog", {"subject": "document", "id": shaft["id"]})
        sug = _wait(lambda: (lambda s: s if s["status"] != "pending" else None)(
            asha.call("GET", f"/api/ai/suggestions/{sug['id']}")), 30, "the AI answer")
        assert sug["status"] == "failed" and sug["error"] == UNREACHABLE
        log = asha.call("GET", f"/api/documents/{shaft['id']}/changelog")
        assert log["draft"]["what"].startswith("Compared with revision A")
        assert "Diameter Ø110 changed to Ø115" in log["draft"]["what"]
        draft = asha.call("POST", f"/api/documents/{shaft['id']}/changelog/draft", {"note": "Hub seat larger"})["draft"]
        saved = asha.call("PUT", f"/api/documents/{shaft['id']}/changelog", {"note": "Hub seat larger",
                                                                             "sections": draft})
        assert saved["saved"]["source"] == "rules"
    finally:
        admin.call("PUT", "/api/ai/settings", {"provider": ""})


# -------------------------------------------------------------------------------------------------- A11 --

def test_a11_copy_design(k):
    """A11 Copy design. Passes when: a machine (assembly with BOM) is copied with new numbers, and the references in
    the copy point to the new numbers.

    Tested through the CAD add-in's check-in call (the assembly structure as the SolidWorks add-in sends it): the
    copy's BOM and the copied assembly's recorded structure point to the new numbers and file names. Not built on
    the server: rewriting the references inside the SolidWorks files themselves; Kosha says so in its notes and the
    add-in updates them when the copy is opened in SolidWorks (not available here)."""
    asha = k.asha
    folder = "Kosha/Products/A11 Drive"
    for pn, desc in (("A11-300-010", "PULLEY"), ("A11-300-020", "BELT GUARD")):
        asha.call("POST", "/api/addin/checkin", files={"file": (f"{pn} {desc.title()}.SLDPRT", f"sw-{pn}".encode())},
                  fields={"folder": folder, "props": json.dumps({"PartNo": pn, "Description": desc,
                                                                 "Material": "S235"})})
    structure = [{"file": "A11-300-010 Pulley.SLDPRT", "part_number": "A11-300-010", "description": "PULLEY", "qty": 2},
                 {"file": "A11-300-020 Belt Guard.SLDPRT", "part_number": "A11-300-020", "description": "BELT GUARD",
                  "qty": 1},
                 {"file": "STD-BLT-M10X30.SLDPRT", "part_number": "STD-BLT-M10X30", "description": "HEX BOLT M10X30",
                  "qty": 4}]
    asm = asha.call("POST", "/api/addin/checkin",
                    files={"file": ("A11-300-000 Drive Assembly.SLDASM", b"sw-assembly")},
                    fields={"folder": folder, "props": json.dumps({"PartNo": "A11-300-000",
                                                                   "Description": "DRIVE ASSEMBLY"}),
                            "structure": json.dumps(structure)})
    assert asm["doc_type"] == "assembly" and asm["bom"]["lines"] == 3

    products = next(f["id"] for f in asha.call("GET", "/api/folders")["folders"] if f["name"] == "Products")
    body = {"document_id": asm["id"], "folder_id": products, "new_folder": "A11 Drive for SO-26-0111",
            "prefix": "A11-400-"}
    p = asha.call("POST", "/api/copy-design/preview", body)
    assert p["problems"] == []
    actions = {r["part_number"]: r["action"] for r in p["rows"]}
    assert actions["A11-300-000"] == actions["A11-300-010"] == actions["A11-300-020"] == "copy"
    assert actions["STD-BLT-M10X30"] == "reuse"

    res = asha.call("POST", "/api/copy-design", {**body, "key": "acceptance-a11-copy"})
    new_pn = {c["source"]["part_number"]: c["item"]["part_number"] for c in res["created"]}
    assert set(new_pn) == {"A11-300-000", "A11-300-010", "A11-300-020"}
    assert all(v.startswith("A11-400-") for v in new_pn.values()) and len(set(new_pn.values())) == 3
    made = {c["source"]["part_number"]: c for c in res["created"]}

    # the copy's BOM points to the new numbers (the standard bolt is reused)
    bom = asha.call("GET", f"/api/boms/{made['A11-300-000']['bom_id']}")
    assert bom["bom"]["part_number"] == new_pn["A11-300-000"]
    assert sorted((ln["part_number"], ln["qty"]) for ln in bom["lines"]) == sorted([
        (new_pn["A11-300-010"], 2), (new_pn["A11-300-020"], 1), ("STD-BLT-M10X30", 4)])
    # the copied assembly file's structure points to the new numbers and the new file names
    copy_doc = made["A11-300-000"]["documents"][0]
    meta = asha.call("GET", f"/api/documents/{copy_doc['id']}")["versions"][0]["meta"]
    refs = {c["part_number"]: c["file"] for c in meta["structure"]}
    assert set(refs) == {new_pn["A11-300-010"], new_pn["A11-300-020"], "STD-BLT-M10X30"}
    assert refs[new_pn["A11-300-010"]].startswith(new_pn["A11-300-010"])
    assert refs[new_pn["A11-300-020"]].startswith(new_pn["A11-300-020"])
    assert not any("A11-300-" in v for v in refs.values())
    assert any("SolidWorks" in n for n in res["notes"])
    # the original is untouched
    orig_bom = asha.call("GET", f"/api/boms/{asm['bom']['bom_id']}")
    assert {ln["part_number"] for ln in orig_bom["lines"]} == {"A11-300-010", "A11-300-020", "STD-BLT-M10X30"}


# -------------------------------------------------------------------------------------------------- A12 --

def test_a12_deviation_limited_to_one_order(k):
    """A12 Deviation limited to one order. Passes when: the deviation shows on that order and in that machine's
    as-built BOM view, and on no other order.

    In Kosha one customer order's machine is a machine build with its serial number and order reference; a deviation
    is limited to an order by that machine's serial number."""
    asha, daniel = k.asha, k.daniel
    bom = asha.call("POST", "/api/boms", {"part_number": "A12-500-000", "description": "SCREW CONVEYOR 300"})["bom_id"]
    asha.call("PUT", f"/api/boms/{bom}/lines", {"lines": [
        {"part_number": "A12-500-010", "description": "SCREW FLIGHT", "qty": 1},
        {"part_number": "A12-500-020", "description": "TROUGH", "qty": 1}]})
    daniel.call("POST", f"/api/boms/{bom}/release")
    item_id = asha.call("GET", f"/api/boms/{bom}")["bom"]["item_id"]
    m1 = asha.call("POST", "/api/machines", {"item_id": item_id, "family": "CV", "customer": "Example Cement",
                                            "order_ref": "SO-26-0201"})["machine"]
    m2 = asha.call("POST", "/api/machines", {"item_id": item_id, "family": "CV", "customer": "Example Quarry",
                                            "order_ref": "SO-26-0202"})["machine"]
    flight = _item(asha, "A12-500-010")

    dev = asha.call("POST", "/api/deviations", {
        "title": "Flight from 8 mm plate", "departure": "8 mm plate instead of 10 mm for the screw flight",
        "reason": "10 mm plate not in stock", "serials": m1["serial"], "affected": [{"item_id": flight["id"]}],
        "approver_ids": [_uid(daniel)]})
    asha.call("POST", f"/api/deviations/{dev['id']}/submit")
    dev = daniel.call("POST", f"/api/deviations/{dev['id']}/decide", {"decision": "approved"})
    assert dev["state"] == "active"

    # on that order and in that machine's as-built BOM
    order1 = asha.call("GET", "/api/machines?q=SO-26-0201")["machines"]
    assert [(m["serial"], m["deviations"]) for m in order1] == [(m1["serial"], [dev["number"]])]
    built = asha.call("GET", f"/api/items/{item_id}/bom/view?view=as_built&serial={m1['serial']}")
    rows = {r["part_number"]: r for r in built["rows"]}
    assert [d["number"] for d in rows["A12-500-010"]["deviations"]] == [dev["number"]]
    assert rows["A12-500-020"]["deviations"] == []
    # on no other order
    order2 = asha.call("GET", "/api/machines?q=SO-26-0202")["machines"]
    assert [(m["serial"], m["deviations"]) for m in order2] == [(m2["serial"], [])]
    other = asha.call("GET", f"/api/items/{item_id}/bom/view?view=as_built&serial={m2['serial']}")
    assert all(r["deviations"] == [] for r in other["rows"])


# -------------------------------------------------------------------------------------------------- A13 --

def test_a13_vendor_package(k):
    """A13 Vendor package. Passes when: a purchase order from the ERP gets the right revision's package; a later
    release warns."""
    asha, daniel = k.asha, k.daniel
    plate = _upload(asha, k.root, "A13-600-001 Base Plate.dxf",
                    W.flange(300, 250, 8, 22, 150, values=_tb("A13-600-001", "BASE PLATE", "S235")))
    _release(daniel, plate["id"])
    drain_erp(k.admin)

    _webhook(k.base, [{"kind": "purchase_order", "code": "PO-26-0301",
                       "detail": "Example Castings|A13-600-001"}])
    order = next(o for o in asha.call("GET", "/api/erp/orders")["orders"] if o["number"] == "PO-26-0301")
    assert order["party"] == "Example Castings" and order["status"] == "open"
    assert [(ln["code"], ln["revision"]) for ln in order["lines"]] == [("A13-600-001", "A")]
    t = asha.call("POST", f"/api/erp/orders/{order['id']}/package")["transmittal"]
    assert t["order_ref"] == "PO-26-0301" and t["recipient"] == "Example Castings"
    assert [(f["filename"], f["revision"]) for f in t["files"]] == [("A13-600-001 Base Plate.dxf", "A")]

    # revision B is released while the order is open: the sender is warned, the package says so
    asha.call("POST", f"/api/documents/{plate['id']}/revise")
    asha.call("POST", f"/api/documents/{plate['id']}/checkout")
    asha.call("POST", f"/api/documents/{plate['id']}/checkin", fields={"comment": "two more holes"}, files={
        "file": ("x.dxf", W.flange(300, 250, 10, 22, 150, values=_tb("A13-600-001", "BASE PLATE", "S235", "B")))})
    _release(daniel, plate["id"], "two more holes")
    page = asha.call("GET", f"/api/transmittals/{t['id']}")["transmittal"]
    assert page["superseded"][0]["sent_revision"] == "A" and page["superseded"][0]["new_revision"] == "B"
    titles = [n["title"] for n in asha.call("GET", "/api/notifications")["notifications"]]
    assert f"{t['number']}: A13-600-001 Base Plate.dxf has a newer revision" in titles, titles
    shared = Client(k.base).call("GET", f"/api/share/{t['token']}")
    assert next(f for f in shared["files"] if f["id"] == plate["id"])["superseded"]


# -------------------------------------------------------------------------------------------------- A14 --

def test_a14_faulty_addin(k):
    """A14 Faulty add-in. Passes when: a deliberately failing add-in is switched off automatically, and the rest of
    Kosha works. (The add-in crashes on every "item created" event; the limit is 3 failures in a row.)"""
    admin, asha, daniel = k.admin, k.asha, k.daniel
    a = admin.call("GET", f"/api/addins/{FAULTY}")
    assert a["status"] == "awaiting_approval"
    assert admin.call("POST", f"/api/addins/{FAULTY}/enable")["status"] == "enabled"
    assert admin.call("POST", f"/api/addins/{FAULTY}/health")["ok"]

    made = [asha.call("POST", "/api/items", {"part_number": f"A14-100-00{n}", "description": f"TEST PART {n}"})["item"]
            for n in range(1, 4)]
    off = _wait(lambda: (lambda x: x if x["status"] == "disabled" else None)(
        admin.call("GET", f"/api/addins/{FAULTY}")), 30, "the faulty add-in to be switched off")
    assert "automatically" in off["message"] and "3 failures" in off["message"]
    r = admin.call("POST", f"/api/addins/{FAULTY}/health")
    assert not r["ok"] and "switched off" in r["message"]

    # nothing was lost, and the rest of Kosha works
    for it in made:
        assert _item(asha, it["part_number"])["id"] == it["id"]
    asha.call("POST", "/api/items", {"part_number": "A14-100-004", "description": "TEST PART 4"})
    doc = _upload(asha, k.root, "A14-100-010 Bracket.dxf",
                  W.flange(160, 120, 4, 12, 50, values=_tb("A14-100-010", "BRACKET", "S235")))
    assert _release(daniel, doc["id"])["state"] == "released"
    assert [d["id"] for d in asha.call("GET", "/api/search?q=A14-100-010")["documents"]] == [doc["id"]]
    assert admin.call("GET", f"/api/addins/{FAULTY}")["status"] == "disabled"     # stays off until an admin acts
    others = {x["id"]: x["status"] for x in admin.call("GET", "/api/addins")["addins"]}
    assert others["org.kosha.erpnext"] == "enabled" and others[AI_PROVIDER] == "enabled"
