# SPDX-License-Identifier: AGPL-3.0-or-later
"""Standard-parts library: market components (fasteners, bearings, keys ...) described by their standards.

A library table is one CSV file in a pack plus its definition in ``pack.yaml``: the columns (sizes from the
standard), the options chosen when a part is used (length, property class, seals ...) and three templates
for the designation, the part number and the description. Every row names its source standard and edition
and says whether it has been verified against the standard.

"Use this part" turns a row and its options into a Kosha item with a deterministic part number, for example
``FST-ISO4017-M16X60-8.8``; using the same part again returns the same item (see ``NumberingService``).
"""
from __future__ import annotations

import csv
import io
import itertools
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from .numbering import (CODE_OK, ID_OK, NAME_OK, BuildResult, DataError, FieldError, FieldSpec, NumberingService,
                        Template, _check_keys, _text, check_refs, fmt_decimal, plain_values, value_text,
                        _attr_checker)

ROW_META = ("standard", "edition", "verified")
COLUMN_TYPES = ("decimal", "text")


@dataclass
class LibraryRow:
    table: str
    key: str
    values: dict                      # column -> text (numbers already normalised: "1.25", "16")
    standard: str
    edition: str
    verified: bool
    origin: str = "pack"              # pack | changed | local

    def to_value(self) -> dict:
        return {"key": self.key, "values": self.values, "standard": self.standard, "edition": self.edition,
                "verified": self.verified}

    def to_dict(self) -> dict:
        return {**self.to_value(), "table": self.table, "origin": self.origin}


class LibraryTable:
    """The definition of one table: columns, options and templates."""

    KEYS = {"id", "file", "category", "label", "standard", "aliases", "key", "size", "columns", "options",
            "designation", "part_number", "description", "uom", "make_buy"}

    def __init__(self, data: Any, where: str, categories: set[str] | None = None):
        if not isinstance(data, dict):
            raise DataError(f"{where}: a library table must be a mapping")
        _check_keys(data, self.KEYS, where)
        self.data = data
        self.id = _text(data, "id", where)
        if not ID_OK.match(self.id):
            raise DataError(f"{where}: id '{self.id}' may use small letters, digits and dashes only")
        where = f"{where} (table '{self.id}')"
        self.where = where
        self.file = _text(data, "file", where, False)
        self.category = _text(data, "category", where)
        if categories is not None and self.category not in categories:
            raise DataError(f"{where}: category '{self.category}' is not defined "
                            f"(known: {', '.join(sorted(categories)) or 'none'})")
        self.label = _text(data, "label", where)
        self.standard = _text(data, "standard", where)
        aliases = data.get("aliases") or []
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise DataError(f"{where}: aliases must be a list of text, for example [DIN 933]")
        self.aliases = [a.strip() for a in aliases]
        self.uom = _text(data, "uom", where, False, "Nos")
        self.make_buy = _text(data, "make_buy", where, False, "buy")
        if self.make_buy not in ("make", "buy"):
            raise DataError(f"{where}: make_buy must be make or buy")
        cols = data.get("columns")
        if not isinstance(cols, dict) or not cols:
            raise DataError(f"{where}: 'columns' must list the size columns of the CSV file")
        self.columns: dict[str, dict] = {}
        for name, spec in cols.items():
            cw = f"{where}, column '{name}'"
            if not isinstance(name, str) or not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,39}$", name) or name in ROW_META:
                raise DataError(f"{cw}: column names use letters, digits and _ and cannot be {', '.join(ROW_META)}")
            spec = spec or {}
            if not isinstance(spec, dict):
                raise DataError(f"{cw}: must be a mapping such as {{label: Thread, unit: mm}}")
            _check_keys(spec, {"label", "unit", "type"}, cw)
            ctype = spec.get("type", "decimal")
            if ctype not in COLUMN_TYPES:
                raise DataError(f"{cw}: type must be decimal or text")
            self.columns[name] = {"label": _text(spec, "label", cw, False) or name, "unit": _text(spec, "unit", cw, False),
                                  "type": ctype}
        self.size = _text(data, "size", where)
        if self.size not in self.columns:
            raise DataError(f"{where}: size '{self.size}' must be one of the columns")
        opts = data.get("options") or {}
        if not isinstance(opts, dict):
            raise DataError(f"{where}: 'options' must be a mapping")
        self.options: dict[str, FieldSpec] = {}
        for name, spec in opts.items():
            if name in self.columns:
                raise DataError(f"{where}: option '{name}' has the same name as a column")
            f = FieldSpec(name, spec, where)
            if f.type == "text" or (f.type == "choice" and f.choices is None) or \
                    (f.type in ("int", "decimal") and f.numeric_choices is None):
                raise DataError(f"{where}, option '{name}': library options need a list of choices")
            if f.more_than and f.more_than not in self.columns:
                raise DataError(f"{where}, option '{name}': more_than '{f.more_than}' is not a column")
            self.options[name] = f
        if not all(NAME_OK.match(n) for n in self.options):
            raise DataError(f"{where}: option names use small letters, digits and _")
        self.key = Template(data.get("key"), f"{where}: 'key'")
        refs_ok = _attr_checker(self.options, None)
        check_refs(self.key, f"{where}: 'key'", {}, extra=self.columns)
        self.designation = Template(data.get("designation"), f"{where}: 'designation'")
        self.part_number = Template(data.get("part_number"), f"{where}: 'part_number'")
        self.description = Template(data.get("description"), f"{where}: 'description'")
        for label, t in (("designation", self.designation), ("part_number", self.part_number),
                         ("description", self.description)):
            check_refs(t, f"{where}: '{label}'", self.options, extra=self.columns, attr_ok=refs_ok)
        pn_names = self.part_number.names()
        missing = [n for n in [*self.options, *self.key.names()] if n not in pn_names]
        if missing:
            raise DataError(f"{where}: 'part_number' must use {', '.join('{' + m + '}' for m in missing)}, otherwise "
                            "different parts would get the same number")

    # -- rows
    def parse_csv(self, text: str, where: str) -> list[LibraryRow]:
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration:
            raise DataError(f"{where}: the file is empty; the first line must name the columns") from None
        header = [h.strip() for h in header]
        wanted = [*self.columns, *ROW_META]
        missing = [c for c in wanted if c not in header]
        if missing:
            raise DataError(f"{where}: the first line is missing the column{'s' if len(missing) > 1 else ''} "
                            f"{', '.join(missing)}")
        extra = [c for c in header if c not in wanted]
        if extra:
            raise DataError(f"{where}: column{'s' if len(extra) > 1 else ''} {', '.join(extra)} "
                            f"{'are' if len(extra) > 1 else 'is'} not defined for table '{self.id}' in pack.yaml")
        rows: list[LibraryRow] = []
        seen: dict[str, int] = {}
        for n, cells in enumerate(reader, start=2):
            if not any(c.strip() for c in cells):
                continue
            if len(cells) != len(header):
                raise DataError(f"{where} row {n}: has {len(cells)} values but the first line names {len(header)} columns")
            raw = {h: c.strip() for h, c in zip(header, cells)}
            row = self.row_from(raw, f"{where} row {n}")
            if row.key in seen:
                raise DataError(f"{where} row {n}: {row.key} appears twice (first on row {seen[row.key]})")
            seen[row.key] = n
            rows.append(row)
        return rows

    def row_from(self, raw: dict, where: str) -> LibraryRow:
        """Check one row given as {column: text, standard, edition, verified}."""
        values = {}
        for name, col in self.columns.items():
            v = raw.get(name)
            v = "" if v is None else str(v).strip()
            if not v:
                raise DataError(f"{where}: '{name}' ({col['label']}) is empty")
            if col["type"] == "decimal":
                try:
                    d = Decimal(v.replace(",", "."))
                    if not d.is_finite() or d < 0:
                        raise InvalidOperation
                except InvalidOperation:
                    raise DataError(f"{where}: '{name}' ({col['label']}) must be a number, found '{v}'") from None
                v = fmt_decimal(d)
            values[name] = v
        standard = str(raw.get("standard") or "").strip()
        if not standard:
            raise DataError(f"{where}: 'standard' is empty; every row names its source standard, for example ISO 4017")
        edition = str(raw.get("edition") or "").strip()
        ver = raw.get("verified")
        if isinstance(ver, bool):
            verified = ver
        else:
            ver = str(ver or "").strip().lower()
            if ver not in ("yes", "no"):
                raise DataError(f"{where}: 'verified' must be yes or no, found '{ver}'")
            verified = ver == "yes"
        if verified and not edition:
            raise DataError(f"{where}: a verified row must name the edition of the standard it was checked against")
        key = self.key.render(lambda n, a, w: values[n]).strip()
        if not key:
            raise DataError(f"{where}: the row key is empty")
        return LibraryRow(self.id, key, values, standard, edition, verified)

    def row_from_value(self, value: dict, where: str, origin: str = "pack") -> LibraryRow:
        if not isinstance(value, dict) or not isinstance(value.get("values"), dict):
            raise DataError(f"{where}: a library row needs 'values' with the size columns")
        row = self.row_from({**value["values"], "standard": value.get("standard"), "edition": value.get("edition"),
                             "verified": value.get("verified")}, where)
        row.origin = origin
        return row

    # -- building parts
    def parse_options(self, row: LibraryRow, options: dict) -> tuple[dict, dict]:
        parsed, errors = {}, {}
        options = options if isinstance(options, dict) else {}
        for name, f in self.options.items():
            try:
                parsed[name] = f.parse(options.get(name), None, row.values)
            except FieldError as e:
                errors[name] = str(e)
        return parsed, errors

    def build(self, row: LibraryRow, options: dict) -> dict:
        parsed, errors = self.parse_options(row, options)
        if errors:
            return {"ok": False, "errors": errors, "error": next(iter(errors.values()))}
        return self._render(row, parsed)

    def _render(self, row: LibraryRow, parsed: dict) -> dict:
        def resolve(name, attr, _w):
            if name in parsed:
                return value_text(parsed[name], attr)
            return row.values[name]
        pn = self.part_number.render(resolve).strip().upper()
        if not CODE_OK.match(pn):
            return {"ok": False, "errors": {"_": f"The part number '{pn}' has characters a part number cannot use."},
                    "error": f"The part number '{pn}' has characters a part number cannot use."}
        return {"ok": True, "errors": {}, "part_number": pn,
                "designation": " ".join(self.designation.render(resolve).split()),
                "description": " ".join(self.description.render(resolve).split()),
                "options": plain_values(parsed)}

    def variants(self, row: LibraryRow):
        """Every valid combination of options for one row, already built."""
        names = list(self.options)
        lists = [self.options[n].numeric_choices or self.options[n].choices for n in names]
        for combo in itertools.product(*lists):
            raw = {n: (fmt_decimal(c) if isinstance(c, Decimal) else c.code) for n, c in zip(names, combo)}
            parsed, errors = self.parse_options(row, raw)
            if errors:
                continue
            r = self._render(row, parsed)
            if r["ok"]:
                yield r

    def to_dict(self, rows: list[LibraryRow] | None = None) -> dict:
        out = {"id": self.id, "label": self.label, "category": self.category, "standard": self.standard,
               "aliases": self.aliases, "size": self.size, "uom": self.uom, "key": self.key.text,
               "columns": [{"name": n, **c} for n, c in self.columns.items()],
               "options": [f.to_dict(None) for f in self.options.values()],
               "designation": self.designation.text, "part_number": self.part_number.text}
        if rows is not None:
            out["rows"] = len(rows)
            out["verified"] = sum(1 for r in rows if r.verified)
            sizes = sorted({r.values[self.size] for r in rows}, key=_size_key)
            out["sizes"] = sizes
        return out


def _size_key(v: str):
    try:
        return (0, Decimal(v), v)
    except InvalidOperation:
        return (1, Decimal(0), v)


# ------------------------------------------------------------------ search --

def normalise(text: str) -> str:
    t = (text or "").upper().replace("×", "X").replace("Ø", " ")
    t = re.sub(r"(?<=\d)\s*[X*]\s*(?=\d)", "X", t)        # 16 x 60 -> 16X60
    t = re.sub(r"\bM\s+(?=\d)", "M", t)                     # M 16 -> M16
    return " ".join(t.split())


def _token_re(token: str) -> re.Pattern:
    """A search word matches at a boundary: "M8" finds "M8X20" but not "M80", "1" does not find "16", "A" does
    not find the A in "PARALLEL"; a word ending in a letter also finds longer words ("SCR" finds "SCREW")."""
    left = r"(?<![A-Z0-9])" if token[0].isalpha() else r"(?<![0-9.])"
    right = r"(?![0-9])" if token[-1].isdigit() else ""
    return re.compile(left + re.escape(token) + right)


@dataclass
class _Entry:
    text: str
    hit: dict = field(default_factory=dict)


class StandardPartsLibrary:
    """Browse, search and use standard parts from the installed packs."""

    def __init__(self, config=None):
        if config is None:
            from .packs import PackConfig
            config = PackConfig.current()
        self.config = config

    # -- tables and rows
    def tables(self) -> list[LibraryTable]:
        out = []
        for rec in self.config.records("library_table"):
            try:
                out.append(LibraryTable(rec.value, f"library table '{rec.key}'"))
            except DataError:
                continue
        return out

    def table(self, table_id: str) -> LibraryTable:
        for t in self.tables():
            if t.id == table_id:
                return t
        raise LookupError(f"There is no library table '{table_id}'. Reload the page; an admin may have removed its pack.")

    def rows(self, table: LibraryTable) -> list[LibraryRow]:
        out = []
        for rec in self.config.records("library_row", scope=table.id):
            try:
                out.append(table.row_from_value(rec.value, f"library row '{rec.key}'", rec.origin))
            except DataError:
                continue
        return out

    def row(self, table: LibraryTable, key: str) -> LibraryRow:
        for r in self.rows(table):
            if r.key.upper() == (key or "").strip().upper():
                return r
        raise LookupError(f"{table.standard} has no size '{key}' in the library.")

    def overview(self) -> list[dict]:
        cats: dict[str, dict] = {}
        for t in self.tables():
            rows = self.rows(t)
            c = cats.setdefault(t.category, {"code": t.category,
                                             "name": (self.config.category(t.category) or {}).get("name", t.category),
                                             "tables": [], "rows": 0})
            c["tables"].append(t.to_dict(rows))
            c["rows"] += len(rows)
        return list(cats.values())

    def browse(self, table_id: str, size: str = "", q: str = "") -> dict:
        t = self.table(table_id)
        rows = self.rows(t)
        all_rows = rows
        if size:
            try:
                want = Decimal(size.replace(",", "."))
                rows = [r for r in rows if _size_key(r.values[t.size])[1] == want]
            except InvalidOperation:
                rows = [r for r in rows if r.values[t.size].upper() == size.strip().upper()]
        if q:
            tokens = [_token_re(tok) for tok in normalise(q).split()]
            rows = [r for r in rows if all(p.search(normalise(" ".join([r.key, *r.values.values()]))) for p in tokens)]
        return {"table": t.to_dict(all_rows), "rows": [r.to_dict() for r in rows],
                "options": [f.to_dict(None) for f in t.options.values()]}

    # -- search by designation
    def _index(self) -> list[_Entry]:
        """Every valid variant of every row, built once per configuration (a pack change makes a new one)."""
        cached = getattr(self.config, "_library_index", None)
        if cached is not None:
            return cached
        entries: list[_Entry] = []
        for t in self.tables():
            words = " ".join([t.standard, *t.aliases, t.label])
            for row in self.rows(t):
                for v in t.variants(row):
                    text = normalise(" ".join([v["designation"], v["part_number"], v["description"], words]))
                    entries.append(_Entry(text, {"table": t.id, "table_label": t.label, "category": t.category,
                                                 "row": row.key, "options": v["options"],
                                                 "designation": v["designation"], "part_number": v["part_number"],
                                                 "description": v["description"], "standard": row.standard,
                                                 "edition": row.edition, "verified": row.verified}))
        self.config._library_index = entries
        return entries

    def search(self, q: str, limit: int = 50) -> dict:
        nq = normalise(q)
        tokens = [_token_re(tok) for tok in nq.split()]
        if not tokens:
            return {"hits": [], "total": 0}
        hits = [e for e in self._index() if all(p.search(e.text) for p in tokens)]
        hits.sort(key=lambda e: 0 if normalise(e.hit["designation"]) == nq or e.hit["part_number"] == nq else 1)
        out = [dict(e.hit) for e in hits[:limit]]
        for h in out:
            it = NumberingService.find_item(h["part_number"])
            h["existing"] = {"id": it["id"], "part_number": it["part_number"], "state": it["state"]} if it else None
        return {"hits": out, "total": len(hits)}

    # -- use
    def preview(self, table_id: str, row_key: str, options: dict) -> dict:
        t = self.table(table_id)
        row = self.row(t, row_key)
        r = t.build(row, options)
        r.update({"standard": row.standard, "edition": row.edition, "verified": row.verified, "table": t.id,
                  "row": row.key})
        if r["ok"]:
            it = NumberingService.find_item(r["part_number"])
            r["existing"] = {"id": it["id"], "part_number": it["part_number"], "description": it["description"],
                             "state": it["state"]} if it else None
        return r

    def use(self, table_id: str, row_key: str, options: dict, user_id: int | None) -> dict:
        t = self.table(table_id)
        row = self.row(t, row_key)
        r = t.build(row, options)
        if not r["ok"]:
            from .numbering import NumberingError
            raise NumberingError(r["error"], r["errors"])
        cat = self.config.category(t.category) or {}
        values = {"row": row.key, **r["options"]}
        res = NumberingService(self.config).ensure_coded_item(
            scheme=f"library:{t.id}", code=r["part_number"], description=r["description"], values=values,
            user_id=user_id, uom=t.uom or cat.get("uom", ""), material="", make_buy=t.make_buy or cat.get("make_buy", "buy"),
            props={"category": t.category,
                   "library": {"table": t.id, "row": row.key, "designation": r["designation"], "options": r["options"],
                               "standard": row.standard, "edition": row.edition, "verified": row.verified}})
        res["designation"] = r["designation"]
        res["verified"] = row.verified
        return res


__all__ = ["LibraryRow", "LibraryTable", "StandardPartsLibrary", "BuildResult", "normalise"]
