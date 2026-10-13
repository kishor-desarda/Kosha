# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""File exchange connector: for any ERP without a supported API.

    Item  -> to-erp/<seq>_item_<code>_<hash>.csv and .json, one file pair per transfer
    BOM   -> to-erp/<seq>_bom_<code>_<hash>.csv and .json (the BOM lines, parent first in the JSON)
    Stock <- a CSV the ERP writes (code, stock, cost, warehouse), when configured
    Acknowledgements <- a CSV the ERP writes (file, status, message): a rejected transfer is written again
    Reconciliation <- the ERP's item and BOM export files (CSV), when configured, read only

Every file is written atomically: to a hidden temporary file in the same folder, flushed to disk (fsync), then
renamed, so the ERP never sees half a file. Writing is idempotent: Kosha remembers (in its own work folder) what
it last wrote for each item and BOM, and the same content is not written twice. File names are fixed by their
content, so a transfer interrupted between the rename and the bookkeeping is finished, not written again.
The schema of the files is described in the add-in's README and does not change within schema version 1.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from kosha_sdk import AddinContext, Health
from kosha_sdk.erp import (ErpBom, ErpBomLine, ErpBomRecord, ErpConnector, ErpError, ErpItem, ErpItemRecord,
                           ErpNotSupported, ErpResult, StockInfo)

SCHEMA = "kosha.erp-exchange/1"
OUT_DIR = "to-erp"
TMP_PREFIX = ".kosha-"
TMP_SUFFIX = ".tmp"
STALE_TMP_SECONDS = 60
ITEM_COLUMNS = ["record", "code", "description", "revision", "uom", "material", "weight_kg", "item_group",
                "make_or_buy", "content_hash", "sequence", "written_at"]
BOM_COLUMNS = ["record", "parent_code", "parent_description", "bom_revision", "line_no", "component_code", "qty", "uom",
               "content_hash", "sequence", "written_at"]
DELIMITERS = {"Comma (,)": ",", "Semicolon (;)": ";", "Tab": "\t"}
FORMATS = {"CSV and JSON": ("csv", "json"), "CSV only": ("csv",), "JSON only": ("json",)}
_TRUE = {"1", "yes", "y", "true", "active", "enabled", "x"}
_FALSE = {"0", "no", "n", "false", "inactive", "disabled", "archived"}
_OK = {"ok", "imported", "done", "accepted", "success"}
_REJECTED = {"error", "rejected", "failed", "refused"}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(folder: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(folder), threading.Lock())


def safe_name(code: str) -> str:
    """A part number as a file-name fragment: letters, digits, dot, dash and underscore only."""
    return re.sub(r"[^0-9A-Za-z._-]+", "_", code).strip("._")[:60] or "item"


def content_hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def qty_text(q: float) -> str:
    q = round(float(q), 6)
    return str(int(q)) if q == int(q) else f"{q:g}"


def fsync_dir(folder: Path) -> None:
    """Make a rename durable (POSIX); a no-op where folders cannot be opened (Windows)."""
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_atomic(path: Path, data: bytes) -> None:
    """Write a file so that readers see the old state or the whole new file, never a part of it."""
    tmp = path.with_name(f"{TMP_PREFIX}{path.name}.{secrets.token_hex(4)}{TMP_SUFFIX}")
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)     # a failed write leaves nothing behind (a crash may: see clean_stale_temps)
        except OSError:
            pass
        raise
    fsync_dir(path.parent)


def clean_stale_temps(folder: Path, older_than: float = STALE_TMP_SECONDS) -> int:
    """Remove temporary files left by an interrupted write. Only Kosha's own, and only old ones."""
    removed = 0
    now = time.time()
    for p in folder.glob(f"{TMP_PREFIX}*{TMP_SUFFIX}"):
        try:
            if now - p.stat().st_mtime > older_than:
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def number(text) -> float | None:
    """A number from a file: 12, 12.5, or 12,5 (decimal comma); None when empty or unreadable."""
    s = str(text or "").strip().replace(" ", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    if s.count(",") == 1 and "." not in s:
        try:
            return float(s.replace(",", "."))
        except ValueError:
            return None
    return None


def flag(text) -> bool | None:
    s = str(text or "").strip().lower()
    return True if s in _TRUE else False if s in _FALSE else None


def read_table(path: Path) -> list[dict[str, str]]:
    """Rows of a CSV file the ERP wrote, as {lower-case column: text}. The separator (, ; tab |) is detected."""
    raw = path.read_bytes().decode("utf-8-sig", errors="replace")
    first = raw.split("\n", 1)[0]
    counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
    delimiter = max(counts, key=counts.get) if any(counts.values()) else ","
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
    rows = []
    for row in reader:
        clean = {str(k or "").strip().lower(): (v or "").strip() if isinstance(v, str) else "" for k, v in row.items()}
        if any(clean.values()):
            rows.append(clean)
    return rows


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(seconds: float) -> str:
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    if seconds < 129600:
        return f"{round(seconds / 3600)} hours ago"
    return f"{round(seconds / 86400)} days ago"


class FileExchangeConnector(ErpConnector):
    def __init__(self, ctx: AddinContext):
        super().__init__(ctx)
        s = ctx.settings
        self.cfg = s
        self._folder_text = str(s.get("folder") or "").strip()
        self._formats = FORMATS.get(s.get("formats") or "", FORMATS["CSV and JSON"])
        self._delimiter = DELIMITERS.get(s.get("csv_delimiter") or "", ",")

    # ------------------------------------------------------------ folders and files
    def _folder(self) -> Path:
        if not self._folder_text:
            raise ErpError("Fill in the exchange folder (ERP settings).")
        folder = Path(self._folder_text).expanduser()
        if not folder.is_absolute():
            raise ErpError(f"The exchange folder must be a full path, such as /srv/erp-exchange or "
                           f"D:\\erp-exchange, not {self._folder_text!r}.")
        if not folder.is_dir():
            raise ErpError(f"The exchange folder {folder} does not exist or cannot be opened. Create it, or connect "
                           "the network share, then try again. Nothing was written.")
        return folder

    def _out(self) -> Path:
        out = self._folder() / OUT_DIR
        try:
            out.mkdir(exist_ok=True)
        except OSError as e:
            raise ErpError(f"Kosha cannot create the folder {out}: {e.strerror or e}. Nothing was written.") from None
        return out

    def _erp_file(self, key: str) -> Path | None:
        name = str(self.cfg.get(key) or "").strip()
        if not name:
            return None
        p = Path(name).expanduser()
        return p if p.is_absolute() else self._folder() / p

    def _ledger_path(self, folder: Path) -> Path:
        tag = hashlib.sha256(str(folder.resolve()).encode("utf-8")).hexdigest()[:12]
        return Path(self.ctx.workdir) / f"file-exchange-{tag}.json"

    def _load_ledger(self, folder: Path) -> dict:
        p = self._ledger_path(folder)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as e:
            raise ErpError(f"Kosha's record of the files it wrote ({p}) cannot be read: {e}. Nothing was written; "
                           "ask the admin to check the file.") from None
        data.setdefault("folder", str(folder))
        data.setdefault("seq", 0)
        data.setdefault("sent", {})
        return data

    def _save_ledger(self, folder: Path, ledger: dict) -> None:
        p = self._ledger_path(folder)
        p.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(p, json.dumps(ledger, indent=1, sort_keys=True).encode("utf-8"))

    def _acks(self) -> dict[str, tuple[str, str]]:
        """{file name without extension: (ok | rejected, message)} from the ERP's acknowledgement file."""
        path = self._erp_file("ack_file")
        if path is None or not path.is_file():
            return {}
        out = {}
        try:
            rows = read_table(path)
        except OSError:
            return {}
        for r in rows:
            name = Path(r.get("file") or r.get("filename") or "").name
            base = name.rsplit(".", 1)[0] if name.endswith((".csv", ".json")) else name
            status = (r.get("status") or "").lower()
            if base:
                out[base] = ("rejected" if status in _REJECTED else "ok" if status in _OK else status,
                             r.get("message") or "")
        return out

    def _erp_differs(self, kind: str, record: dict, since: float) -> str:
        """Why the ERP's own export, written after Kosha's last transfer, shows that the ERP holds something else
        ("" when it agrees, or when there is no newer export to tell). Sending again then writes the file again."""
        key, label = ("items_export_file", "item export") if kind == "item" else ("boms_export_file", "BOM export")
        path = self._erp_file(key)
        try:
            if path is None or not path.is_file() or path.stat().st_mtime <= since:
                return ""
            rows = read_table(path)
        except OSError:
            return ""
        code = record["code"].upper()
        if kind == "item":
            row = next((r for r in rows if (r.get("code") or "").upper() == code), None)
            if row is None:
                return f"the ERP's {label} does not have {record['code']}"
            wanted = {"description": record["description"], "revision": record["revision"]}
            if record["material"]:
                wanted["material"] = record["material"]
            fields = [k for k, v in wanted.items() if k in row and " ".join(row[k].split()) != " ".join(str(v).split())]
            return f"the ERP's {label} shows another {' and '.join(fields)}" if fields else ""
        have = sorted(((r.get("component_code") or "").upper(), round(number(r.get("qty")) or 0.0, 6))
                      for r in rows if (r.get("parent_code") or "").upper() == code)
        if not have:
            return f"the ERP's {label} has no BOM for {record['code']}"
        want = sorted((ln["code"].upper(), round(float(ln["qty"]), 6)) for ln in record["lines"])
        return f"the ERP's {label} shows other lines" if have != want else ""

    def _csv(self, columns: list[str], rows: list[dict]) -> bytes:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=columns, delimiter=self._delimiter, lineterminator="\r\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        return buf.getvalue().encode("utf-8")

    def _transfer(self, kind: str, code: str, record: dict, csv_rows, columns: list[str]) -> ErpResult:
        """Write one transfer (CSV and/or JSON) unless the same content was already written. Idempotent."""
        folder = self._folder()
        out = self._out()
        digest = content_hash(record)
        key = f"{kind}:{code.upper()}"
        with _lock_for(folder):
            ledger = self._load_ledger(folder)
            prev = ledger["sent"].get(key)
            again = ""
            if prev and prev.get("hash") == digest:
                state, msg = self._acks().get(prev.get("file", ""), ("", ""))
                if state == "rejected":
                    again = f"written again: the ERP rejected {prev.get('file')}" + (f" ({msg})" if msg else "")
                else:
                    differs = self._erp_differs(kind, record, float(prev.get("ts") or 0))
                    if not differs:
                        return ErpResult.unchanged(f"unchanged (already written as {prev.get('file')})",
                                                   prev.get("file", ""))
                    again = f"written again: {differs}"
            # The name follows from the content and the next sequence number, so a transfer interrupted after a
            # file was renamed into place (but before it was recorded) is finished here, not written twice.
            seq = ledger["seq"] + 1
            base = f"{seq:06d}_{kind}_{safe_name(code)}_{digest[:10]}"
            written_at = _now()
            doc = {"schema": SCHEMA, "kind": kind, "sequence": seq, "content_hash": digest, "written_at": written_at,
                   kind: record}
            try:
                clean_stale_temps(out)
                for ext in self._formats:
                    target = out / f"{base}.{ext}"
                    if target.exists():
                        continue
                    if ext == "json":
                        data = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
                    else:
                        data = self._csv(columns, [{**r, "content_hash": digest, "sequence": seq,
                                                    "written_at": written_at} for r in csv_rows])
                    write_atomic(target, data)
            except OSError as e:
                raise ErpError(f"Kosha could not write to the exchange folder {out}: {e.strerror or e}. The ERP "
                               "has not received this transfer completely; the job can be retried once the folder "
                               "is reachable.") from None
            ledger["seq"] = max(ledger["seq"], seq)
            ledger["sent"][key] = {"hash": digest, "file": base, "at": written_at, "ts": time.time()}
            try:
                self._save_ledger(folder, ledger)
            except OSError as e:
                raise ErpError(f"Kosha wrote {base} but could not record it in {self._ledger_path(folder)}: "
                               f"{e.strerror or e}. Retry the job: it finishes this transfer without writing it "
                               "twice.") from None
        files = " and ".join(f"{base}.{ext}" for ext in self._formats)
        if again:
            return ErpResult.updated(f"{again}; wrote {files}", doc, base)
        if prev:
            return ErpResult.updated(f"changed; wrote {files}", doc, base)
        return ErpResult.created(f"wrote {files}", doc, base)

    # ------------------------------------------------------------ contract
    def health(self) -> Health:
        try:
            folder = self._folder()
            out = self._out()
            probe = out / f"{TMP_PREFIX}probe.{secrets.token_hex(4)}{TMP_SUFFIX}"
            try:
                with open(probe, "xb") as f:
                    f.write(b"kosha")
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                probe.unlink(missing_ok=True)
        except ErpError as e:
            return Health.down(str(e))
        except OSError as e:
            return Health.down(f"Kosha cannot write to {self._folder_text}/{OUT_DIR}: {e.strerror or e}.")
        parts = [f"Kosha can write to {out}."]
        try:
            ledger = self._load_ledger(folder)
        except ErpError as e:
            return Health.down(str(e))
        waiting = len({p.name.rsplit(".", 1)[0] for p in out.glob("[0-9]*_*.*") if p.suffix in (".csv", ".json")})
        parts.append(f"Items and BOMs written so far: {len(ledger['sent'])}. Transfers waiting in the folder: {waiting}.")
        acks = self._acks()
        rejected = [(e["file"], acks[e["file"]][1]) for e in ledger["sent"].values()
                    if acks.get(e.get("file", ""), ("",))[0] == "rejected"]
        if rejected:
            first, msg = rejected[0]
            parts.append(f"The ERP rejected {len(rejected)} of them, for example {first}" + (f": {msg}." if msg else "."))
        for key, label in (("stock_file", "Stock file"), ("ack_file", "Acknowledgement file"),
                           ("items_export_file", "Item export"), ("boms_export_file", "BOM export")):
            path = self._erp_file(key)
            if path is None:
                continue
            if path.is_file():
                parts.append(f"{label} {path.name}: updated {_ago(time.time() - path.stat().st_mtime)}.")
            else:
                parts.append(f"{label} {path} is not there yet.")
        return Health.up(" ".join(parts))

    @staticmethod
    def _item_record(item: ErpItem) -> dict:
        return {"code": item.code, "description": item.description or item.code, "revision": item.revision,
                "uom": item.uom, "material": item.material, "weight_kg": item.weight_kg,
                "item_group": item.item_group, "make_or_buy": item.make_or_buy}

    def upsert_item(self, item: ErpItem) -> ErpResult:
        record = self._item_record(item)
        row = {"record": "item", **{k: ("" if v is None else v) for k, v in record.items()}}
        return self._transfer("item", item.code, record, [row], ITEM_COLUMNS)

    def upsert_bom(self, bom: ErpBom) -> ErpResult:
        lines = [{"line_no": n, "code": ln.code, "qty": float(ln.qty), "uom": ln.uom}
                 for n, ln in enumerate(bom.lines, start=1)]
        record = {"code": bom.item.code, "description": bom.item.description or bom.item.code,
                  "revision": bom.revision, "lines": lines}
        rows = [{"record": "bom_line", "parent_code": bom.item.code, "parent_description": record["description"],
                 "bom_revision": bom.revision, "line_no": ln["line_no"], "component_code": ln["code"],
                 "qty": qty_text(ln["qty"]), "uom": ln["uom"]} for ln in lines]
        return self._transfer("bom", bom.item.code, record, rows, BOM_COLUMNS)

    def stock_and_cost(self, code: str) -> StockInfo:
        path = self._erp_file("stock_file")
        if path is None:
            raise ErpNotSupported("Set the stock file in the exchange folder settings, so Kosha can read stock and "
                                  "cost from the file the ERP writes.")
        if not path.is_file():
            raise ErpError(f"The ERP has not written the stock file {path} yet. Nothing was changed in Kosha.")
        try:
            rows = [r for r in read_table(path) if (r.get("code") or "").upper() == code.upper()]
        except OSError as e:
            raise ErpError(f"Kosha cannot read the stock file {path}: {e.strerror or e}.") from None
        if not rows:
            known = f"item:{code.upper()}" in self._load_ledger(self._folder())["sent"]
            if not known:
                try:
                    known = bool(self.fetch_items([code]))
                except ErpNotSupported:
                    known = False
            return StockInfo(exists=known)
        warehouses = tuple({"warehouse": r.get("warehouse") or "", "actual_qty": number(r.get("stock")) or 0.0}
                           for r in rows)
        costs = [number(r.get("cost")) for r in rows if number(r.get("cost")) is not None]
        return StockInfo(exists=True, stock=sum(w["actual_qty"] for w in warehouses),
                         cost=costs[-1] if costs else None, warehouses=warehouses)

    # ------------------------------------------------- reading back (reconciliation)
    def _export(self, key: str, label: str) -> list[dict[str, str]]:
        path = self._erp_file(key)
        if path is None:
            raise ErpNotSupported(f"Set the {label} in the exchange folder settings, so Kosha can compare with what "
                                  "the ERP holds.")
        if not path.is_file():
            raise ErpError(f"The ERP has not written the {label} {path} yet.")
        try:
            return read_table(path)
        except OSError as e:
            raise ErpError(f"Kosha cannot read the {label} {path}: {e.strerror or e}.") from None

    def fetch_items(self, codes) -> dict[str, ErpItemRecord]:
        wanted = {str(c).upper() for c in codes}
        out = {}
        for r in self._export("items_export_file", "item export"):
            code = r.get("code") or ""
            if code.upper() not in wanted:
                continue
            # a column the export does not have is not compared (None)
            text = {k: r.get(k) for k in ("description", "uom", "revision", "material")}
            out[code] = ErpItemRecord(code=code, **text,
                                      active=flag(r.get("active")) if "active" in r else None,
                                      from_kosha=flag(r.get("from_kosha")) if "from_kosha" in r else None)
        return out

    def fetch_bom(self, code: str) -> ErpBomRecord | None:
        rows = [r for r in self._export("boms_export_file", "BOM export")
                if (r.get("parent_code") or "").upper() == code.upper() and r.get("component_code")]
        if not rows:
            return None
        lines = tuple(ErpBomLine(r["component_code"], number(r.get("qty")) or 0.0, r.get("uom") or "") for r in rows)
        revs = {r.get("revision") for r in rows if "revision" in r}
        refs = [r.get("reference") for r in rows if r.get("reference")]
        return ErpBomRecord(code=rows[0]["parent_code"], lines=lines,
                            revision=revs.pop() if len(revs) == 1 else None, reference=refs[0] if refs else "")

    def list_items_from_kosha(self) -> list[str]:
        rows = self._export("items_export_file", "item export")
        if rows and "from_kosha" not in rows[0]:
            raise ErpNotSupported("The ERP's item export has no from_kosha column, so Kosha cannot tell which items "
                                  "it created there.")
        return [r["code"] for r in rows if r.get("code") and flag(r.get("from_kosha"))]
