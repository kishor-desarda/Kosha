# SPDX-License-Identifier: AGPL-3.0-or-later
"""DXF and DWG drawings: title block, dimensions, notes, entities by layer, holes, overall size.

Both versions are read with Kosha's own DXF reader (cad/dxf.py). DWG files go through the DWG
converter configured on the server; without one, only the title block stored at check-in is compared.
Every change carries the zone (bounding box in drawing units) where it happened, so the viewer can
zoom to it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..cad import dwg, dxf
from .core import Change, Comparer, Side, fmt, pad, union

DIM_LAYER = re.compile(r"DIM", re.I)
_NUM = r"\d+(?:[.,]\d+)?"
MEASUREMENT = re.compile(
    rf"^(?:\d+\s*[xX×]\s*)?(?:[Ø⌀]|SR|R|M)?\s*{_NUM}(?:\s*[xX×]\s*{_NUM})?(?:\s*[A-Za-z]{{1,2}}\d{{1,2}})?"
    rf"(?:\s*(?:±|\+|-|−)\s*{_NUM}(?:\s*/\s*[-+−]?\s*{_NUM})?)?\s*°?$")
ATTR_LABEL = {"part_number": "Part number", "description": "Description", "material": "Material",
              "revision": "Revision", "weight": "Weight", "scale": "Scale", "drawn_by": "Drawn by"}
ATTR_SEVERITY = {"part_number": "major", "material": "major", "description": "minor", "weight": "minor",
                 "scale": "minor", "revision": "info", "drawn_by": "info"}
_TAG_KEY = {t: k for k, tags in dxf.TAG_MAP.items() for t in tags}
_INFO_TAG = re.compile(r"DATE|DRAWN|CHECK|APPR|DRN|CHK|SHEET", re.I)
MAX_PAIRS = 250_000


# ---------------------------------------------------------------- reading --

class _TaggingCollector(dxf.Collector):
    """The normal collector, plus for every primitive: which top-level entity it came from and its own type."""

    def __init__(self, doc):
        super().__init__(doc)
        self.tags: list[tuple[int, str, str]] = []   # (top index, own type, top-level type)
        self._stack: list[str] = []
        self._top = -1
        self.top_entities: list[dxf.Entity] = []

    def run(self, include_paper=False):
        ents = [e for e in self.doc.entities if include_paper or e.i(67) != 1]
        if not include_paper and not ents:
            ents = self.doc.entities
        self.top_entities = ents
        for idx, e in enumerate(ents):
            self._top = idx
            self.walk([e], dxf.IDENTITY, 7, 0, None, "0")
        for e in self.doc.entities:
            if e.type == "INSERT":
                for a in e.children:
                    tag = (a.get(2) or "").strip().upper()
                    val = dxf.clean_text(a.get(1) or "")
                    if tag and val and tag not in self.attribs:
                        self.attribs[tag] = val
        return self

    def entity(self, e, T, color, layer, cat, depth):
        self._stack.append(e.type)
        try:
            super().entity(e, T, color, layer, cat, depth)
        finally:
            self._stack.pop()

    def add(self, kind, data, color, layer, cat):
        n = len(self.prims)
        super().add(kind, data, color, layer, cat)
        if len(self.prims) > n:
            self.tags.append((self._top, self._stack[-1] if self._stack else "", self._stack[0] if self._stack else ""))


@dataclass
class _Text:
    text: str
    x: float
    y: float
    box: tuple
    layer: str
    dim: bool


@dataclass
class _Drawing:
    col: _TaggingCollector
    units: str
    attrs: dict          # tag -> (value, zone)
    texts: list          # _Text
    geom: list           # (prim, layer)
    extents: tuple | None
    diag: float


def read(data: bytes) -> _Drawing:
    doc = dxf.parse(data)
    col = _TaggingCollector(doc).run()
    attrs: dict = {}
    for e in doc.entities:
        if e.type != "INSERT":
            continue
        for a in e.children:
            tag = (a.get(2) or "").strip().upper()
            if not tag or tag in attrs:
                continue
            val = dxf.clean_text(a.get(1) or "")
            x, y, hh = a.f(10), a.f(20), a.f(40, 2.5)
            attrs[tag] = (val, (x, y, x + 0.6 * hh * max(len(val), 1), y + hh))
    texts: list[_Text] = []
    dim_groups: dict[int, list] = {}
    geom = []
    for p, (top, own, top_type) in zip(col.prims, col.tags):
        if p.kind == "text":
            if own == "ATTRIB":
                continue   # title block values are compared as attributes
            if top_type == "DIMENSION":
                dim_groups.setdefault(top, []).append(p)
                continue
            s = p.data[4].strip()
            box = _bounds([p])
            dim = bool(DIM_LAYER.search(p.layer)) or bool(MEASUREMENT.match(s))
            texts.append(_Text(s, p.data[0], p.data[1], box, p.layer, dim))
            continue
        if top_type in ("DIMENSION", "LEADER", "MULTILEADER") or p.cat == "dim" or DIM_LAYER.search(p.layer):
            continue
        geom.append(p)
    # DIMENSION entities: the text drawn in their block, or the measured value when there is no block
    for idx, e in enumerate(col.top_entities):
        if e.type != "DIMENSION":
            continue
        ps = dim_groups.get(idx)
        if ps:
            ps = sorted(ps, key=lambda q: (-round(q.data[1], 1), q.data[0]))
            s = " ".join(q.data[4].strip() for q in ps if q.data[4].strip())
            box = _bounds(ps)
            x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        else:
            s = _dimension_text(e)
            x, y = (e.f(11), e.f(21)) if e.get(11) is not None else (e.f(10), e.f(20))
            hh = 2.5
            box = (x - 0.3 * hh * len(s), y - hh / 2, x + 0.3 * hh * len(s), y + hh / 2)
        if s:
            texts.append(_Text(s, x, y, box, e.layer, True))
    allb = dxf.bounds(col.prims)
    diag = math.hypot(allb[2] - allb[0], allb[3] - allb[1]) if allb else 1.0
    body = [p for p in geom if p.cat == "geom"]
    body = dxf._drop_frames(body)
    return _Drawing(col, doc.units, attrs, texts, geom, dxf.bounds(body), diag or 1.0)


def _dimension_text(e) -> str:
    raw = dxf.clean_text(e.get(1) or "")
    kind = e.i(70) & 7
    m = e.get(42)
    meas = ""
    if m is not None:
        try:
            v = float(m)
            meas = fmt(math.degrees(v) if kind in (2, 5) and v < 2 * math.pi + 1e-9 else v, 2)
            meas = {3: "Ø", 4: "R"}.get(kind, "") + meas + ("°" if kind in (2, 5) else "")
        except ValueError:
            meas = ""
    if not raw or raw == "<>":
        return meas
    if "<>" in raw:
        return raw.replace("<>", meas)
    return raw


def _bounds(prims) -> tuple | None:
    return dxf.bounds(prims)


# --------------------------------------------------------------- compare --

class DrawingComparer(Comparer):
    name = "drawing"
    label = "Drawing comparison"
    exts = ("dxf",)

    def load(self, side: Side) -> bytes | None:
        return side.data()

    def applies(self, a: Side, b: Side) -> bool:
        return a.has_content and b.has_content

    def compare(self, a: Side, b: Side) -> list[Change]:
        da_bytes, db_bytes = self.load(a), self.load(b)
        if da_bytes is None or db_bytes is None:
            return self.metadata_only(a, b)
        if da_bytes == db_bytes:
            return []
        old, new = read(da_bytes), read(db_bytes)
        if old.col.truncated or new.col.truncated:
            self.note("Very large drawing: only the first 400,000 entities were compared.")
        diag = max(old.diag, new.diag)
        changes: list[Change] = []
        changes += self.attributes(old.attrs, new.attrs, diag)
        changes += self.extents(old, new)
        changes += self.texts(old, new, diag)
        hole_ids_old, hole_ids_new, hole_changes = self.holes(old, new, diag)
        changes += hole_changes
        changes += self.entities(old, new, hole_ids_old, hole_ids_new, diag)
        return changes

    # ----------------------------------------------------- title block
    def attributes(self, old: dict, new: dict, diag: float) -> list[Change]:
        out = []
        for tag in list(dict.fromkeys(list(old) + list(new))):
            va = old.get(tag, ("", None))
            vb = new.get(tag, ("", None))
            if va[0].strip() == vb[0].strip():
                continue
            key = _TAG_KEY.get(tag)
            label = ATTR_LABEL.get(key) or tag.replace("_", " ").capitalize()
            sev = ATTR_SEVERITY.get(key) or ("info" if _INFO_TAG.search(tag) else "minor")
            out.append(Change("attribute", "Title block", va[0] or None, vb[0] or None, sev,
                              pad(union([va[1], vb[1]]), diag * 0.01), {"label": label, "tag": tag}))
        return out

    def metadata_only(self, a: Side, b: Side) -> list[Change]:
        ta = (a.meta or {}).get("attributes") or {}
        tb = (b.meta or {}).get("attributes") or {}
        return self.attributes({k: (v, None) for k, v in ta.items()}, {k: (v, None) for k, v in tb.items()}, 1.0)

    # ----------------------------------------------------- overall size
    def extents(self, old: _Drawing, new: _Drawing) -> list[Change]:
        if not old.extents or not new.extents:
            return []
        wa, ha = old.extents[2] - old.extents[0], old.extents[3] - old.extents[1]
        wb, hb = new.extents[2] - new.extents[0], new.extents[3] - new.extents[1]
        tol = max(wa, ha, wb, hb) * 1e-4
        if abs(wa - wb) <= tol and abs(ha - hb) <= tol:
            return []
        return [Change("extents", "Drawing", f"{fmt(wa, 2)} × {fmt(ha, 2)}", f"{fmt(wb, 2)} × {fmt(hb, 2)}", "major",
                       union([old.extents, new.extents]), {"units": new.units, "from": [wa, ha], "to": [wb, hb]})]

    # ----------------------------------------------------- dimensions and notes
    def texts(self, old: _Drawing, new: _Drawing, diag: float) -> list[Change]:
        out = []
        for dim in (True, False):
            ta = [t for t in old.texts if t.dim == dim]
            tb = [t for t in new.texts if t.dim == dim]
            changed, removed, added = match_texts(ta, tb, diag * 0.08)
            kind = "dimension" if dim else "note"
            sev = "major" if dim else "minor"
            for x, y in changed:
                out.append(Change(kind, f"Layer {y.layer}", x.text, y.text, sev, pad(union([x.box, y.box]), diag * 0.01),
                                  {"noun": noun_for(y.text) if dim else "Note", "layer": y.layer}))
            for x in removed:
                out.append(Change(kind, f"Layer {x.layer}", x.text, None, sev, pad(x.box, diag * 0.01), {"layer": x.layer}))
            for y in added:
                out.append(Change(kind, f"Layer {y.layer}", None, y.text, sev, pad(y.box, diag * 0.01), {"layer": y.layer}))
        return out

    # ----------------------------------------------------- holes
    def holes(self, old: _Drawing, new: _Drawing, diag: float):
        def candidates(d: _Drawing):
            ext = d.extents
            size = max(ext[2] - ext[0], ext[3] - ext[1]) if ext else 0
            out = []
            for i, p in enumerate(d.geom):
                if p.kind == "circle" and p.cat == "geom" and size and 2 * p.data[2] <= 0.5 * size + 1e-9:
                    out.append((i, p))
            return out

        ca, cb = candidates(old), candidates(new)
        used_a: set = set()
        used_b: set = set()
        # identical holes
        index: dict = {}
        for i, p in cb:
            index.setdefault((p.layer, _r(p.data[0]), _r(p.data[1]), _r(p.data[2])), []).append(i)
        for i, p in ca:
            k = (p.layer, _r(p.data[0]), _r(p.data[1]), _r(p.data[2]))
            if index.get(k):
                used_a.add(i)
                used_b.add(index[k].pop())
        ra = [(i, p) for i, p in ca if i not in used_a]
        rb = [(i, p) for i, p in cb if i not in used_b]
        changes: list[Change] = []
        # same centre, other diameter: resized
        resized: dict = {}
        at_centre: dict = {}
        for j, q in rb:
            at_centre.setdefault((q.layer, _r(q.data[0], 2), _r(q.data[1], 2)), []).append((j, q))
        for i, p in ra:
            for j, q in at_centre.get((p.layer, _r(p.data[0], 2), _r(p.data[1], 2)), []):
                if j in used_b:
                    continue
                used_a.add(i)
                used_b.add(j)
                resized.setdefault((p.layer, _r(2 * p.data[2]), _r(2 * q.data[2])), []).append((p, q))
                break
        for (layer, d0, d1), pairs in resized.items():
            zone = union([_bounds([p]) for p, _ in pairs] + [_bounds([q]) for _, q in pairs])
            changes.append(Change("holes", f"Layer {layer}", f"Ø{fmt(d0)}", f"Ø{fmt(d1)}", "major", pad(zone, diag * 0.01),
                                  {"action": "resized", "count": len(pairs), "from_d": d0, "to_d": d1, "layer": layer}))
        # per layer and diameter: added, removed, repositioned
        groups: dict = {}
        for i, p in ra:
            if i not in used_a:
                groups.setdefault((p.layer, _r(2 * p.data[2])), ([], []))[0].append(p)
        for j, q in rb:
            if j not in used_b:
                groups.setdefault((q.layer, _r(2 * q.data[2])), ([], []))[1].append(q)
        for (layer, d), (gone, came) in groups.items():
            n_old = sum(1 for _, p in ca if p.layer == layer and _r(2 * p.data[2]) == d)
            n_new = sum(1 for _, q in cb if q.layer == layer and _r(2 * q.data[2]) == d)
            moved = min(len(gone), len(came))
            zone = pad(union([_bounds([p]) for p in gone + came]), diag * 0.01)
            base = {"d": d, "layer": layer}
            if len(came) > len(gone):
                changes.append(Change("holes", f"Layer {layer}", n_old, n_new, "major", zone,
                                      {**base, "action": "added", "count": len(came) - len(gone), "moved": moved}))
            elif len(gone) > len(came):
                changes.append(Change("holes", f"Layer {layer}", n_old, n_new, "major", zone,
                                      {**base, "action": "removed", "count": len(gone) - len(came), "moved": moved}))
            elif moved:
                changes.append(Change("holes", f"Layer {layer}", n_old, n_new, "major", zone,
                                      {**base, "action": "moved", "count": moved}))
        ids_a = {id(p) for _, p in ca}
        ids_b = {id(q) for _, q in cb}
        return ids_a, ids_b, changes

    # ----------------------------------------------------- other entities
    def entities(self, old: _Drawing, new: _Drawing, skip_a: set, skip_b: set, diag: float) -> list[Change]:
        ga = [p for p in old.geom if id(p) not in skip_a]
        gb = [p for p in new.geom if id(p) not in skip_b]
        pool: dict = {}
        for p in gb:
            pool.setdefault((p.layer, geom_key(p)), []).append(p)
        removed = []
        for p in ga:
            k = (p.layer, geom_key(p))
            if pool.get(k):
                pool[k].pop()
            else:
                removed.append(p)
        added = [p for lst in pool.values() for p in lst]
        if not removed and not added:
            return []
        groups: dict = {}
        for p in removed:
            groups.setdefault((p.layer, p.kind), ([], []))[0].append(p)
        for p in added:
            groups.setdefault((p.layer, p.kind), ([], []))[1].append(p)
        out = []
        for (layer, kind), (gone, came) in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            moved, gone, came = pair(gone, came, shape_key, math.inf)
            changed, gone, came = pair(gone, came, lambda p: p.kind, diag * 0.05, overlap=True)
            sev = "minor" if all(p.cat == "frame" for p in gone + came + [x for m in moved + changed for x in m]) else "major"
            for action, items, zone_items in (("changed", changed, [x for m in changed for x in m]),
                                              ("moved", moved, [x for m in moved for x in m]),
                                              ("added", came, came), ("removed", gone, gone)):
                if not items:
                    continue
                n = len(items)
                before = None if action == "added" else n
                after = None if action == "removed" else n
                detail = {"action": action, "count": n, "entity": kind, "layer": layer}
                if action == "moved":   # the same displacement for all of them: say by how much
                    offs = {(_r(cb[0] - ca[0], 2), _r(cb[1] - ca[1], 2))
                            for ca, cb in ((_C(x).data_center, _C(y).data_center) for x, y in items)}
                    if len(offs) == 1:
                        detail["offset"] = list(next(iter(offs)))
                out.append(Change("entities", f"Layer {layer}", before, after, sev,
                                  pad(union([_bounds([x]) for x in zone_items]), diag * 0.01), detail))
        return out


class DwgComparer(DrawingComparer):
    name = "dwg"
    label = "DWG drawing comparison"
    exts = ("dwg",)

    def load(self, side: Side) -> bytes | None:
        try:
            return dwg.to_dxf(side.data())
        except dwg.DwgUnavailable:
            self.note("Only the title blocks of these DWG drawings were compared: geometry and dimensions need "
                      "the DWG converter on the server (Admin › CAD conversion).")
            return None


# ---------------------------------------------------------------- helpers --

def _r(v: float, nd: int = 3) -> float:
    x = round(float(v), nd)
    return 0.0 if x == 0 else x


def geom_key(p) -> tuple:
    k = p.kind
    if k == "line":
        a, b = p.data
        return ("line",) + tuple(sorted([(_r(a[0]), _r(a[1])), (_r(b[0]), _r(b[1]))]))
    if k == "circle":
        return ("circle", _r(p.data[0]), _r(p.data[1]), _r(p.data[2]))
    if k == "arc":
        cx, cy, r, a0, a1 = p.data
        return ("arc", _r(cx), _r(cy), _r(r), _r(a0 % 360, 2), _r(a1 % 360, 2))
    if k == "poly":
        pts, closed = p.data
        return ("poly", bool(closed), tuple((_r(x), _r(y)) for x, y in pts))
    return (k, repr(p.data))


def shape_key(p) -> tuple:
    """The same shape anywhere on the sheet (translation only)."""
    k = p.kind
    if k == "line":
        (x0, y0), (x1, y1) = p.data
        dx, dy = x1 - x0, y1 - y0
        if dx < 0 or (abs(dx) < 1e-12 and dy < 0):
            dx, dy = -dx, -dy
        return ("line", _r(dx), _r(dy))
    if k == "circle":
        return ("circle", _r(p.data[2]))
    if k == "arc":
        return ("arc", _r(p.data[2]), _r(p.data[3] % 360, 2), _r(p.data[4] % 360, 2))
    if k == "poly":
        pts, closed = p.data
        x0, y0 = pts[0]
        return ("poly", bool(closed), tuple((_r(x - x0), _r(y - y0)) for x, y in pts))
    return (k, repr(p.data))


class _C:
    __slots__ = ("data_center",)

    def __init__(self, p):
        b = _bounds([p])
        self.data_center = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if b else (0.0, 0.0)


def pair(gone: list, came: list, keyfn, maxdist: float, overlap: bool = False):
    """Greedy nearest pairing of removed and added entities that share ``keyfn``.
    Returns (pairs, unpaired removed, unpaired added)."""
    if not gone or not came:
        return [], gone, came
    by_key: dict = {}
    for i, p in enumerate(gone):
        by_key.setdefault(keyfn(p), ([], []))[0].append(i)
    for j, q in enumerate(came):
        by_key.setdefault(keyfn(q), ([], []))[1].append(j)
    centers_a = [_C(p).data_center for p in gone]
    centers_b = [_C(q).data_center for q in came]
    boxes_a = [_bounds([p]) for p in gone] if overlap else None
    boxes_b = [_bounds([q]) for q in came] if overlap else None
    cand = []
    for ia, ib in by_key.values():
        if not ia or not ib or len(ia) * len(ib) > MAX_PAIRS:
            continue
        for i in ia:
            for j in ib:
                d = math.hypot(centers_a[i][0] - centers_b[j][0], centers_a[i][1] - centers_b[j][1])
                if d > maxdist:
                    continue
                if overlap and not _near(boxes_a[i], boxes_b[j], maxdist):
                    continue
                cand.append((d, i, j))
    cand.sort()
    ua: set = set()
    ub: set = set()
    pairs = []
    for _, i, j in cand:
        if i in ua or j in ub:
            continue
        ua.add(i)
        ub.add(j)
        pairs.append((gone[i], came[j]))
    return pairs, [p for i, p in enumerate(gone) if i not in ua], [q for j, q in enumerate(came) if j not in ub]


def _near(a, b, margin: float) -> bool:
    if not a or not b:
        return False
    return not (a[2] + margin < b[0] or b[2] + margin < a[0] or a[3] + margin < b[1] or b[3] + margin < a[1])


def match_texts(old: list[_Text], new: list[_Text], maxdist: float):
    """Pair texts of two versions: identical ones drop out, nearby different ones are 'changed'."""
    # 1. identical text: unchanged wherever it moved (a moved label is not a measured change)
    pool: dict = {}
    for j, t in enumerate(new):
        pool.setdefault(t.text, []).append(j)
    left_a = []
    used_b: set = set()
    for t in old:
        js = [j for j in pool.get(t.text, []) if j not in used_b]
        if js:
            j = min(js, key=lambda j: math.hypot(new[j].x - t.x, new[j].y - t.y))
            used_b.add(j)
        else:
            left_a.append(t)
    left_b = [t for j, t in enumerate(new) if j not in used_b]
    # 2. nearest different text, same family first
    cand = []
    for i, a in enumerate(left_a):
        for j, b in enumerate(left_b):
            d = math.hypot(a.x - b.x, a.y - b.y)
            if d > maxdist:
                continue
            if family(a.text) != family(b.text):
                d += maxdist * 0.5
            cand.append((d, i, j))
    cand.sort()
    ua: set = set()
    ub: set = set()
    changed = []
    for _, i, j in cand:
        if i in ua or j in ub:
            continue
        ua.add(i)
        ub.add(j)
        changed.append((left_a[i], left_b[j]))
    changed.sort(key=lambda ab: (ab[1].x, -ab[1].y))
    return changed, [t for i, t in enumerate(left_a) if i not in ua], [t for j, t in enumerate(left_b) if j not in ub]


_FIT = re.compile(r"[Ø⌀]\s*\d+(?:[.,]\d+)?\s*([A-Za-z]{1,2})\d{1,2}\b")


def family(s: str) -> str:
    t = s.strip().upper()
    if re.match(r"^\d+\s*[X×]\s*[Ø⌀]", t):
        return "holes"
    if t.startswith(("Ø", "⌀")):
        return "diameter"
    if re.match(r"^S?R\s?\d", t):
        return "radius"
    if re.match(r"^M\d", t):
        return "thread"
    if "°" in t:
        return "angle"
    return "linear"


def noun_for(s: str) -> str:
    """'Ø80 H7' is a bore (capital ISO 286 letters are holes), 'Ø110 h6' a diameter, 'R5' a radius ..."""
    f = family(s)
    if len(re.findall(r"\d+(?:[.,]\d+)?", s)) > 2:
        return "Dimension"   # several values in one text, e.g. "Ø200 PCD 160 8xØ18"
    if f == "diameter":
        m = _FIT.search(s)
        return "Bore" if m and m.group(1)[0].isupper() else "Diameter"
    return {"holes": "Holes", "radius": "Radius", "thread": "Thread", "angle": "Angle"}.get(f, "Dimension")
