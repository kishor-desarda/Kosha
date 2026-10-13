# SPDX-License-Identifier: AGPL-3.0-or-later
"""Demo data for the invented company *Demo Engineering*: users, product folders,
real DXF drawings, STL models, revision histories, a BOM, change orders and legacy duplicates.

Every company, person and product name here is made up.

Run with:  python -m kosha --demo   (only seeds an empty installation)
"""
from __future__ import annotations

import io
import logging
import time

from . import boms, changes, db, erp, lifecycle, security, similarity
from .cad import dxfwriter as W
from .cad import mesh as M

log = logging.getLogger("kosha.seed")

DEMO_PASSWORD = "demo-kosha-1"

USERS = [
    ("asha", "Asha Menon", "engineer", "guided"),
    ("daniel", "Daniel Okafor", "approver", "expert"),
    ("sofia", "Sofia Lindqvist", "approver", "expert"),
    ("tom", "Tom Becker", "viewer", "guided"),
]

FOLDERS = [
    "Products/Mixers/MX-VA-1200 Vertical Agitator",
    "Products/Mixers/RB-500 Ribbon Blender",
    "Products/Conveyors/SC-300 Screw Conveyor",
    "Products/Conveyors/BC-650 Belt Conveyor",
    "Products/Pumps/CP-80 Centrifugal Pump",
    "Products/Gearboxes/HG-160 Helical Gearbox",
    "Standard Parts/Bearings and Housings",
    "Standard Parts/Fasteners",
    "Legacy Drawings/2008-2015",
    "Legacy Drawings/2016-2021",
]


def _user(username: str) -> dict:
    return db.one("SELECT id, username, full_name, role, ui_mode FROM users WHERE username = ?", (username,))


def _folder(path: str) -> int:
    return lifecycle.ensure_folder_path("Kosha/" + path, None)


def _put(user, folder, name, data: bytes, comment="", keep=False, props=None):
    return lifecycle.checkin(user, folder, name, io.BytesIO(data), comment, keep, props or {})


def _tb(pn, desc, mat, rev):
    return {"PART_NO": pn, "DESCRIPTION": desc, "MATERIAL": mat, "REV": rev}


def _seed_quality(asha, daniel, sofia, shaft_doc, housing_doc) -> None:
    """Two deviations (one active, one waiting for Sofia) and a discussion with mentions on the agitator shaft."""
    from datetime import timedelta

    from . import deviations, discussions
    key = db.one("SELECT id FROM items WHERE part_number = 'MX-100-050'")
    d1 = deviations.create(asha, {
        "title": "42CrMo4 instead of C45 for shaft keys, batch 12", "reason": "C45 bar is out of stock until week 48.",
        "departure": "material 42CrMo4 allowed instead of C45", "serials": "batch 12", "qty_limit": 20,
        "valid_until": (deviations.today() + timedelta(days=45)).isoformat(),
        "affected": [{"item_id": key["id"]}], "approver_ids": [daniel["id"]]})
    deviations.submit(asha, d1)
    deviations.decide(daniel, d1, "approved", "Fine: 42CrMo4 is stronger; keep the same heat treatment.")
    deviations.record_usage(asha, d1, 6, "batch 12, first lot")
    d2 = deviations.create(asha, {
        "title": "Drive-end housing from EN-GJS-400 castings", "reason": "EN-GJL-250 castings are on back order.",
        "departure": "housing machined from EN-GJS-400-15 castings instead of EN-GJL-250", "serials": "SN 2041 to 2044",
        "qty_limit": 4, "affected": [{"document_id": housing_doc["id"]}], "approver_ids": [sofia["id"]]})
    deviations.submit(asha, d2)
    tid = discussions.start(asha, "document", shaft_doc["id"], "@daniel the field report shows fretting at the hub "
                            "seat. Do we go to Ø115 with ECO-0001, or try a shrink fit first?", "Hub seat fretting")
    discussions.reply(daniel, tid, "Ø115 h6 is the safer fix. @sofia please check the impeller disc bore before we submit.")


def seed() -> None:
    if db.scalar("SELECT COUNT(*) FROM documents"):
        log.info("Kosha already has files; demo data not loaded.")
        return
    t0 = time.time()
    root = db.scalar("SELECT id FROM folders WHERE parent_id IS NULL AND name = 'Kosha'")
    if not root:
        db.insert("folders", {"parent_id": None, "name": "Kosha", "created_at": db.now()})
    for username, name, role, mode in USERS:
        if not db.one("SELECT id FROM users WHERE username = ?", (username,)):
            db.insert("users", {"username": username, "full_name": name, "email": f"{username}@example.com",
                                "role": role, "password_hash": security.hash_password(DEMO_PASSWORD), "ui_mode": mode,
                                "created_at": db.now()})
    for f in FOLDERS:
        _folder(f)
    asha, daniel, sofia = _user("asha"), _user("daniel"), _user("sofia")
    mx = _folder("Products/Mixers/MX-VA-1200 Vertical Agitator")
    std = _folder("Standard Parts/Bearings and Housings")
    leg1 = _folder("Legacy Drawings/2008-2015")
    leg2 = _folder("Legacy Drawings/2016-2021")
    conveyor = _folder("Products/Conveyors/SC-300 Screw Conveyor")
    pump = _folder("Products/Pumps/CP-80 Centrifugal Pump")

    def release(doc, who=daniel, comment="Checked and released"):
        return lifecycle.set_state(who, doc["id"], "released", comment)

    # --- Main shaft: three released revisions
    shaft_l, shaft_d = (60, 80, 220, 80, 80), (80, 95, 110, 95, 80)
    d = _put(asha, mx, "MX-100-020 Agitator Shaft.dxf",
             W.shaft(shaft_l, (80, 90, 100, 90, 80), values=_tb("MX-100-020", "AGITATOR SHAFT", "C45", "A")),
             "First issue")
    d = release(d)
    lifecycle.revise(asha, d["id"], "Material upgraded to 42CrMo4 after fatigue review")
    lifecycle.checkout(asha, d["id"])
    d = _put(asha, mx, "MX-100-020 Agitator Shaft.dxf",
             W.shaft(shaft_l, (80, 95, 105, 95, 80), values=_tb("MX-100-020", "AGITATOR SHAFT", "42CrMo4", "B")),
             "42CrMo4, bearing seats Ø95")
    d = release(d)
    lifecycle.revise(asha, d["id"], "Impeller hub seat enlarged")
    lifecycle.checkout(asha, d["id"])
    d = _put(asha, mx, "MX-100-020 Agitator Shaft.dxf",
             W.shaft(shaft_l, shaft_d, values=_tb("MX-100-020", "AGITATOR SHAFT", "42CrMo4", "C")), "Hub seat Ø110 h6")
    shaft_doc = release(d)
    m = _put(asha, mx, "MX-100-020 Agitator Shaft.stl", M.write_stl(M.shaft_mesh(shaft_l, shaft_d)), "3D model")
    release(m)

    # --- Impeller hub disc
    disc = M.disc_mesh(900, 110, 60, 220, 90)
    m = _put(asha, mx, "MX-100-010 Impeller Disc.stl", M.write_stl(disc), "3D model")
    release(m)
    d = _put(asha, mx, "MX-100-010 Impeller Disc.dxf",
             W.flange(od=900, pcd=700, holes=12, hole_d=22, bore=110, values=_tb("MX-100-010", "IMPELLER DISC", "1.4401 (316)", "B")),
             "Impeller disc drawing")
    disc_doc = release(d)

    # --- Bearing housings (standard part + legacy duplicates)
    hs = _put(daniel, std, "STD-BH-2208 Bearing Housing.dxf",
              W.bearing_housing(80, 220, 120, values=_tb("STD-BH-2208", "BEARING HOUSING 2208 SERIES", "EN-GJL-250", "A")),
              "Standard housing")
    release(hs, sofia)
    de = _put(asha, mx, "MX-100-030 Bearing Housing DE.dxf",
              W.bearing_housing(80, 220, 120, values=_tb("MX-100-030", "BEARING HOUSING DRIVE END", "EN-GJL-250", "B")), "")
    release(de)
    nde = _put(asha, mx, "MX-100-031 Bearing Housing NDE.dxf",
               W.bearing_housing(80, 220, 122, values=_tb("MX-100-031", "BEARING HOUSING NON DRIVE END", "EN-GJL-250", "B")), "")
    release(nde)
    for folder, pn, desc, bore, base in (
        (leg1, "BRG-HSG-80", "BEARING HOUSING 80 BORE", 80.0, 220.0),
        (leg1, "BH2208", "PLUMMER BLOCK 2208", 80.5, 220.0),
        (leg2, "HSG-80-OLD", "HOUSING FOR 22208", 80.0, 240.0),
        (leg2, "SC-BH-11", "SCREW CONVEYOR BEARING HOUSING", 80.0, 222.0),
    ):
        x = _put(asha, folder, f"{pn} {desc.title()}.dxf",
                 W.bearing_housing(bore, base, 120, values=_tb(pn, desc, "EN-GJL-250", "0")), "Imported from the old file share")
        release(x, sofia, "Legacy import")

    # --- Flanges (legacy duplicates)
    for folder, pn, desc, od, pcd, holes in (
        (pump, "CP-80-014", "INLET FLANGE DN150", 285, 240, 8),
        (leg1, "FL-150-PN10", "FLANGE DN150 PN10", 285, 240, 8),
        (leg2, "FLG-DN150", "PIPE FLANGE 150NB", 286, 240, 8),
        (conveyor, "SC-300-021", "OUTLET FLANGE DN100", 220, 180, 8),
    ):
        x = _put(asha, folder, f"{pn} {desc.title()}.dxf",
                 W.flange(od, pcd, holes, 22, od * 0.55, values=_tb(pn, desc, "S275JR", "A")), "")
        release(x, sofia)

    # --- other legacy shafts
    for folder, pn, desc, dia in ((leg1, "SH-MX-40", "MIXER SHAFT", (80, 95, 110, 95, 80)),
                                  (leg2, "SC-SH-200", "SCREW CONVEYOR DRIVE SHAFT", (70, 85, 100, 85, 70))):
        x = _put(asha, folder, f"{pn} {desc.title()}.dxf", W.shaft(shaft_l, dia, values=_tb(pn, desc, "42CrMo4", "1")), "")
        release(x, sofia)

    # --- general arrangement + assembly model + BOM
    ga = W.DxfWriter()
    ga.sheet(841, 594, _tb("MX-GA-001", "MX-VA-1200 VERTICAL AGITATOR GA", "-", "E"))
    ga.rect(120, 180, 520, 240)
    ga.rect(160, 120, 440, 60)
    ga.circle(380, 300, 110)
    ga.circle(380, 300, 40)
    for k in range(6):
        ga.rect(140 + k * 80, 80, 40, 40)
    ga.text(380, 450, 10, "MX-VA-1200 VERTICAL AGITATOR", "TEXT", align=1)
    g = _put(asha, mx, "MX-GA-001 General Arrangement.dxf", ga.tobytes(), "GA for customer approval")
    release(g, sofia)

    asm_tris = (M.translate(M.shaft_mesh(shaft_l, shaft_d), 0, 0, 0) +
                M.translate(M.disc_mesh(900, 110, 60, 220, 90), 140 + 60, 0, 0) +
                M.translate(M.box_mesh(110, 260, 210), -15, -130, -150) +
                M.translate(M.box_mesh(110, 260, 210), 425, -130, -150) +
                M.translate(M.box_mesh(640, 420, 30), -60, -210, -180))
    asm = _put(asha, mx, "MX-100-000 Agitator Assembly.stl", M.write_stl(asm_tris), "Assembly model",
               props={"description": "AGITATOR ASSEMBLY"})
    asm_item = asm["item_id"]
    db.execute("UPDATE items SET item_type = 'assembly' WHERE id = ?", (asm_item,))
    with db.tx():
        boms.ensure_item("STD-BRG-22220", "SPHERICAL ROLLER BEARING 22220 EK", asha["id"], "Nos", "", "buy")
        boms.ensure_item("STD-BLT-M16X60", "HEX BOLT M16 X 60 GR 8.8", asha["id"], "Nos", "", "buy")
        boms.ensure_item("STD-WSH-M16", "WASHER M16", asha["id"], "Nos", "", "buy")
        boms.ensure_item("MX-100-040", "LOCK NUT KM22", asha["id"], "Nos", "", "buy")
        boms.ensure_item("MX-100-050", "SHAFT KEY 28X16X100", asha["id"], "Nos", "C45", "make")
    bom_id = boms.create(asha["id"], asm_item, "A", "solidworks", "Generated from MX-100-000 Agitator Assembly")
    boms.set_lines(asha["id"], bom_id, [
        {"part_number": "MX-100-010", "qty": 1}, {"part_number": "MX-100-020", "qty": 1},
        {"part_number": "MX-100-030", "qty": 1}, {"part_number": "MX-100-031", "qty": 1},
        {"part_number": "MX-100-050", "qty": 1}, {"part_number": "STD-BRG-22220", "qty": 2},
        {"part_number": "MX-100-040", "qty": 2}, {"part_number": "STD-BLT-M16X60", "qty": 8},
        {"part_number": "STD-WSH-M16", "qty": 8},
    ])
    db.execute("UPDATE items SET state = 'released' WHERE part_number LIKE 'STD-%' OR part_number IN ('MX-100-040')")
    db.execute("UPDATE items SET material = '1.4401 (316)' WHERE part_number = 'MX-100-010'")
    db.execute("UPDATE items SET material = '42CrMo4', weight_kg = 186 WHERE part_number = 'MX-100-020'")

    # in-work file checked out to Asha
    sleeve = _put(asha, mx, "MX-100-021 Shaft Sleeve.dxf",
                  W.shaft((40, 60, 40), (110, 130, 110), keyway=False, values=_tb("MX-100-021", "SHAFT SLEEVE", "1.4006 (410)", "A")),
                  "First draft", keep=True)
    del sleeve
    # waiting for review
    key = _put(asha, mx, "MX-100-050 Shaft Key.dxf",
               W.shaft((100,), (28,), keyway=False, values=_tb("MX-100-050", "SHAFT KEY 28X16X100", "C45", "A")), "")
    lifecycle.set_state(asha, key["id"], "in_review", "Please check key length")

    # change orders
    co1 = changes.create(asha, "Increase agitator shaft hub seat to Ø115", "Field report: fretting at impeller hub seat",
                         "Increase the hub seat from Ø110 to Ø115 h6 and update the impeller disc bore to suit.",
                         "high", [shaft_doc["id"], disc_doc["id"]], [daniel["id"]])
    changes.revise_items(asha, co1)
    co2 = changes.create(asha, "Assembly model first release", "New product variant", "", "normal",
                         [asm["id"]], [daniel["id"], sofia["id"]])
    changes.submit(asha, co2)
    _seed_quality(asha, daniel, sofia, shaft_doc, de)

    # legacy leftovers: a byte-identical copy and look-alike bought-out parts from an old part master
    inlet = db.one("SELECT v.blob_sha FROM documents d JOIN versions v ON v.document_id = d.id WHERE d.part_number = 'CP-80-014'")
    if inlet:
        from . import store
        _put(sofia, leg2, "CP-80-014.dxf", store.read_bytes(inlet["blob_sha"]), "Copy found on an old file share")
    with db.tx():
        boms.ensure_item("P-1001", "BLT HEX M16 X 60 8.8", sofia["id"], "Nos", "", "buy")
        boms.ensure_item("P-2001", "BRG 22220EK SPHERICAL ROLLER", sofia["id"], "Nos", "", "buy")
        boms.ensure_item("P-3001", "GLAND PACKING 12 SQ", sofia["id"], "Kg", "", "buy")
        boms.ensure_item("P-3002", "PACKING GLAND 12 SQ", sofia["id"], "Kg", "", "buy")
    db.execute("UPDATE items SET state = 'released' WHERE part_number LIKE 'P-%'")

    # duplicate analysis
    run = similarity.start_run(sofia["id"], None, 0.88)
    for _ in range(60):
        if db.scalar("SELECT status FROM cp_runs WHERE id = ?", (run,)) != "running":
            break
        time.sleep(0.25)
    erp.process_pending()
    from .packs import install_for_demo   # categories, spec codes and the standard-parts library
    install_for_demo(["general-machinery", "standard-parts"])
    from .inputs import seed_demo as seed_inputs   # projects and reference material (inputs library)
    seed_inputs()
    log.info("Demo data loaded in %.1fs", time.time() - t0)
