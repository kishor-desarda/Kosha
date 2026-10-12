# SPDX-License-Identifier: AGPL-3.0-or-later
"""Legacy import + duplicate detection test (messy folder tree, part master, re-run).

    python tests/test_import.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_api as T  # noqa: E402  (sets up a temporary data folder)

from kosha.cad import dxfwriter as W  # noqa: E402


def build_legacy(root: Path) -> Path:
    tb = lambda pn, d, m, r: {"PART_NO": pn, "DESCRIPTION": d, "MATERIAL": m, "REV": r}

    def put(rel, data):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    housing = W.bearing_housing(80, 220, values=tb("AG-BH-01", "BEARING HOUSING", "EN-GJL-250", "Rev. 2"))
    put("2009/Agitator AG-500/AG-BH-01 Bearing housing.dxf", housing)
    put("2009/Agitator AG-500/Copy of AG-BH-01 Bearing housing.dxf", housing)
    put("Backup D drive/old/AG-BH-01.dxf", housing)
    put("2013/Conveyor/SC-HSG-80 Housing.dxf", W.bearing_housing(80, 222, values=tb("SC-HSG-80", "HOUSING 80", "EN-GJL-250", "1")))
    put("2013/Conveyor/SC-SH-01 Shaft.dxf", W.shaft(values=tb("SC-SH-01", "CONVEYOR SHAFT", "42CrMo4", "R-3")))
    put("2015/Pump/SC-SH-01 Shaft rework.dxf",
        W.shaft((60, 80, 240, 80, 80), (80, 95, 110, 95, 80), values=tb("SC-SH-01", "SHAFT", "42CrMo4", "4")))
    put("2015/Pump/CP-FL-150.dxf", W.flange(285, 240, 8, 22, 150, values=tb("CP-FL-150", "FLANGE DN150", "S275JR", "0")))
    put("2015/Pump/CP-FL-150.dxf.bak", b"junk")
    put("2015/Pump/~$notes.docx", b"junk")
    put("2015/Pump/Thumbs.db", b"junk")
    put("2015/Pump/CP-200-000 Pump.SLDASM", b"stored as-is")
    meta = root.parent / "part_master.csv"
    meta.write_text("Item Code,Item Name,Material,UOM,Make/Buy\n"
                    "P-1001,BLT HEX M16 X 60 8.8,,Nos,Buy\n"
                    "P-2001,BRG 22220EK SPHERICAL ROLLER,,Nos,Buy\n"
                    "P-3001,GLAND PACKING 12 SQ,,Kg,Buy\n"
                    "P-3002,PACKING GLAND 12 SQ,,Kg,Buy\n"
                    "P-4001,MAIN SHAFT,42CrMo4,Nos,Make\n"
                    "P-4002,SHAFT MAIN,42CrMo4,Nos,Make\n")
    return meta


def wait_job(c, job_id):
    for _ in range(300):
        j = next(x for x in c.call("GET", "/api/import/jobs")["jobs"] if x["id"] == job_id)
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("import did not finish")


def main():
    T.serve("kosha.app:app", T.PORT)
    admin, _ = T.login("admin", "admin-pass-1")
    tmp = Path(tempfile.mkdtemp(prefix="legacy_"))
    src = tmp / "D-drive"
    meta = build_legacy(src)

    pv = admin.call("POST", "/api/import/preview", {"source": str(src)})
    assert pv["files"] == 11 and pv["selected_files"] == 8, pv
    admin.call("POST", "/api/import/preview", {"source": str(tmp / "nope")}, expect=400)

    # dry run first
    r = admin.call("POST", "/api/import/start", fields={"source": str(src), "target": "Legacy Drawings/D drive",
                                                         "dry_run": "1", "groups": "solidworks,cad2d"})
    j = wait_job(admin, r["job"]["id"])
    assert j["status"] == "done" and j["imported"] == 6 and j["duplicates"] == 2, j
    assert admin.call("GET", "/api/search?q=AG-BH-01")["documents"] == []

    # real import with the part master
    r = admin.call("POST", "/api/import/start",
                   fields={"source": str(src), "target": "Legacy Drawings/D drive", "groups": "solidworks,cad2d",
                           "state": "released"}, files={"metadata": ("part_master.csv", meta.read_bytes())})
    j = wait_job(admin, r["job"]["id"])
    assert j["status"] == "done" and j["imported"] == 6 and j["duplicates"] == 2 and j["errors"] == 0, j
    rep, ctype = admin.call("GET", f"/api/import/jobs/{j['id']}/report", raw=True)
    assert rep[:2] == b"PK"
    docs = admin.call("GET", "/api/search?q=AG-BH-01")["documents"]
    assert [d["filename"] for d in docs] == ["AG-BH-01 Bearing housing.dxf"], docs  # best-named copy kept
    assert docs[0]["state"] == "released" and docs[0]["revision"] == "2"
    shaft = admin.call("GET", "/api/search?q=CONVEYOR%20SHAFT")["documents"][0]
    assert shaft["revision"] == "3"
    items = {i["part_number"]: i for i in admin.call("GET", "/api/items?q=P-")["items"]}
    assert items["P-1001"]["make_buy"] == "buy" and items["P-3001"]["uom"] == "Kg"
    tree = [f["name"] for f in admin.call("GET", "/api/folders")["folders"]]
    assert "Agitator AG-500" in tree and "Backup D drive" not in tree  # folder with only a copy is not created

    # re-running is safe
    r = admin.call("POST", "/api/import/start", fields={"source": str(src), "target": "Legacy Drawings/D drive",
                                                         "groups": "solidworks,cad2d"})
    j = wait_job(admin, r["job"]["id"])
    assert j["imported"] == 0 and j["errors"] == 0, j

    # an identical copy that slipped in another way, plus a standard bolt
    other = admin.call("POST", "/api/folders", {"parent_id": 1, "name": "Shared"})["folder"]["id"]
    admin.call("POST", "/api/documents/upload", fields={"folder_id": other},
               files={"file": ("CP-FL-150 copy.dxf", (src / "2015/Pump/CP-FL-150.dxf").read_bytes())})
    # dropping a folder tree from the browser: sub-folders are recreated
    r = admin.call("POST", "/api/documents/upload", fields={"folder_id": other, "paths": json.dumps(["Sub A", "Sub A/Deep"])},
                   files={"file": (["A-1 Plate.dxf", "A-2 Plate.dxf"],
                                   [W.flange(100, 80, 4, 10, 40, values={"PART_NO": "A-1"}),
                                    W.flange(120, 90, 4, 10, 50, values={"PART_NO": "A-2"})])})
    assert len(r["documents"]) == 2 and not r["errors"], r
    names = {f["name"]: f for f in admin.call("GET", "/api/folders")["folders"]}
    assert names["Deep"]["parent_id"] == names["Sub A"]["id"] and names["Sub A"]["parent_id"] == other

    admin.call("POST", "/api/items", {"part_number": "STD-BLT-M16X60", "description": "HEX BOLT M16 X 60 GR 8.8",
                                      "make_buy": "buy"})

    admin.call("POST", "/api/common-parts/analyze", {"threshold": 0.9})
    for _ in range(100):
        run = admin.call("GET", "/api/common-parts/runs/latest")["run"]
        if run["status"] != "running":
            break
        time.sleep(0.1)
    groups = admin.call("GET", "/api/common-parts/groups")["groups"]
    by_kind = {}
    for g in groups:
        by_kind.setdefault(g["kind"], []).append(g)
    print(json.dumps({k: [(g["label"], g["n"]) for g in v] for k, v in by_kind.items()}, indent=1))
    assert len(by_kind.get("identical", [])) == 1
    assert any("SC-SH-01" in g["label"] for g in by_kind.get("part_number", []))
    assert any(g["n"] >= 2 for g in by_kind.get("geometry", []))
    desc = {frozenset(g["item_numbers"]) for g in by_kind.get("description", [])}
    assert frozenset({"STD-BLT-M16X60", "P-1001"}) in desc, desc
    assert frozenset({"P-3001", "P-3002"}) in desc, desc
    assert not any("P-4001" in s for s in desc)  # no sizes in text: never matched on words alone

    # decide: standard bolt
    g = next(g for g in by_kind["description"] if "P-1001" in g["item_numbers"])
    detail = admin.call("GET", f"/api/common-parts/groups/{g['id']}")
    std = next(m["item"]["id"] for m in detail["items"] if m["item"]["part_number"] == "STD-BLT-M16X60")
    admin.call("POST", f"/api/common-parts/groups/{g['id']}/decide", {"decision": "standardized", "standard_item_id": std})
    p1001 = admin.call("GET", "/api/search?q=P-1001")["items"][0]
    assert admin.call("GET", f"/api/items/{p1001['id']}")["standard_part"]["part_number"] == "STD-BLT-M16X60"

    # decide: identical copy -> keep one, retire the other
    g = by_kind["identical"][0]
    detail = admin.call("GET", f"/api/common-parts/groups/{g['id']}")
    keep = next(m["document"]["id"] for m in detail["members"] if m["is_reference"])
    res = admin.call("POST", f"/api/common-parts/groups/{g['id']}/decide", {"decision": "obsolete_others",
                                                                         "standard_document_id": keep})
    assert res["done"], res
    xl, _ = admin.call("GET", "/api/common-parts/report?status=all", raw=True)
    assert xl[:2] == b"PK"
    shutil.rmtree(tmp, ignore_errors=True)
    print("ALL IMPORT TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(T.DATA, ignore_errors=True)
