# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""File exchange connector with temporary folders, a simulated ERP on the other side, and real crashes."""
import csv
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(HERE.parent), str(ROOT / "sdk" / "python")]

from fake_erp_folder import SETTINGS, FakeFolderErp  # noqa: E402
from kosha_file_exchange.connector import (SCHEMA, FileExchangeConnector, clean_stale_temps, number,  # noqa: E402
                                           read_table)
from kosha_sdk import AddinContext  # noqa: E402
from kosha_sdk.erp import ErpBom, ErpBomLine, ErpError, ErpItem, ErpNotSupported  # noqa: E402
from kosha_sdk.testing import check_erp_connector, check_manifest  # noqa: E402

PUMP = ErpItem(code="DE-400-001", description="PUMP CASING", revision="A", uom="Nos", material="EN-GJL-250",
               weight_kg=4.5, item_group="Pumps", make_or_buy="make")


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "exchange"
    f.mkdir()
    return f


@pytest.fixture
def workdir(tmp_path):
    w = tmp_path / "work"
    w.mkdir()
    return w


def connector(folder, workdir, **overrides):
    settings = {"folder": str(folder), "formats": "CSV and JSON", "csv_delimiter": "Comma (,)", **overrides}
    return FileExchangeConnector(AddinContext("org.kosha.file-exchange", settings=settings, workdir=workdir,
                                              log=logging.getLogger("test")))


def transfers(folder) -> list[str]:
    return sorted(p.name for p in (folder / "to-erp").iterdir() if p.is_file())


class FastErp(FileExchangeConnector):
    """The connector with an ERP that imports at once, so the read-back checks can run straight after a send."""
    erp: FakeFolderErp

    def _transfer(self, *a, **kw):
        r = super()._transfer(*a, **kw)
        self.erp.run()
        return r


def test_manifest_is_valid():
    assert check_manifest(HERE.parent) == []


def test_conformance_without_erp_files(folder, workdir):
    (folder / "stock.csv").write_text("code,stock,cost\n", encoding="utf-8")
    assert check_erp_connector(connector(folder, workdir, stock_file="stock.csv")) == []


def test_conformance_with_an_erp_that_writes_back(folder, workdir):
    c = FastErp(AddinContext("org.kosha.file-exchange", workdir=workdir,
                             settings={"folder": str(folder), "formats": "CSV and JSON", **SETTINGS}))
    c.erp = FakeFolderErp(folder)
    c.erp.write_back()
    assert check_erp_connector(c) == []
    assert len(c.erp.imported) == 4                     # three items and one BOM, each imported once


def test_item_files_follow_the_schema(folder, workdir):
    c = connector(folder, workdir)
    r = c.upsert_item(PUMP)
    assert r.action == "created" and r.external_id.startswith("000001_item_DE-400-001_")
    assert transfers(folder) == [f"{r.external_id}.csv", f"{r.external_id}.json"]
    doc = json.loads((folder / "to-erp" / f"{r.external_id}.json").read_text(encoding="utf-8"))
    assert doc["schema"] == SCHEMA == "kosha.erp-exchange/1" and doc["kind"] == "item" and doc["sequence"] == 1
    assert doc["item"] == {"code": "DE-400-001", "description": "PUMP CASING", "revision": "A", "uom": "Nos",
                           "material": "EN-GJL-250", "weight_kg": 4.5, "item_group": "Pumps", "make_or_buy": "make"}
    rows = list(csv.DictReader(io.StringIO((folder / "to-erp" / f"{r.external_id}.csv").read_text(encoding="utf-8"))))
    assert list(rows[0]) == ["record", "code", "description", "revision", "uom", "material", "weight_kg", "item_group",
                             "make_or_buy", "content_hash", "sequence", "written_at"]
    assert rows[0]["record"] == "item" and rows[0]["weight_kg"] == "4.5" and rows[0]["content_hash"] == doc["content_hash"]
    assert rows[0]["written_at"] == doc["written_at"]


def test_same_content_is_not_written_twice(folder, workdir):
    c = connector(folder, workdir)
    first = c.upsert_item(PUMP)
    assert c.upsert_item(PUMP).action == "unchanged"
    erp = FakeFolderErp(folder)
    erp.run()                                             # the ERP takes the files away
    assert transfers(folder) == []
    again = connector(folder, workdir).upsert_item(PUMP)  # a new instance (Kosha restarted) remembers too
    assert again.action == "unchanged" and first.external_id in again.message
    assert transfers(folder) == []
    changed = c.upsert_item(ErpItem(**{**PUMP.__dict__, "revision": "B"}))
    assert changed.action == "updated" and changed.external_id.startswith("000002_item_")
    back = c.upsert_item(PUMP)                            # back to rev A is a change again
    assert back.action == "updated" and back.external_id.startswith("000003_item_")


def test_bom_files_list_the_lines(folder, workdir):
    c = connector(folder, workdir, formats="CSV only", csv_delimiter="Semicolon (;)")
    bom = ErpBom(ErpItem("DE-400-000", "PUMP ASSY; COMPLETE"), "B",
                 (ErpBomLine("DE-400-001", 1, "Nos"), ErpBomLine("DE-400-002", 2.5, "Kg")))
    r = c.upsert_bom(bom)
    assert r.action == "created" and transfers(folder) == [f"{r.external_id}.csv"]
    text = (folder / "to-erp" / f"{r.external_id}.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    assert [(x["record"], x["parent_code"], x["parent_description"], x["bom_revision"], x["line_no"],
             x["component_code"], x["qty"], x["uom"]) for x in rows] == [
        ("bom_line", "DE-400-000", "PUMP ASSY; COMPLETE", "B", "1", "DE-400-001", "1", "Nos"),
        ("bom_line", "DE-400-000", "PUMP ASSY; COMPLETE", "B", "2", "DE-400-002", "2.5", "Kg")]
    assert c.upsert_bom(bom).action == "unchanged"
    j = connector(folder, workdir, formats="JSON only").upsert_bom(
        ErpBom(bom.item, "C", (ErpBomLine("DE-400-001", 3, "Nos"),)))
    doc = json.loads((folder / "to-erp" / f"{j.external_id}.json").read_text(encoding="utf-8"))
    assert doc["kind"] == "bom" and doc["bom"]["revision"] == "C"
    assert doc["bom"]["lines"] == [{"line_no": 1, "code": "DE-400-001", "qty": 3.0, "uom": "Nos"}]


def test_odd_part_numbers_make_safe_file_names(folder, workdir):
    r = connector(folder, workdir).upsert_item(ErpItem(code="DE/400 001:A", description="ODD"))
    assert "_item_DE_400_001_A_" in r.external_id
    assert json.loads((folder / "to-erp" / f"{r.external_id}.json").read_text())["item"]["code"] == "DE/400 001:A"


def test_parallel_sends_write_one_transfer(folder, workdir):
    errors = []

    def send():
        try:
            connector(folder, workdir).upsert_item(PUMP)
        except Exception as e:  # pragma: no cover - the assertion below shows it
            errors.append(e)
    threads = [threading.Thread(target=send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(transfers(folder)) == 2


CRASH = """
import os, sys
sys.path[:0] = sys.argv[1:4]
from kosha_file_exchange import connector as C
from kosha_sdk import AddinContext
from kosha_sdk.erp import ErpItem
where, folder, workdir = sys.argv[4], sys.argv[5], sys.argv[6]
if where == "before_rename":
    os.replace = lambda *a, **k: os._exit(3)          # the power fails after the temporary file is on disk
else:
    C.FileExchangeConnector._save_ledger = lambda *a, **k: os._exit(3)   # ... after the rename, before the record
c = C.FileExchangeConnector(AddinContext("org.kosha.file-exchange", settings={"folder": folder}, workdir=workdir))
c.upsert_item(ErpItem(code="DE-400-001", description="PUMP CASING", revision="A", uom="Nos"))
"""


def crash(where, folder, workdir):
    r = subprocess.run([sys.executable, "-c", CRASH, str(HERE), str(HERE.parent), str(ROOT / "sdk" / "python"),
                        where, str(folder), str(workdir)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 3, r.stderr


def test_crash_between_temporary_write_and_rename(folder, workdir):
    crash("before_rename", folder, workdir)
    out = folder / "to-erp"
    left = [p for p in out.iterdir()]
    assert len(left) == 1 and left[0].name.startswith(".kosha-") and left[0].name.endswith(".tmp")
    assert left[0].stat().st_size > 0                     # the data was on disk, but never under its real name
    assert not list(workdir.iterdir())                    # nothing was recorded as sent
    erp = FakeFolderErp(folder)
    assert erp.run() == []                                # the ERP ignores the temporary file
    old = time.time() - 3600
    os.utime(left[0], (old, old))
    item = ErpItem(code="DE-400-001", description="PUMP CASING", revision="A", uom="Nos")
    r = connector(folder, workdir).upsert_item(item)
    assert r.action == "created"
    assert transfers(folder) == [f"{r.external_id}.csv", f"{r.external_id}.json"]   # the stale temporary file is gone
    assert erp.run() == [r.external_id]
    assert connector(folder, workdir).upsert_item(item).action == "unchanged"


def test_crash_after_rename_before_recording(folder, workdir):
    crash("after_rename", folder, workdir)
    written = transfers(folder)
    assert len(written) == 2 and written[0].startswith("000001_item_DE-400-001_")
    stamps = {n: (folder / "to-erp" / n).stat().st_mtime_ns for n in written}
    r = connector(folder, workdir).upsert_item(ErpItem(code="DE-400-001", description="PUMP CASING", revision="A",
                                                       uom="Nos"))
    assert r.action == "created" and transfers(folder) == written      # finished, not written a second time
    assert {n: (folder / "to-erp" / n).stat().st_mtime_ns for n in written} == stamps
    assert connector(folder, workdir).upsert_item(ErpItem(code="DE-400-001", description="PUMP CASING", revision="A",
                                                          uom="Nos")).action == "unchanged"


def test_only_old_temporary_files_are_cleaned(tmp_path):
    fresh, old, other = tmp_path / ".kosha-a.csv.1.tmp", tmp_path / ".kosha-b.csv.2.tmp", tmp_path / "erp.tmp"
    for p in (fresh, old, other):
        p.write_text("x")
    os.utime(old, (time.time() - 3600,) * 2)
    os.utime(other, (time.time() - 3600,) * 2)
    assert clean_stale_temps(tmp_path) == 1
    assert fresh.exists() and not old.exists() and other.exists()      # another writer's file and the ERP's are kept


def test_rejected_transfer_is_written_again(folder, workdir):
    c = connector(folder, workdir, ack_file="from-erp/ack.csv")
    erp = FakeFolderErp(folder)
    erp.reject_units = {"Nos"}
    first = c.upsert_item(PUMP)
    erp.run()
    assert "rejected 1" in c.health().message and "Unit Nos does not exist" in c.health().message
    erp.reject_units = set()                              # the unit was added in the ERP
    again = c.upsert_item(PUMP)
    assert again.action == "updated" and "the ERP rejected" in again.message and "Unit Nos does not exist" in again.message
    assert again.external_id != first.external_id
    erp.run()
    assert "DE-400-001" in erp.items and c.upsert_item(PUMP).action == "unchanged"
    assert "The ERP rejected" not in c.health().message


def test_stock_and_cost(folder, workdir):
    with pytest.raises(ErpNotSupported, match="stock file"):
        connector(folder, workdir).stock_and_cost("DE-400-001")
    c = connector(folder, workdir, stock_file="from-erp/stock.csv")
    with pytest.raises(ErpError, match="has not written the stock file"):
        c.stock_and_cost("DE-400-001")
    (folder / "from-erp").mkdir()
    (folder / "from-erp" / "stock.csv").write_text(
        "﻿Code;Stock;Cost;Warehouse\nDE-400-001;6;12,50;Main store\nde-400-001;4,5;12,75;Assembly\n"
        "DE-400-009;1;;\n", encoding="utf-8")
    s = c.stock_and_cost("DE-400-001")
    assert s.exists and s.stock == 10.5 and s.cost == 12.75
    assert s.warehouses == ({"warehouse": "Main store", "actual_qty": 6.0}, {"warehouse": "Assembly", "actual_qty": 4.5})
    assert c.stock_and_cost("DE-400-009").cost is None
    assert not c.stock_and_cost("DE-400-002").exists
    c.upsert_item(ErpItem(code="DE-400-002", description="IMPELLER"))
    s = c.stock_and_cost("DE-400-002")                   # sent, but the ERP has not reported stock yet
    assert s.exists and s.stock == 0 and s.cost is None


def test_reading_back_for_reconciliation(folder, workdir):
    c = connector(folder, workdir, **SETTINGS)
    with pytest.raises(ErpError, match="has not written the item export"):
        c.fetch_items(["DE-400-001"])
    erp = FakeFolderErp(folder)
    for code, desc in (("DE-400-001", "PUMP CASING"), ("DE-400-002", "IMPELLER"), ("DE-400-000", "PUMP ASSY")):
        c.upsert_item(ErpItem(code=code, description=desc, revision="A", uom="Nos"))
    c.upsert_bom(ErpBom(ErpItem("DE-400-000", "PUMP ASSY"), "A", (ErpBomLine("DE-400-001", 1, "Nos"),
                                                                  ErpBomLine("DE-400-002", 2, "Nos"))))
    erp.run()
    erp.items["DE-400-002"]["active"] = "no"
    erp.items["DE-900"] = {"code": "DE-900", "description": "MADE IN THE ERP", "uom": "Kg", "revision": "",
                           "material": "", "active": "yes", "from_kosha": "no"}
    erp.write_back()
    got = c.fetch_items(["de-400-001", "DE-400-002", "DE-900", "DE-NONE"])
    assert set(got) == {"DE-400-001", "DE-400-002", "DE-900"}
    r = got["DE-400-001"]
    assert (r.description, r.uom, r.revision, r.material, r.active, r.from_kosha) == \
        ("PUMP CASING", "Nos", "A", "", True, True)
    assert got["DE-400-002"].active is False and got["DE-900"].from_kosha is False
    assert sorted(c.list_items_from_kosha()) == ["DE-400-000", "DE-400-001", "DE-400-002"]
    rb = c.fetch_bom("DE-400-000")
    assert rb.revision == "A" and rb.reference == "BOM-DE-400-000-A"
    assert [(ln.code, ln.qty) for ln in rb.lines] == [("DE-400-001", 1.0), ("DE-400-002", 2.0)]
    assert c.fetch_bom("DE-400-001") is None


def test_send_again_writes_again_when_the_erp_export_disagrees(folder, workdir):
    c = connector(folder, workdir, **SETTINGS)
    erp = FakeFolderErp(folder)
    item = ErpItem(code="DE-400-001", description="PUMP CASING", revision="A", uom="Nos")
    child = ErpItem(code="DE-400-002", description="IMPELLER", revision="A", uom="Nos")
    bom = ErpBom(ErpItem("DE-400-000", "PUMP ASSY"), "A", (ErpBomLine("DE-400-001", 1),))
    for it in (item, child, ErpItem(code="DE-400-000", description="PUMP ASSY")):
        c.upsert_item(it)
    c.upsert_bom(bom)
    assert c.upsert_item(item).action == "unchanged"         # the ERP has not exported anything yet: nothing to tell
    erp.run()
    assert c.upsert_item(item).action == "unchanged" and c.upsert_bom(bom).action == "unchanged"   # the ERP agrees
    time.sleep(0.01)
    erp.items["DE-400-001"]["description"] = "CHANGED IN THE ERP"
    erp.boms["DE-400-000"]["lines"].append(("DE-400-002", 5, "Nos"))
    erp.write_back()
    r = c.upsert_item(item)
    assert r.action == "updated" and "item export shows another description" in r.message
    assert c.upsert_item(item).action == "unchanged"         # written again once, not on every send
    b = c.upsert_bom(bom)
    assert b.action == "updated" and "BOM export shows other lines" in b.message
    erp.run()
    assert erp.items["DE-400-001"]["description"] == "PUMP CASING" and len(erp.boms["DE-400-000"]["lines"]) == 1
    assert c.upsert_item(item).action == "unchanged" and c.upsert_bom(bom).action == "unchanged"


def test_export_columns_the_erp_does_not_have_are_not_compared(folder, workdir):
    (folder / "items.csv").write_text("code\tdescription\nDE-1\tROLLER\n", encoding="utf-8")
    c = connector(folder, workdir, items_export_file=str(folder / "items.csv"))      # a full path works too
    r = c.fetch_items(["DE-1"])["DE-1"]
    assert (r.description, r.uom, r.revision, r.material, r.active, r.from_kosha) == ("ROLLER", None, None, None, None, None)
    with pytest.raises(ErpNotSupported, match="no from_kosha column"):
        c.list_items_from_kosha()
    with pytest.raises(ErpNotSupported, match="BOM export"):
        c.fetch_bom("DE-1")
    with pytest.raises(ErpNotSupported, match="item export"):
        connector(folder, workdir).fetch_items(["DE-1"])


def test_folder_problems_are_plain_messages(folder, workdir, tmp_path):
    with pytest.raises(ErpError, match="Fill in the exchange folder"):
        connector("", workdir).upsert_item(PUMP)
    with pytest.raises(ErpError, match="must be a full path"):
        connector("exchange", workdir).upsert_item(PUMP)
    missing = connector(tmp_path / "not-mounted", workdir)
    with pytest.raises(ErpError, match="does not exist .* Nothing was written"):
        missing.upsert_item(PUMP)
    assert not missing.health().ok and "does not exist" in missing.health().message
    (folder / "to-erp").write_text("in the way")          # to-erp is a file, so nothing can be written there
    with pytest.raises(ErpError, match="cannot create the folder"):
        connector(folder, workdir).upsert_item(PUMP)


def test_unreadable_record_stops_before_writing(folder, workdir):
    c = connector(folder, workdir)
    c.upsert_item(PUMP)
    ledger = next(workdir.iterdir())
    ledger.write_text("{broken", encoding="utf-8")
    with pytest.raises(ErpError, match="cannot be read"):
        c.upsert_item(ErpItem(**{**PUMP.__dict__, "revision": "B"}))
    assert len(transfers(folder)) == 2


def test_health_says_where_and_what(folder, workdir):
    c = connector(folder, workdir, stock_file="from-erp/stock.csv", items_export_file="from-erp/items.csv")
    c.upsert_item(PUMP)
    h = c.health()
    assert h.ok and str(folder / "to-erp") in h.message and "Items and BOMs written so far: 1." in h.message
    assert "Transfers waiting in the folder: 1." in h.message and "is not there yet" in h.message
    FakeFolderErp(folder).run()
    h = c.health()
    assert "Transfers waiting in the folder: 0." in h.message and "Stock file stock.csv: updated just now" in h.message
    assert not [p for p in (folder / "to-erp").iterdir() if p.name.startswith(".")]    # the probe file is removed


def test_numbers_and_tables():
    assert (number("12"), number("12.5"), number("12,5"), number(""), number("n/a"), number("1 200")) == \
        (12.0, 12.5, 12.5, None, None, 1200.0)


def test_read_table_skips_empty_rows(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("Code|Stock\n\nA|1\n|\n", encoding="utf-8")
    assert read_table(p) == [{"code": "A", "stock": "1"}]
