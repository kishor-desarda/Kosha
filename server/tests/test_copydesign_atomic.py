# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy design is all-or-nothing: a failure half-way leaves no item, file, BOM, number or job behind,
and running the copy again afterwards makes it exactly once."""
import io

import pytest

from kosha import boms, config, copydesign, db, lifecycle, store
from kosha.cad import dxfwriter as W

TABLES = ("items", "documents", "versions", "boms", "bom_lines", "lifecycle_events", "copy_jobs", "copy_links", "numbering")


@pytest.fixture
def kosha_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BLOB_DIR", tmp_path / "blobs")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "kosha.db")
    db.close_thread_connection()
    db.init()
    uid = db.insert("users", {"username": "asha", "full_name": "Asha Menon", "role": "engineer", "password_hash": "x",
                              "created_at": db.now()})
    user = {"id": uid, "role": "engineer", "full_name": "Asha Menon"}
    folder = lifecycle.ensure_folder_path("Kosha/Products/Pumps", uid)
    for pn, desc in (("CP-80-010", "IMPELLER"), ("CP-80-020", "PUMP SHAFT")):
        data = W.shaft((40, 60), (30, 40), keyway=False, values={"PART_NO": pn, "DESCRIPTION": desc, "MATERIAL": "C45"})
        lifecycle.checkin(user, folder, f"{pn} {desc.title()}.dxf", io.BytesIO(data))
    lifecycle.checkin(user, folder, "CP-80-000 Pump Assembly.dxf",
                      io.BytesIO(W.flange(200, 160, 8, 18, 100, values={"PART_NO": "CP-80-000", "DESCRIPTION": "PUMP"})))
    asm = db.scalar("SELECT id FROM items WHERE part_number = 'CP-80-000'")
    with db.tx():
        boms.ensure_item("STD-BLT-M12X40", "HEX BOLT M12 X 40", uid, "Nos", "", "buy")
    bom = boms.create(uid, asm, "A")
    boms.set_lines(uid, bom, [{"part_number": "CP-80-010", "qty": 1}, {"part_number": "CP-80-020", "qty": 1},
                              {"part_number": "STD-BLT-M12X40", "qty": 6}])
    yield {"user": user, "folder": folder, "asm": asm}
    db.close_thread_connection()


def snapshot():
    return {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in TABLES}


def request(ctx, key="atomic-copy-1"):
    return {"item_id": ctx["asm"], "folder_id": ctx["folder"], "prefix": "CP-90-", "key": key}


def test_a_failure_half_way_leaves_nothing_behind(kosha_db, monkeypatch):
    before = snapshot()
    blobs_before = sorted(p.name for p in config.BLOB_DIR.rglob("*") if p.is_file())

    def boom(*a, **kw):
        raise RuntimeError("disk unplugged")
    with monkeypatch.context() as m:
        m.setattr(copydesign, "_copy_bom", boom)   # items, files and numbers are already written by then
        with pytest.raises(RuntimeError):
            copydesign.execute(kosha_db["user"], request(kosha_db))
    assert snapshot() == before
    assert copydesign.job("atomic-copy-1") is None

    res = copydesign.execute(kosha_db["user"], request(kosha_db))      # the same key after the crash: made once
    assert [c["item"]["part_number"] for c in res["created"]] == ["CP-90-001", "CP-90-002", "CP-90-003"]
    after = snapshot()
    assert after["items"] == before["items"] + 3 and after["documents"] == before["documents"] + 3
    assert after["boms"] == before["boms"] + 1 and after["copy_jobs"] == 1
    again = copydesign.execute(kosha_db["user"], request(kosha_db))
    assert again["replayed"] and snapshot() == after
    # no file bytes were duplicated: the copies point at the same stored content
    assert sorted(p.name for p in config.BLOB_DIR.rglob("*") if p.is_file()) == blobs_before
    shas = {r["blob_sha"] for r in db.rows("SELECT blob_sha FROM versions")}
    assert all(store.exists(s) for s in shas) and len(shas) == 3


def test_numbers_typed_in_are_never_handed_out_twice(kosha_db):
    ids = {r["part_number"]: r["id"] for r in db.rows("SELECT id, part_number FROM items")}
    body = {**request(kosha_db, "atomic-copy-2"), "rows": [{"item_id": ids["CP-80-020"], "new_part_number": "CP-90-001"}]}
    p = copydesign.plan(kosha_db["user"], body)
    assert [r["new_part_number"] for r in p["rows"] if r["action"] == "copy"] == ["CP-90-002", "CP-90-003", "CP-90-001"]
    res = copydesign.execute(kosha_db["user"], body)
    assert sorted(c["item"]["part_number"] for c in res["created"]) == ["CP-90-001", "CP-90-002", "CP-90-003"]
    assert copydesign.peek_numbers("CP-90-", 3, 1, set()) == ["CP-90-004"]
