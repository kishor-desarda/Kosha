# SPDX-License-Identifier: AGPL-3.0-or-later
"""The measured change list: what differs between two versions of a file, as facts.

A ``Comparer`` knows one kind of content (DXF drawings, meshes, BOMs, text documents, item
properties) and turns two ``Side`` objects into ``Change`` objects. The ``DiffEngine`` keeps
comparers registered by file type, runs the ones that apply, and never lets one failing
comparer hide the others. Every change has a plain-English one-line rendering (``render``),
so engineers do not type these facts and nothing downstream (change-log drafts, later AI)
has to invent them.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("kosha.diff")

SEVERITIES = ("major", "minor", "info")
MAX_CHANGES = 300


# ------------------------------------------------------------------ change --

@dataclass
class Change:
    """One measured difference. ``zone`` is a bounding box in drawing units (min x, min y, max x, max y)."""
    kind: str
    where: str
    before: Any
    after: Any
    severity_hint: str = "minor"
    zone: tuple[float, float, float, float] | None = None
    detail: dict = field(default_factory=dict)
    source: str = ""

    @property
    def text(self) -> str:
        return render(self)

    @property
    def fact(self) -> tuple | None:
        """Identity of a property-like fact, used to drop the same fact reported by two comparers."""
        if self.kind in ("attribute", "property", "custom_property"):
            label = _canon_label(self.detail.get("label") or self.where)
            return (label, _canon_value(self.before), _canon_value(self.after))
        return None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "where": self.where, "before": _jsonable(self.before), "after": _jsonable(self.after),
            "severity": self.severity_hint, "text": self.text,
            "zone": [round(v, 3) for v in self.zone] if self.zone else None,
            "detail": {k: _jsonable(v) for k, v in self.detail.items()}, "source": self.source,
        }


def _jsonable(v):
    if isinstance(v, float):
        return round(v, 6) if math.isfinite(v) else None
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return v


_LABEL_SYNONYMS = {"mass": "weight", "weight_kg": "weight", "wt": "weight", "partno": "part number",
                   "part_number": "part number", "part no": "part number", "desc": "description", "matl": "material"}


def _canon_label(s: str) -> str:
    s = str(s or "").strip().lower().replace("_", " ")
    return _LABEL_SYNONYMS.get(s, _LABEL_SYNONYMS.get(s.replace(" ", "_"), s))


def _canon_value(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    try:
        f = float(s.replace(",", "."))
        return fmt(f)
    except ValueError:
        return s.upper()


# ------------------------------------------------------------------ format --

def fmt(v, nd: int = 3) -> str:
    """Number for screen text: no trailing zeros, no '-0'."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    s = f"{float(v):.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def fmt_big(v: float, nd: int = 1) -> str:
    """Large quantities with thousands separators: 3,456.2."""
    s = f"{float(v):,.{nd}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def pct(before: float, after: float) -> str:
    if not before:
        return ""
    p = (after - before) / abs(before) * 100
    return f"{'+' if p >= 0 else '−'}{abs(p):.1f} %"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _q(v) -> str:
    s = str(v if v is not None else "").replace("\n", " ").strip()
    return "“" + (s if len(s) <= 80 else s[:77] + "…") + "”"


# ------------------------------------------------------------------ render --

_KIND_WORD = {"line": ("line", "lines"), "poly": ("polyline", "polylines"), "circle": ("circle", "circles"),
              "arc": ("arc", "arcs")}


def render(c: Change) -> str:
    """Plain-English one-line rendering of a change."""
    d = c.detail
    b, a = c.before, c.after
    k = c.kind
    if k in ("attribute", "property", "custom_property"):
        label = d.get("label") or c.where
        if k == "custom_property":
            label = f"Custom property {label}"
        unit = f" {d['unit']}" if d.get("unit") else ""
        if b in (None, ""):
            return f"{label} set to {a}{unit}"
        if a in (None, ""):
            return f"{label} removed (was {b}{unit})"
        return f"{label} changed from {b}{unit} to {a}{unit}"
    if k == "dimension":
        noun = d.get("noun") or "Dimension"
        if b is None:
            return f"Dimension {a} added"
        if a is None:
            return f"Dimension {b} removed"
        return f"{noun} {b} changed to {a}"
    if k == "note":
        if b is None:
            return f"Note {_q(a)} added"
        if a is None:
            return f"Note {_q(b)} removed"
        return f"Note {_q(b)} changed to {_q(a)}"
    if k == "entities":
        one, many = _KIND_WORD.get(d.get("entity"), ("entity", "entities"))
        n = int(d.get("count") or 0)
        return f"{n} {one if n == 1 else many} {d.get('action', 'changed')} on layer {d.get('layer', '0')}"
    if k == "holes":
        layer = d.get("layer", "0")
        action = d.get("action")
        if action == "resized":
            n = int(d.get("count") or 0)
            return f"{plural(n, 'hole')} changed from Ø{fmt(d.get('from_d'))} to Ø{fmt(d.get('to_d'))} on layer {layer}"
        n = int(d.get("count") or 0)
        dia = f"Ø{fmt(d.get('d'))}"
        if action == "moved":
            return f"{plural(n, 'hole')} {dia} moved on layer {layer}"
        extra = f"; {d['moved']} repositioned" if d.get("moved") else ""
        return f"{plural(n, 'hole')} {dia} {action} on layer {layer} ({b} → {a}{extra})"
    if k == "extents":
        return f"Overall size changed from {b} to {a} {d.get('units', '')}".rstrip()
    if k == "model_size":
        return f"Model size changed from {b} to {a} mm"
    if k in ("volume", "area", "mass"):
        unit = d.get("unit", "")
        word = {"volume": "Volume", "area": "Surface area", "mass": "Mass"}[k]
        change = pct(_num(b), _num(a)) if _num(b) is not None and _num(a) is not None else ""

        def amount(v):
            n = _num(v)
            return (fmt_big(n) if n is not None else str(v)) + (f" {unit}" if unit else "")
        return f"{word} changed from {amount(b)} to {amount(a)}" + (f" ({change})" if change else "")
    if k == "component":
        if b is None:
            return f"Component {a} added" + (f" (qty {fmt(d['qty'])})" if d.get("qty") is not None else "")
        if a is None:
            return f"Component {b} removed"
        return f"Component {b} changed to {a}"
    if k == "bom_added":
        return f"{_part(d, a)} added to the BOM (qty {fmt(d.get('qty'))})"
    if k == "bom_removed":
        return f"{_part(d, b)} removed from the BOM (was qty {fmt(d.get('qty'))})"
    if k == "bom_qty":
        return f"Quantity of {_part(d, d.get('part_number'))} changed from {fmt(b)} to {fmt(a)}"
    if k == "bom_swap":
        fn = f" at find no. {d['find_number']}" if d.get("find_number") else ""
        q = f" (qty {fmt(d['from_qty'])} → {fmt(d['to_qty'])})" if d.get("from_qty") != d.get("to_qty") else ""
        return f"{b} replaced by {a}{fn}{q}"
    if k == "bom_uom":
        return f"Unit of {d.get('part_number')} changed from {b} to {a}"
    if k == "bom_find":
        return f"{d.get('part_number')} moved from find no. {b} to {a}"
    if k == "text_summary":
        unit = d.get("unit", "line")
        parts = [f"{plural(int(d[x]), unit)} {w}" for x, w in (("changed", "changed"), ("added", "added"),
                                                                ("removed", "removed")) if d.get(x)]
        return (", ".join(parts) or "No text changes") + (f" ({d['scope']})" if d.get("scope") else "")
    if k == "text_line":
        unit = (d.get("unit") or "line").capitalize()
        pos = f"{unit} {d.get('index')}" + (f" on page {d['page']}" if d.get("page") else "")
        if b is None:
            return f"{pos} added: {_q(a)}"
        if a is None:
            return f"{pos} removed: {_q(b)}"
        return f"{pos} changed: {_q(b)} → {_q(a)}"
    if k == "cell":
        if b in (None, ""):
            return f"Cell {c.where} set to {a}"
        if a in (None, ""):
            return f"Cell {c.where} cleared (was {b})"
        return f"Cell {c.where} changed from {b} to {a}"
    if k == "lifecycle":
        return str(d.get("text") or f"{c.where}: {b} → {a}")
    if b is None:
        return f"{c.where}: {a} added"
    if a is None:
        return f"{c.where}: {b} removed"
    return f"{c.where}: {b} → {a}"


def _part(d: dict, pn) -> str:
    desc = (d.get("description") or "").strip()
    return f"{pn} {desc.title() if desc.isupper() else desc}".strip()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ sides --

@dataclass
class Side:
    """One version of a file, with whatever Kosha knows about it. Content is loaded only when needed."""
    filename: str = ""
    ext: str = ""
    revision: str = ""
    version: int | None = None
    document_id: int | None = None
    blob_sha: str | None = None
    meta: dict = field(default_factory=dict)
    props: dict | None = None            # item properties as of this side (description, material ...)
    bom_lines: list[dict] | None = None  # BOM of the item at this revision
    bom_id: int | None = None
    loader: Callable[[], bytes] | None = None
    _data: bytes | None = None

    @classmethod
    def of_bytes(cls, filename: str, data: bytes, **kw) -> "Side":
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return cls(filename=filename, ext=ext, _data=data, **kw)

    def data(self) -> bytes:
        if self._data is None:
            if self.loader is None:
                raise ValueError("no content for this version")
            self._data = self.loader()
        return self._data

    @property
    def has_content(self) -> bool:
        return self._data is not None or self.loader is not None

    @property
    def label(self) -> str:
        if self.version is not None:
            return f"rev {self.revision} (v{self.version})" if self.revision else f"v{self.version}"
        return f"rev {self.revision}" if self.revision else "this version"


# --------------------------------------------------------------- comparer --

class Comparer:
    """Base class: one kind of content. Subclasses set ``name``, ``label`` and ``exts`` and implement ``compare``.

    ``exts = ("*",)`` makes a comparer apply to every file type (properties, BOMs)."""
    name = "base"
    label = "Comparison"
    exts: tuple[str, ...] = ()
    version = 1

    def applies(self, a: Side, b: Side) -> bool:
        return True

    def compare(self, a: Side, b: Side) -> list[Change]:  # pragma: no cover - interface
        raise NotImplementedError

    def notes(self) -> list[str]:
        """Plain-language limits noticed during the last ``compare`` (for example: no DWG converter)."""
        return list(getattr(self, "_notes", []))

    def note(self, text: str) -> None:
        if not hasattr(self, "_notes"):
            self._notes = []
        self._notes.append(text)


@dataclass
class Diff:
    changes: list[Change]
    notes: list[str]
    comparers: list[str]

    def to_dict(self) -> dict:
        counts = {s: sum(1 for c in self.changes if c.severity_hint == s) for s in SEVERITIES}
        return {"changes": [c.to_dict() for c in self.changes], "notes": self.notes, "comparers": self.comparers,
                "counts": counts, "total": len(self.changes)}


class DiffEngine:
    """Comparers registered by file type. ``compare`` runs every comparer that applies, guarded."""

    def __init__(self):
        self._by_ext: dict[str, list[type[Comparer]]] = {}
        self._always: list[type[Comparer]] = []

    def register(self, comparer: type[Comparer], exts: tuple[str, ...] | None = None) -> type[Comparer]:
        for e in (exts or comparer.exts):
            e = e.lower().lstrip(".")
            if e == "*":
                if comparer not in self._always:
                    self._always.append(comparer)
            else:
                self._by_ext.setdefault(e, [])
                if comparer not in self._by_ext[e]:
                    self._by_ext[e].append(comparer)
        return comparer

    def comparers_for(self, ext: str) -> list[type[Comparer]]:
        return list(self._by_ext.get((ext or "").lower(), [])) + list(self._always)

    @property
    def file_types(self) -> list[str]:
        return sorted(self._by_ext)

    def compare(self, a: Side, b: Side) -> Diff:
        changes: list[Change] = []
        notes: list[str] = []
        used: list[str] = []
        for cls in self.comparers_for(b.ext or a.ext):
            comp = cls()
            try:
                if not comp.applies(a, b):
                    continue
                found = comp.compare(a, b)
            except Exception as e:  # one comparer must never hide the others
                log.exception("Comparer %s failed on %s", cls.name, b.filename)
                notes.append(f"{cls.label}: Kosha could not compare these versions ({e.__class__.__name__}). "
                             "The other differences are still listed.")
                continue
            used.append(cls.name)
            for c in found:
                c.source = c.source or cls.name
            changes.extend(found)
            notes.extend(comp.notes())
        changes = _dedupe(changes)
        order = {s: i for i, s in enumerate(SEVERITIES)}
        changes.sort(key=lambda c: order.get(c.severity_hint, 9))  # stable: comparer order kept within a severity
        if len(changes) > MAX_CHANGES:
            notes.append(f"{len(changes) - MAX_CHANGES} more differences are not listed; the first {MAX_CHANGES} are shown.")
            changes = changes[:MAX_CHANGES]
        return Diff(changes, list(dict.fromkeys(notes)), used)


def _dedupe(changes: list[Change]) -> list[Change]:
    seen: set = set()
    out = []
    for c in changes:
        f = c.fact
        if f is not None:
            if f in seen:
                continue
            seen.add(f)
        out.append(c)
    return out


def union(boxes) -> tuple[float, float, float, float] | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def pad(box, amount: float):
    if not box:
        return None
    return (box[0] - amount, box[1] - amount, box[2] + amount, box[3] + amount)
