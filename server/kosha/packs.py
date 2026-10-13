# SPDX-License-Identifier: AGPL-3.0-or-later
"""Industry packs: declarative data (YAML for structure, CSV for tables) that gives a Kosha installation its
categories, item types, units, item fields, dictionaries, spec-code templates, number schemes, title-block maps
and standard-parts library.

Three layers, so a pack can be updated without losing what the company changed:

* ``pack_records``: exactly what each installed pack put in (replaced when the pack is updated);
* ``pack_local``: what the company changed or added (never touched by a pack update or removal);
* ``PackConfig``: the effective configuration, the pack records with the company's changes on top.

Removing a pack switches its records off. It never deletes items, part numbers or the company's own changes.
Everything is checked when it is loaded; errors name the file and the row, in plain words.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import config as kconfig
from . import db
from .library import LibraryTable
from .numbering import (CATEGORIES_DICTIONARY, NAME_OK, Choice, DataError, FieldSpec, SequenceScheme, SpecTemplate,
                        _check_keys, _text, choice_from)

log = logging.getLogger("kosha.packs")

PackError = DataError

KINDS = {
    "category": "Categories", "item_type": "Item types", "unit": "Units", "field": "Item fields",
    "dictionary": "Dictionary entries", "spec_template": "Spec-code templates", "numbering_scheme": "Number schemes",
    "title_block": "Title-block maps", "library_table": "Library tables", "library_row": "Library rows",
}
PACK_KEYS = {"id", "name", "version", "licence", "description", "categories", "item_types", "units", "fields",
             "dictionaries", "spec_codes", "numbering", "title_blocks", "tables"}
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
CATEGORY_CODE = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")
SLUG = re.compile(r"^[a-z][a-z0-9\-]{0,39}$")
TITLE_FIELDS = ("part_number", "description", "material", "revision", "weight_kg", "scale", "drawn_by")


def packs_dir() -> Path:
    return Path(os.environ.get("KOSHA_PACKS_DIR") or kconfig.REPO_DIR / "packs").resolve()


def version_tuple(v: str) -> tuple:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except ValueError:
        return (0,)


def canonical(value: Any) -> str:
    """For comparing values: key order does not matter."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def stored(value: Any) -> str:
    """For storing values: key order is kept (fields appear on screen in the order the pack lists them)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass
class Record:
    kind: str
    key: str
    scope: str
    value: dict
    source: str = ""

    def label(self) -> str:
        v = self.value
        name = v.get("name") or v.get("label") or ""
        if self.kind == "library_row":
            return f"{self.key} ({v.get('standard', '')})"
        return f"{self.key} — {name}" if name and name != self.key else self.key


@dataclass
class Pack:
    id: str
    name: str
    version: str
    licence: str
    description: str
    folder: Path
    records: list[Record] = field(default_factory=list)

    def counts(self) -> dict:
        out: dict[str, int] = {}
        for r in self.records:
            out[r.kind] = out.get(r.kind, 0) + 1
        return out

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "version": self.version, "licence": self.licence,
                "description": self.description, "counts": self.counts()}


# ------------------------------------------------------------------ loading --

class PackLoader:
    """Reads and checks the packs in a folder (``packs/`` in the source tree, or ``KOSHA_PACKS_DIR``)."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root).resolve() if root else packs_dir()

    def _rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root.parent)).replace(os.sep, "/")
        except ValueError:
            return str(p)

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and (p / "pack.yaml").is_file())

    def load_all(self) -> tuple[list[Pack], dict[str, str]]:
        packs, problems = [], {}
        for pid in self.ids():
            try:
                packs.append(self.load(pid))
            except DataError as e:
                problems[pid] = str(e)
        return packs, problems

    def load(self, pack_id: str) -> Pack:
        if not isinstance(pack_id, str) or not SLUG.match(pack_id):
            raise DataError(f"'{pack_id}' is not a pack name")
        folder = self.root / pack_id
        path = folder / "pack.yaml"
        if not path.is_file():
            raise DataError(f"There is no pack '{pack_id}' in {self._rel(self.root)}")
        data = self._yaml(path)
        return self._build(folder, path, data)

    def _yaml(self, path: Path) -> Any:
        where = self._rel(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise DataError(f"{where}: could not read the file ({e})") from None
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            line = f" line {mark.line + 1}" if mark else ""
            problem = getattr(e, "problem", None) or str(e)
            raise DataError(f"{where}{line}: this is not valid YAML ({problem})") from None

    def _file(self, folder: Path, rel: Any, where: str) -> Path:
        if not isinstance(rel, str) or not rel.strip():
            raise DataError(f"{where}: a file name is missing")
        p = (folder / rel).resolve()
        if folder.resolve() not in p.parents:
            raise DataError(f"{where}: '{rel}' must be a file inside the pack folder")
        if not p.is_file():
            raise DataError(f"{where}: the file '{rel}' does not exist")
        return p

    def _build(self, folder: Path, path: Path, data: Any) -> Pack:
        where = self._rel(path)
        if not isinstance(data, dict):
            raise DataError(f"{where}: the file must be a mapping with id, name and version")
        _check_keys(data, PACK_KEYS, where)
        pid = _text(data, "id", where)
        if pid != folder.name:
            raise DataError(f"{where}: id '{pid}' must be the same as the folder name '{folder.name}'")
        version = str(data.get("version") or "")
        if not VERSION.match(version):
            raise DataError(f"{where}: version must look like 1.0.0 (found '{version}')")
        pack = Pack(pid, _text(data, "name", where), version, _text(data, "licence", where, False),
                    " ".join(_text(data, "description", where, False).split()), folder)
        recs = pack.records
        seen: set[tuple[str, str]] = set()

        def items(key: str) -> list:
            v = data.get(key) or []
            if not isinstance(v, list):
                raise DataError(f"{where}: '{key}' must be a list")
            return v

        def keyed(kind: str, key: str, value: dict, src: str, scope: str = ""):
            if (kind, key) in seen:
                raise DataError(f"{src}: '{key}' appears twice")
            seen.add((kind, key))
            recs.append(Record(kind, key, scope, value, src))

        # categories
        for i, c in enumerate(items("categories"), 1):
            src = f"{where}, categories entry {i}"
            keyed("category", *self._category(c, src), src)
        categories = {r.key for r in recs if r.kind == "category"}
        for i, t in enumerate(items("item_types"), 1):
            src = f"{where}, item_types entry {i}"
            keyed("item_type", *self._item_type(t, src), src)
        for i, u in enumerate(items("units"), 1):
            src = f"{where}, units entry {i}"
            keyed("unit", *self._unit(u, src), src)
        for i, f in enumerate(items("fields"), 1):
            src = f"{where}, fields entry {i}"
            keyed("field", *self._field(f, src), src)
        # dictionaries
        dicts = data.get("dictionaries") or {}
        if not isinstance(dicts, dict):
            raise DataError(f"{where}: 'dictionaries' must be a mapping of name to a list of entries")
        dict_entries: dict[str, list[Choice]] = {}
        for name, entries in dicts.items():
            src = f"{where}, dictionary '{name}'"
            if not isinstance(name, str) or not NAME_OK.match(name) or name == CATEGORIES_DICTIONARY:
                raise DataError(f"{src}: dictionary names use small letters, digits and _ (and '{CATEGORIES_DICTIONARY}' "
                                "is reserved)")
            if not isinstance(entries, list) or not entries:
                raise DataError(f"{src}: must be a list of entries with code and name")
            dict_entries[name] = []
            for i, e in enumerate(entries, 1):
                esrc = f"{src} entry {i}"
                ch = choice_from(e, esrc)
                if not isinstance(e, dict):
                    raise DataError(f"{esrc}: each entry needs a code and a name, for example {{code: C45, name: C45 steel}}")
                keyed("dictionary", f"{name}/{ch.code}", {"code": ch.code, "name": ch.name, **ch.extra}, esrc, name)
                dict_entries[name].append(ch)
        # spec-code templates
        for i, rel in enumerate(items("spec_codes"), 1):
            src = f"{where}, spec_codes entry {i}"
            p = self._file(folder, rel, src)
            tdata = self._yaml(p)
            t = SpecTemplate(tdata, self._rel(p), dict_entries, categories)
            keyed("spec_template", t.id, tdata, self._rel(p), t.category)
        # number schemes
        for i, s in enumerate(items("numbering"), 1):
            src = f"{where}, numbering entry {i}"
            sch = SequenceScheme(s, src, dict_entries)
            keyed("numbering_scheme", sch.id, s, src)
        # title-block maps
        for i, tb in enumerate(items("title_blocks"), 1):
            src = f"{where}, title_blocks entry {i}"
            keyed("title_block", *self._title_block(tb, src), src)
        # library tables
        for i, td in enumerate(items("tables"), 1):
            src = f"{where}, tables entry {i}"
            t = LibraryTable(td, src, categories)
            if not t.file:
                raise DataError(f"{src} (table '{t.id}'): 'file' (the CSV file with the rows) is missing")
            p = self._file(folder, t.file, src)
            try:
                text = p.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError) as e:
                raise DataError(f"{self._rel(p)}: could not read the file ({e}); save it as UTF-8 CSV") from None
            rows = t.parse_csv(text, self._rel(p))
            keyed("library_table", t.id, td, src, t.category)
            for row in rows:
                keyed("library_row", f"{t.id}/{row.key}", row.to_value(), f"{self._rel(p)} {row.key}", t.id)
        return pack

    # -- record checks (also used for the company's own changes)
    @staticmethod
    def _category(c: Any, src: str) -> tuple[str, dict]:
        if not isinstance(c, dict):
            raise DataError(f"{src}: must be a mapping such as {{code: GR, name: Gears}}")
        _check_keys(c, {"code", "name", "make_buy", "uom", "description"}, src)
        code = _text(c, "code", src)
        if not CATEGORY_CODE.match(code):
            raise DataError(f"{src}: category code '{code}' must be 1 to 6 capital letters or digits, starting with a letter")
        out = {"code": code, "name": _text(c, "name", src)}
        if c.get("make_buy") is not None:
            if c["make_buy"] not in ("make", "buy"):
                raise DataError(f"{src}: make_buy must be make or buy")
            out["make_buy"] = c["make_buy"]
        for k in ("uom", "description"):
            if c.get(k):
                out[k] = _text(c, k, src)
        return code, out

    @staticmethod
    def _item_type(t: Any, src: str) -> tuple[str, dict]:
        if not isinstance(t, dict):
            raise DataError(f"{src}: must be a mapping such as {{code: weldment, name: Weldment, base: assembly}}")
        _check_keys(t, {"code", "name", "base", "description"}, src)
        code = _text(t, "code", src)
        if not SLUG.match(code):
            raise DataError(f"{src}: item type code '{code}' may use small letters, digits and dashes only")
        base = t.get("base", "part")
        if base not in ("part", "assembly"):
            raise DataError(f"{src}: base must be part or assembly (how Kosha treats this type)")
        out = {"code": code, "name": _text(t, "name", src), "base": base}
        if t.get("description"):
            out["description"] = _text(t, "description", src)
        return code, out

    @staticmethod
    def _unit(u: Any, src: str) -> tuple[str, dict]:
        if not isinstance(u, dict):
            raise DataError(f"{src}: must be a mapping such as {{code: Nos, name: Numbers}}")
        code = _text(u, "code", src)
        if not re.match(r"^[A-Za-z][A-Za-z0-9.]{0,9}$", code):
            raise DataError(f"{src}: unit code '{code}' must be 1 to 10 letters or digits")
        out = {"code": code, "name": _text(u, "name", src)}
        for k, v in u.items():
            if k in ("code", "name"):
                continue
            if not isinstance(v, str):
                raise DataError(f"{src}: '{k}' must be text in quotes")
            out[k] = v.strip()
        return code, out

    @staticmethod
    def _field(f: Any, src: str) -> tuple[str, dict]:
        if not isinstance(f, dict):
            raise DataError(f"{src}: must be a mapping with key, label and type")
        key = _text(f, "key", src)
        FieldSpec(key, {k: v for k, v in f.items() if k != "key"}, src)
        return key, dict(f)

    @staticmethod
    def _title_block(tb: Any, src: str) -> tuple[str, dict]:
        if not isinstance(tb, dict):
            raise DataError(f"{src}: must be a mapping with id, label and map")
        _check_keys(tb, {"id", "label", "map"}, src)
        tid = _text(tb, "id", src)
        if not SLUG.match(tid):
            raise DataError(f"{src}: id '{tid}' may use small letters, digits and dashes only")
        m = tb.get("map")
        if not isinstance(m, dict) or not m:
            raise DataError(f"{src}: 'map' must list attribute names and the Kosha field each fills")
        for attr, target in m.items():
            if target not in TITLE_FIELDS:
                raise DataError(f"{src}: attribute '{attr}' maps to '{target}', which is not one of "
                                f"{', '.join(TITLE_FIELDS)}")
        return tid, {"id": tid, "label": _text(tb, "label", src), "map": {str(k).upper(): v for k, v in m.items()}}


# --------------------------------------------------------- effective config --

@dataclass
class Rec:
    kind: str
    key: str
    scope: str
    value: dict
    origin: str                        # pack | changed | local
    pack_id: str | None = None         # the pack whose record is used
    providers: list = field(default_factory=list)
    pack_value: dict | None = None
    conflict: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "key": self.key, "scope": self.scope, "value": self.value, "origin": self.origin,
                "pack_id": self.pack_id, "providers": self.providers, "pack_value": self.pack_value,
                "conflict": self.conflict}


class PackConfig:
    """The effective configuration: installed packs' records, with the company's changes on top."""

    _lock = threading.Lock()
    _cached: "PackConfig | None" = None

    def __init__(self, recs: list[Rec], hidden: list[Rec], generation: int):
        self.generation = generation
        self._db = ""
        self._library_index = None        # filled by StandardPartsLibrary on first search
        self._recs = recs
        self._hidden = hidden
        self._by = {(r.kind, r.key): r for r in recs}

    @classmethod
    def current(cls) -> "PackConfig":
        gen = int(db.get_setting("packs_generation") or 0)
        with cls._lock:
            if cls._cached is not None and cls._cached.generation == gen and cls._cached._db == str(kconfig.DB_PATH):
                return cls._cached
        cfg = cls.load(gen)
        cfg._db = str(kconfig.DB_PATH)
        with cls._lock:
            cls._cached = cfg
        return cfg

    @classmethod
    def load(cls, generation: int = 0) -> "PackConfig":
        merged: dict[tuple, Rec] = {}
        order: list[tuple] = []
        for r in db.rows("SELECT r.pack_id, r.kind, r.rkey, r.scope, r.value_json FROM pack_records r "
                         "JOIN packs p ON p.id = r.pack_id WHERE p.status = 'installed' ORDER BY p.seq, r.pos"):
            k = (r["kind"], r["rkey"])
            value = json.loads(r["value_json"])
            if k in merged:                      # an earlier pack wins; a later one may only add missing keys
                m = merged[k]
                m.providers.append(r["pack_id"])
                for vk, vv in value.items():
                    m.value.setdefault(vk, vv)
                continue
            merged[k] = Rec(r["kind"], r["rkey"], r["scope"], value, "pack", r["pack_id"], [r["pack_id"]])
            order.append(k)
        for rec in merged.values():
            rec.pack_value = json.loads(json.dumps(rec.value))
        hidden: list[Rec] = []
        for loc in db.rows("SELECT * FROM pack_local ORDER BY updated_at, kind, rkey"):
            k = (loc["kind"], loc["rkey"])
            base = merged.get(k)
            conflict = bool(base and loc["base_json"] is not None and canonical(json.loads(loc["base_json"])) !=
                            canonical(base.pack_value))
            if loc["hidden"]:
                if base:
                    base.conflict = conflict
                    hidden.append(base)
                    merged.pop(k)
                    order.remove(k)
                continue
            value = json.loads(loc["value_json"] or "{}")
            if base:
                base.value, base.origin, base.conflict = value, "changed", conflict
            else:
                merged[k] = Rec(loc["kind"], loc["rkey"], loc["scope"], value, "local")
                order.append(k)
        return cls([merged[k] for k in order], hidden, generation)

    # -- generic access
    def records(self, kind: str, scope: str | None = None) -> list[Rec]:
        return [r for r in self._recs if r.kind == kind and (scope is None or r.scope == scope)]

    def hidden(self) -> list[Rec]:
        return list(self._hidden)

    def get(self, kind: str, key: str) -> Rec | None:
        return self._by.get((kind, key))

    def value(self, kind: str, key: str) -> dict | None:
        r = self.get(kind, key)
        return r.value if r else None

    # -- typed helpers
    def categories(self) -> list[dict]:
        return [r.value for r in self.records("category")]

    def category(self, code: str | None) -> dict | None:
        return self.value("category", code or "")

    def item_type(self, code: str) -> dict | None:
        return self.value("item_type", code or "")

    def dictionary(self, name: str) -> list[Choice]:
        out = []
        for r in self.records("dictionary", scope=name):
            v = r.value
            out.append(Choice(str(v.get("code", "")), str(v.get("name", v.get("code", ""))),
                              {k: x for k, x in v.items() if k not in ("code", "name")}))
        return out

    def dictionaries_with_entries(self) -> dict[str, list[Choice]]:
        names = {r.scope for r in self._recs if r.kind == "dictionary"}
        return {n: self.dictionary(n) for n in sorted(names)}

    def title_block_tags(self) -> dict[str, str]:
        """Attribute name -> Kosha field, from every installed title-block map (for CAD readers)."""
        out: dict[str, str] = {}
        for r in self.records("title_block"):
            for attr, target in (r.value.get("map") or {}).items():
                out.setdefault(str(attr).upper(), target)
        return out


def _bump_generation() -> None:
    gen = int(db.get_setting("packs_generation") or 0) + 1
    db.set_setting("packs_generation", gen)


# --------------------------------------------------------------- management --

class PackManager:
    """Install, update and remove packs; record and undo the company's own changes."""

    def __init__(self, loader: PackLoader | None = None):
        self.loader = loader or PackLoader()

    # -- reading
    @staticmethod
    def installed() -> dict[str, dict]:
        return {r["id"]: r for r in db.rows("SELECT * FROM packs ORDER BY seq")}

    def overview(self) -> list[dict]:
        packs, problems = self.loader.load_all()
        installed = self.installed()
        local = db.rows("SELECT kind, rkey FROM pack_local")
        out = []
        seen = set()
        for p in packs:
            seen.add(p.id)
            row = installed.get(p.id)
            mine = {(r.kind, r.key) for r in p.records}
            out.append({**p.info(), "available_version": p.version, **self._state(row, p.version),
                        "local_changes": sum(1 for x in local if (x["kind"], x["rkey"]) in mine),
                        "problem": None})
        for pid, msg in problems.items():
            seen.add(pid)
            row = installed.get(pid)
            out.append({"id": pid, "name": row["name"] if row else pid, "version": row["version"] if row else "",
                        "licence": row["licence"] if row else "", "description": row["description"] if row else "",
                        "counts": self._installed_counts(pid) if row else {}, "available_version": None,
                        **self._state(row, None), "local_changes": 0, "problem": msg})
        for pid, row in installed.items():
            if pid not in seen:
                out.append({"id": pid, "name": row["name"], "version": row["version"], "licence": row["licence"],
                            "description": row["description"], "counts": self._installed_counts(pid),
                            "available_version": None, **self._state(row, None), "local_changes": 0,
                            "problem": "The pack's files are no longer on the server. What it installed is kept."})
        return out

    @staticmethod
    def _installed_counts(pid: str) -> dict:
        return {r["kind"]: r["n"] for r in db.rows("SELECT kind, COUNT(*) AS n FROM pack_records WHERE pack_id = ? "
                                                   "GROUP BY kind", (pid,))}

    @staticmethod
    def _state(row: dict | None, available: str | None) -> dict:
        if not row or row["status"] != "installed":
            return {"installed_version": None, "status": "removed" if row else "not_installed",
                    "installed_at": row["installed_at"] if row else None, "update_available": False}
        newer = bool(available and version_tuple(available) > version_tuple(row["version"]))
        return {"installed_version": row["version"], "status": "installed", "installed_at": row["installed_at"],
                "updated_at": row["updated_at"], "update_available": newer}

    def detail(self, pack_id: str) -> dict:
        try:
            pack = self.loader.load(pack_id)
            records, info, problem = pack.records, pack.info(), None
        except DataError as e:
            row = self.installed().get(pack_id)
            if not row:
                raise
            records = [Record(r["kind"], r["rkey"], r["scope"], json.loads(r["value_json"]), r["source"])
                       for r in db.rows("SELECT * FROM pack_records WHERE pack_id = ? ORDER BY pos", (pack_id,))]
            info, problem = {"id": pack_id, "name": row["name"], "version": row["version"]}, str(e)
        cfg = PackConfig.current()
        hidden = {(r.kind, r.key) for r in cfg.hidden()}
        row = self.installed().get(pack_id)
        active = bool(row and row["status"] == "installed")
        out = []
        for r in records:
            eff = cfg.get(r.kind, r.key)
            if not active:
                status = "not_installed"
            elif (r.kind, r.key) in hidden:
                status = "hidden"
            elif eff is None:
                status = "not_installed"
            elif eff.origin == "changed":
                status = "conflict" if eff.conflict else "changed"
            elif eff.pack_id != pack_id:
                status = "other_pack"
            else:
                status = "pack"
            out.append({"kind": r.kind, "key": r.key, "scope": r.scope, "label": r.label(), "status": status,
                        "value": r.value, "effective": eff.value if eff else None,
                        "provided_by": eff.pack_id if eff else None})
        return {**info, **self._state(row, info.get("version") if problem is None else None), "problem": problem,
                "records": out, "kinds": KINDS,
                "history": db.rows("SELECT * FROM pack_history WHERE pack_id = ? ORDER BY id DESC LIMIT 20", (pack_id,))}

    # -- install / update / remove
    def plan(self, pack_id: str) -> dict:
        return self._plan(self.loader.load(pack_id))

    def _plan(self, pack: Pack) -> dict:
        pack_id = pack.id
        row = self.installed().get(pack_id)
        old = {}
        if row:
            old = {(r["kind"], r["rkey"]): canonical(json.loads(r["value_json"])) for r in
                   db.rows("SELECT kind, rkey, value_json FROM pack_records WHERE pack_id = ?", (pack_id,))}
        new = {(r.kind, r.key): canonical(r.value) for r in pack.records}
        labels = {(r.kind, r.key): r.label() for r in pack.records}
        if not row:
            action = "install"
        elif row["status"] != "installed":
            action = "reinstall"
        elif version_tuple(pack.version) < version_tuple(row["version"]):
            action = "downgrade"
        elif version_tuple(pack.version) == version_tuple(row["version"]) and old == new:
            action = "none"
        else:
            action = "update"
        added = [k for k in new if k not in old]
        removed = [k for k in old if k not in new]
        changed = [k for k in new if k in old and old[k] != new[k]]
        local = {(r["kind"], r["rkey"]) for r in db.rows("SELECT kind, rkey FROM pack_local")}
        kept = [k for k in changed + removed if k in local]

        def show(keys):
            return [{"kind": k[0], "key": k[1], "label": labels.get(k, k[1])} for k in keys[:200]]
        return {"pack": pack.info(), "action": action, "from_version": row["version"] if row else None,
                "to_version": pack.version, "added": show(added), "changed": show(changed), "removed": show(removed),
                "kept_local": show(kept), "counts": {"added": len(added), "changed": len(changed),
                                                     "removed": len(removed), "kept_local": len(kept)}}

    def install(self, pack_id: str, user_id: int | None) -> dict:
        """Install, update or re-install a pack from its files. The company's changes are kept."""
        pack = self.loader.load(pack_id)
        plan = self._plan(pack)
        if plan["action"] == "downgrade":
            raise DataError(f"The pack on the server ({plan['to_version']}) is older than the installed one "
                            f"({plan['from_version']}). Nothing was changed.")
        if plan["action"] == "none":
            return {**plan, "message": f"{plan['pack']['name']} {plan['to_version']} is already installed."}
        now = db.now()
        with db.tx():
            row = db.one("SELECT * FROM packs WHERE id = ?", (pack.id,))
            if row and row["status"] == "installed":
                db.execute("UPDATE packs SET name = ?, version = ?, licence = ?, description = ?, updated_at = ? "
                           "WHERE id = ?", (pack.name, pack.version, pack.licence, pack.description, now, pack.id))
            else:
                seq = (db.scalar("SELECT MAX(seq) FROM packs") or 0) + 1
                if row:
                    db.execute("UPDATE packs SET name = ?, version = ?, licence = ?, description = ?, status = "
                               "'installed', seq = ?, installed_by = ?, installed_at = ?, updated_at = ? WHERE id = ?",
                               (pack.name, pack.version, pack.licence, pack.description, seq, user_id, now, now, pack.id))
                else:
                    db.insert("packs", {"id": pack.id, "name": pack.name, "version": pack.version,
                                        "licence": pack.licence, "description": pack.description, "status": "installed",
                                        "seq": seq, "installed_by": user_id, "installed_at": now, "updated_at": now})
            db.execute("DELETE FROM pack_records WHERE pack_id = ?", (pack.id,))
            for pos, r in enumerate(pack.records):
                db.execute("INSERT INTO pack_records (pack_id, kind, rkey, scope, value_json, source, pos) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)", (pack.id, r.kind, r.key, r.scope, stored(r.value),
                                                            r.source, pos))
            db.insert("pack_history", {"pack_id": pack.id, "action": plan["action"], "from_version": plan["from_version"],
                                       "to_version": pack.version, "report_json": json.dumps(plan["counts"]),
                                       "user_id": user_id, "at": now})
            db.audit(user_id, f"pack_{plan['action']}", "pack", None,
                     f"{pack.id} {plan['from_version'] or ''}->{pack.version}".replace(" ->", " "))
            _bump_generation()
        verb = {"install": "installed", "reinstall": "installed again", "update": "updated"}[plan["action"]]
        msg = f"{pack.name} {pack.version} {verb}."
        if plan["counts"]["kept_local"]:
            msg += f" {plan['counts']['kept_local']} of your own changes were kept."
        return {**plan, "message": msg}

    def remove(self, pack_id: str, user_id: int | None) -> dict:
        row = self.installed().get(pack_id)
        if not row or row["status"] != "installed":
            raise DataError(f"The pack '{pack_id}' is not installed, so there is nothing to remove.")
        keys = {(r["kind"], r["rkey"]) for r in db.rows("SELECT kind, rkey FROM pack_records WHERE pack_id = ?",
                                                         (pack_id,))}
        local = sum(1 for r in db.rows("SELECT kind, rkey FROM pack_local") if (r["kind"], r["rkey"]) in keys)
        schemes = [f"spec:{k}" for (kind, k) in keys if kind == "spec_template"] + \
                  [f"library:{k}" for (kind, k) in keys if kind == "library_table"] + \
                  [k for (kind, k) in keys if kind == "numbering_scheme"]
        items = 0
        if schemes:
            marks = ", ".join("?" for _ in schemes)
            items = db.scalar(f"SELECT COUNT(DISTINCT item_id) FROM number_allocations WHERE scheme IN ({marks}) "
                              "AND item_id IS NOT NULL", schemes) or 0
        now = db.now()
        with db.tx():
            db.execute("UPDATE packs SET status = 'removed', updated_at = ? WHERE id = ?", (now, pack_id))
            report = {"records": len(keys), "kept_local": local, "items_kept": items}
            db.insert("pack_history", {"pack_id": pack_id, "action": "remove", "from_version": row["version"],
                                       "to_version": None, "report_json": json.dumps(report), "user_id": user_id,
                                       "at": now})
            db.audit(user_id, "pack_remove", "pack", None, f"{pack_id} {row['version']}")
            _bump_generation()
        msg = f"{row['name']} removed. Its {len(keys)} records are switched off."
        if items:
            msg += f" The {items} items made from it stay, with their part numbers."
        if local:
            msg += f" Your {local} own changes stay."
        return {"pack": {"id": pack_id, "name": row["name"]}, **report, "message": msg}

    # -- the company's own changes
    @staticmethod
    def scope_of(kind: str, key: str, value: dict) -> str:
        if kind in ("dictionary", "library_row"):
            return key.split("/", 1)[0]
        if kind in ("spec_template", "library_table"):
            return str(value.get("category", ""))
        return ""

    def check_record(self, kind: str, key: str, value: Any, cfg: PackConfig | None = None) -> dict:
        """Check a company change or addition; returns the value to store. Raises DataError in plain words."""
        cfg = cfg or PackConfig.current()
        where = "Your change"
        if kind not in KINDS:
            raise DataError(f"Unknown kind of record '{kind}'.")
        if not isinstance(value, dict):
            raise DataError(f"{where}: the value must be a mapping.")
        if not isinstance(key, str) or not key.strip():
            raise DataError(f"{where}: the key is missing.")
        L = PackLoader
        if kind == "category":
            k, v = L._category(value, where)
        elif kind == "item_type":
            k, v = L._item_type(value, where)
        elif kind == "unit":
            k, v = L._unit(value, where)
        elif kind == "field":
            k, v = L._field(value, where)
        elif kind == "title_block":
            k, v = L._title_block(value, where)
        elif kind == "dictionary":
            name = key.split("/", 1)[0]
            if not NAME_OK.match(name) or name == CATEGORIES_DICTIONARY:
                raise DataError(f"{where}: '{name}' is not a dictionary name.")
            ch = choice_from(value, where)
            k, v = f"{name}/{ch.code}", {"code": ch.code, "name": ch.name, **ch.extra}
        elif kind == "spec_template":
            t = SpecTemplate(value, where, cfg.dictionaries_with_entries(), {c["code"] for c in cfg.categories()})
            k, v = t.id, value
        elif kind == "numbering_scheme":
            s = SequenceScheme(value, where, cfg.dictionaries_with_entries())
            k, v = s.id, value
        elif kind == "library_table":
            t = LibraryTable(value, where, {c["code"] for c in cfg.categories()})
            k, v = t.id, value
        else:  # library_row
            table_id = key.split("/", 1)[0]
            tv = cfg.value("library_table", table_id)
            if not tv:
                raise DataError(f"{where}: there is no library table '{table_id}'.")
            row = LibraryTable(tv, f"table '{table_id}'").row_from_value(value, where)
            k, v = f"{table_id}/{row.key}", row.to_value()
        if k != key:
            raise DataError(f"{where}: the code '{k}' does not match the record being changed ('{key}'). "
                            "To use a different code, add a new record instead.")
        return v

    def set_local(self, kind: str, key: str, value: Any, user_id: int | None) -> dict:
        cfg = PackConfig.current()
        v = self.check_record(kind, key, value, cfg)
        eff = cfg.get(kind, key)
        hidden = {(r.kind, r.key): r for r in cfg.hidden()}
        base = eff.pack_value if eff and eff.pack_value is not None else \
            (hidden[(kind, key)].pack_value if (kind, key) in hidden else None)
        with db.tx():
            old = db.one("SELECT value_json FROM pack_local WHERE kind = ? AND rkey = ?", (kind, key))
            db.execute("DELETE FROM pack_local WHERE kind = ? AND rkey = ?", (kind, key))
            db.insert("pack_local", {"kind": kind, "rkey": key, "scope": self.scope_of(kind, key, v),
                                     "value_json": stored(v), "hidden": 0,
                                     "base_json": stored(base) if base is not None else None,
                                     "updated_by": user_id, "updated_at": db.now()})
            db.audit(user_id, "pack_record_change" if base is not None else "pack_record_add", "pack_record", None,
                     f"{kind} {key}: {stored(v)[:400]}" + (f" (was {old['value_json'][:200]})" if old else ""))
            _bump_generation()
        return PackConfig.current().get(kind, key).to_dict()

    def hide(self, kind: str, key: str, user_id: int | None) -> None:
        cfg = PackConfig.current()
        eff = cfg.get(kind, key)
        if not eff or eff.pack_value is None:
            raise DataError("Only records that come from a pack can be switched off. Remove your own record instead.")
        with db.tx():
            db.execute("DELETE FROM pack_local WHERE kind = ? AND rkey = ?", (kind, key))
            db.insert("pack_local", {"kind": kind, "rkey": key, "scope": eff.scope, "value_json": None, "hidden": 1,
                                     "base_json": stored(eff.pack_value), "updated_by": user_id,
                                     "updated_at": db.now()})
            db.audit(user_id, "pack_record_hide", "pack_record", None, f"{kind} {key}")
            _bump_generation()

    def revert(self, kind: str, key: str, user_id: int | None) -> dict:
        """Undo the company's change (back to the pack's version) or remove the company's own record."""
        old = db.one("SELECT * FROM pack_local WHERE kind = ? AND rkey = ?", (kind, key))
        if not old:
            raise DataError(f"There is no change of yours to undo for {key}.")
        with db.tx():
            db.execute("DELETE FROM pack_local WHERE kind = ? AND rkey = ?", (kind, key))
            db.audit(user_id, "pack_record_revert", "pack_record", None,
                     f"{kind} {key}: removed {('hidden' if old['hidden'] else old['value_json'] or '')[:400]}")
            _bump_generation()
        eff = PackConfig.current().get(kind, key)
        return {"record": eff.to_dict() if eff else None}

    def keep_mine(self, kind: str, key: str, user_id: int | None) -> None:
        """The pack changed a record the company had changed: keep the company's version and stop asking."""
        eff = PackConfig.current().get(kind, key)
        if not eff or eff.origin != "changed":
            raise DataError(f"{key} has no change of yours that differs from the pack.")
        with db.tx():
            db.execute("UPDATE pack_local SET base_json = ?, updated_by = ?, updated_at = ? WHERE kind = ? AND rkey = ?",
                       (stored(eff.pack_value), user_id, db.now(), kind, key))
            db.audit(user_id, "pack_record_keep", "pack_record", None, f"{kind} {key}")
            _bump_generation()

    def local_changes(self) -> list[dict]:
        cfg = PackConfig.current()
        out = []
        for loc in db.rows("SELECT * FROM pack_local ORDER BY updated_at DESC"):
            eff = cfg.get(loc["kind"], loc["rkey"])
            hidden = bool(loc["hidden"])
            hrec = next((h for h in cfg.hidden() if (h.kind, h.key) == (loc["kind"], loc["rkey"])), None)
            out.append({"kind": loc["kind"], "key": loc["rkey"], "scope": loc["scope"], "hidden": hidden,
                        "value": json.loads(loc["value_json"]) if loc["value_json"] else None,
                        "origin": "hidden" if hidden else (eff.origin if eff else "local"),
                        "pack_value": (eff.pack_value if eff else (hrec.pack_value if hrec else None)),
                        "conflict": bool(eff.conflict if eff else (hrec.conflict if hrec else False)),
                        "updated_at": loc["updated_at"]})
        return out


def install_for_demo(pack_ids: list[str], user_id: int | None = None) -> None:
    """Used by the demo seed: install packs, logging (not raising) problems so the demo still loads."""
    m = PackManager()
    for pid in pack_ids:
        try:
            m.install(pid, user_id)
        except DataError as e:
            log.warning("Pack %s was not installed: %s", pid, e)
