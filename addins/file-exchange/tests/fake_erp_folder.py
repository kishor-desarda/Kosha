# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""A simulated ERP on the other side of the exchange folder, used by the connector and Kosha tests.

`FakeFolderErp.run()` does what an ERP's import job would do: it reads every transfer in `to-erp` in sequence
order (JSON when there is one, else CSV, so both formats are proven importable), skips a sequence number it has
already imported, moves the files to `to-erp/imported`, and writes back, under `from-erp/`, an acknowledgement
file, a stock file and item and BOM exports in the layout the README describes.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path


class FakeFolderErp:
    def __init__(self, folder: str | Path, delimiter: str = ","):
        self.folder = Path(folder)
        self.delimiter = delimiter
        self.items: dict[str, dict] = {}        # CODE -> {code, description, uom, revision, material, active, from_kosha}
        self.boms: dict[str, dict] = {}         # CODE -> {revision, reference, lines: [(code, qty, uom)]}
        self.stock: dict[str, list[tuple[str, float]]] = {}
        self.cost: dict[str, float] = {}
        self.acks: list[tuple[str, str, str]] = []
        self.seen: set[int] = set()           # sequence numbers imported: each one once
        self.reject_units: set[str] = set()
        self.imported: list[str] = []

    # ------------------------------------------------------------ import
    def _read(self, base: str, out: Path) -> dict:
        js = out / f"{base}.json"
        if js.exists():
            return json.loads(js.read_text(encoding="utf-8"))
        rows = list(csv.DictReader(io.StringIO((out / f"{base}.csv").read_text(encoding="utf-8-sig")),
                                   delimiter=self.delimiter))
        first = rows[0]
        if first["record"] == "item":
            item = {k: first[k] for k in ("code", "description", "revision", "uom", "material", "item_group",
                                          "make_or_buy")}
            item["weight_kg"] = float(first["weight_kg"]) if first["weight_kg"] else None
            return {"kind": "item", "sequence": int(first["sequence"]), "item": item}
        return {"kind": "bom", "sequence": int(first["sequence"]),
                "bom": {"code": first["parent_code"], "description": first["parent_description"],
                        "revision": first["bom_revision"],
                        "lines": [{"line_no": int(r["line_no"]), "code": r["component_code"], "qty": float(r["qty"]),
                                   "uom": r["uom"]} for r in rows]}}

    def run(self) -> list[str]:
        out = self.folder / "to-erp"
        done = out / "imported"
        done.mkdir(parents=True, exist_ok=True)
        bases = sorted({p.name.rsplit(".", 1)[0] for p in out.iterdir()
                        if p.is_file() and not p.name.startswith(".") and p.suffix in (".csv", ".json")})
        now = []
        for base in bases:
            doc = self._read(base, out)
            status, message = "ok", "imported"
            if doc["sequence"] in self.seen:
                message = "already imported"
            elif doc["kind"] == "item":
                it = doc["item"]
                if it["uom"] in self.reject_units:
                    status, message = "rejected", f"Unit {it['uom']} does not exist"
                else:
                    prev = self.items.get(it["code"].upper(), {})
                    self.items[it["code"].upper()] = {
                        "code": it["code"], "description": it["description"], "uom": prev.get("uom") or it["uom"],
                        "revision": it["revision"], "material": it["material"] or prev.get("material", ""),
                        "active": prev.get("active", "yes"), "from_kosha": "yes"}
            else:
                b = doc["bom"]
                missing = [ln["code"] for ln in b["lines"] if ln["code"].upper() not in self.items]
                if b["code"].upper() not in self.items or missing:
                    status, message = "rejected", f"Unknown parts: {', '.join(missing or [b['code']])}"
                else:
                    self.boms[b["code"].upper()] = {"code": b["code"], "revision": b["revision"],
                                                    "reference": f"BOM-{b['code']}-{b['revision']}",
                                                    "lines": [(ln["code"], ln["qty"], ln["uom"]) for ln in b["lines"]]}
            if status == "ok":
                self.seen.add(doc["sequence"])
            self.acks.append((base + ".json" if (out / f"{base}.json").exists() else base + ".csv", status, message))
            for ext in (".csv", ".json"):
                if (out / f"{base}{ext}").exists():
                    os.replace(out / f"{base}{ext}", done / f"{base}{ext}")
            now.append(base)
        self.imported += now
        self.write_back()
        return now

    # ------------------------------------------------------------ files written back
    def _write(self, name: str, columns: list[str], rows: list[list]) -> None:
        back = self.folder / "from-erp"
        back.mkdir(exist_ok=True)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=self.delimiter, lineterminator="\n")
        w.writerow(columns)
        w.writerows(rows)
        tmp = back / f".{name}.tmp"
        tmp.write_text(buf.getvalue(), encoding="utf-8")
        os.replace(tmp, back / name)

    def write_back(self) -> None:
        self._write("ack.csv", ["file", "status", "message"], [list(a) for a in self.acks])
        self._write("items.csv", ["code", "description", "uom", "revision", "material", "active", "from_kosha"],
                    [[i[k] for k in ("code", "description", "uom", "revision", "material", "active", "from_kosha")]
                     for i in self.items.values()])
        self._write("boms.csv", ["parent_code", "component_code", "qty", "uom", "revision", "reference"],
                    [[b["code"], c, q, u, b["revision"], b["reference"]] for b in self.boms.values() for c, q, u in b["lines"]])
        rows = []
        for code, item in self.items.items():
            per_wh = self.stock.get(code) or [("", 0.0)]
            for wh, qty in per_wh:
                rows.append([item["code"], qty, self.cost.get(code, ""), wh])
        self._write("stock.csv", ["code", "stock", "cost", "warehouse"], rows)

    def add_stock(self, code: str, qty: float, warehouse: str = "Main store", cost: float | None = None) -> None:
        self.stock.setdefault(code.upper(), []).append((warehouse, qty))
        if cost is not None:
            self.cost[code.upper()] = cost
        self.write_back()


SETTINGS = {"stock_file": "from-erp/stock.csv", "ack_file": "from-erp/ack.csv",
            "items_export_file": "from-erp/items.csv", "boms_export_file": "from-erp/boms.csv"}
