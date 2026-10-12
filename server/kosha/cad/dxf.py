# SPDX-License-Identifier: AGPL-3.0-or-later
"""A dependency-free DXF reader.

Reads ASCII DXF (R12 … 2018) well enough to:
  * render model space to SVG for the embedded viewer and thumbnails,
  * pull title-block attributes (part number, description, material, revision),
  * compute a geometric signature used by common-part detection.

AutoCAD and GStarCAD DWG files are first converted to DXF (see dwg.py).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable

MAX_PRIMITIVES = 400_000
MAX_INSERT_DEPTH = 8


class DxfError(Exception):
    pass


# ---------------------------------------------------------------- reading --

def decode(data: bytes) -> str:
    if data.startswith(b"AutoCAD Binary DXF"):
        raise DxfError("Binary DXF is not supported. Save the drawing as ASCII DXF or DWG.")
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def pairs(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    out: list[tuple[int, str]] = []
    n = len(lines) - 1
    i = 0
    while i < n:
        code_s = lines[i].strip()
        try:
            code = int(code_s)
        except ValueError:
            i += 1
            continue
        out.append((code, lines[i + 1].rstrip("\r")))
        i += 2
    return out


@dataclass
class Entity:
    type: str
    codes: list[tuple[int, str]]
    children: list["Entity"] = field(default_factory=list)  # VERTEX / ATTRIB

    def get(self, code: int, default=None):
        for c, v in self.codes:
            if c == code:
                return v
        return default

    def f(self, code: int, default: float = 0.0) -> float:
        v = self.get(code)
        if v is None:
            return default
        try:
            return float(v)
        except ValueError:
            return default

    def i(self, code: int, default: int = 0) -> int:
        v = self.get(code)
        if v is None:
            return default
        try:
            return int(float(v))
        except ValueError:
            return default

    def all(self, code: int) -> list[str]:
        return [v for c, v in self.codes if c == code]

    @property
    def layer(self) -> str:
        return self.get(8, "0") or "0"


@dataclass
class Block:
    name: str
    base: tuple[float, float]
    entities: list[Entity]
    flags: int = 0


@dataclass
class Document:
    header: dict
    layers: dict  # name -> {"color": int, "off": bool, "frozen": bool}
    blocks: dict  # name(upper) -> Block
    entities: list[Entity]

    @property
    def units(self) -> str:
        return {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}.get(int(self.header.get("$INSUNITS", 4) or 4), "mm")


def _group_entities(raw: list[Entity]) -> list[Entity]:
    """Attach VERTEX/ATTRIB records to their POLYLINE/INSERT parents."""
    out: list[Entity] = []
    parent: Entity | None = None
    for e in raw:
        if e.type in ("VERTEX", "ATTRIB") and parent is not None:
            parent.children.append(e)
            continue
        if e.type == "SEQEND":
            parent = None
            continue
        parent = None
        out.append(e)
        if e.type == "POLYLINE" or (e.type == "INSERT" and e.i(66) == 1):
            parent = e
    return out


def parse(data: bytes | str) -> Document:
    text = data if isinstance(data, str) else decode(data)
    pr = pairs(text)
    if not pr:
        raise DxfError("The file does not look like a DXF drawing.")
    header: dict = {}
    layers: dict = {}
    blocks: dict = {}
    entities: list[Entity] = []

    section = None
    i = 0
    n = len(pr)
    cur: Entity | None = None
    block_raw: list[Entity] | None = None
    block_info: dict | None = None
    ent_raw: list[Entity] = []
    in_layer_table = False
    layer_rec: dict | None = None
    hdr_var = None

    def flush():
        nonlocal cur
        if cur is None:
            return
        if section == "BLOCKS":
            if cur.type == "BLOCK":
                pass
            elif block_raw is not None:
                block_raw.append(cur)
        elif section == "ENTITIES":
            ent_raw.append(cur)
        cur = None

    while i < n:
        code, val = pr[i]
        if code == 0:
            v = val.strip()
            flush()
            if layer_rec is not None:
                layers[layer_rec["name"].upper()] = layer_rec
                layer_rec = None
            if v == "SECTION":
                section = pr[i + 1][1].strip() if i + 1 < n else None
                i += 2
                continue
            if v == "ENDSEC":
                section = None
                i += 1
                continue
            if v == "EOF":
                break
            if section == "TABLES":
                if v == "TABLE":
                    in_layer_table = i + 1 < n and pr[i + 1][1].strip() == "LAYER"
                elif v == "ENDTAB":
                    in_layer_table = False
                elif v == "LAYER" and in_layer_table:
                    layer_rec = {"name": "", "color": 7, "off": False, "frozen": False}
                i += 1
                continue
            if section == "BLOCKS":
                if v == "BLOCK":
                    block_raw = []
                    block_info = {"name": "", "x": 0.0, "y": 0.0, "flags": 0}
                    cur = Entity("BLOCK", [])
                elif v == "ENDBLK":
                    if block_info is not None and block_raw is not None:
                        b = Block(block_info["name"], (block_info["x"], block_info["y"]),
                                  _group_entities(block_raw), block_info["flags"])
                        blocks[b.name.upper()] = b
                    block_raw = None
                    block_info = None
                else:
                    cur = Entity(v, [])
                i += 1
                continue
            if section == "ENTITIES":
                cur = Entity(v, [])
                i += 1
                continue
            i += 1
            continue

        # non-zero group codes
        if section == "HEADER":
            if code == 9:
                hdr_var = val.strip()
            elif hdr_var:
                header.setdefault(hdr_var, val.strip())
        elif section == "TABLES" and layer_rec is not None:
            if code == 2:
                layer_rec["name"] = val.strip()
            elif code == 62:
                try:
                    c = int(val)
                except ValueError:
                    c = 7
                layer_rec["color"] = abs(c) or 7
                layer_rec["off"] = c < 0
            elif code == 70:
                try:
                    layer_rec["frozen"] = bool(int(val) & 1)
                except ValueError:
                    pass
        elif cur is not None:
            if cur.type == "BLOCK" and block_info is not None:
                if code == 2:
                    block_info["name"] = val.strip()
                elif code == 10:
                    block_info["x"] = _num(val)
                elif code == 20:
                    block_info["y"] = _num(val)
                elif code == 70:
                    block_info["flags"] = int(_num(val))
            else:
                cur.codes.append((code, val))
        i += 1
    flush()
    entities = _group_entities(ent_raw)
    return Document(header, layers, blocks, entities)


def _num(v: str) -> float:
    try:
        return float(v)
    except ValueError:
        return 0.0


# ------------------------------------------------------------- primitives --

@dataclass
class Prim:
    kind: str            # line | poly | circle | arc | text
    data: tuple
    color: int
    layer: str
    cat: str             # geom | text | dim | frame


_ANNOT_LAYER = re.compile(
    r"(DIM|TEXT|TXT|ANNO|TITLE|BORDER|FRAME|SHEET|HATCH|CENT|^CL$|HIDDEN|NOTE|SYMB|^TB|LOGO|VPORT|VIEWPORT|DEFPOINTS|REVISION|TABLE)",
    re.I,
)
_FRAME_BLOCK = re.compile(r"(TITLE|BORDER|FRAME|SHEET|^A[0-4]|LOGO|TB_|REV)", re.I)


class Affine:
    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        # x' = a*x + c*y + e ; y' = b*x + d*y + f
        self.a, self.b, self.c, self.d, self.e, self.f = a, b, c, d, e, f

    def apply(self, x, y):
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def then(self, other: "Affine") -> "Affine":
        """Return transform: first self, then other."""
        a = other.a * self.a + other.c * self.b
        b = other.b * self.a + other.d * self.b
        c = other.a * self.c + other.c * self.d
        d = other.b * self.c + other.d * self.d
        e = other.a * self.e + other.c * self.f + other.e
        f = other.b * self.e + other.d * self.f + other.f
        return Affine(a, b, c, d, e, f)

    @property
    def scale(self) -> float:
        return math.sqrt(abs(self.a * self.d - self.b * self.c))

    @property
    def uniform(self) -> bool:
        sx = math.hypot(self.a, self.b)
        sy = math.hypot(self.c, self.d)
        dot = self.a * self.c + self.b * self.d
        return abs(sx - sy) <= 1e-6 * max(sx, sy, 1e-12) and abs(dot) < 1e-6 * max(sx * sy, 1e-12)

    @property
    def mirrored(self) -> bool:
        return (self.a * self.d - self.b * self.c) < 0

    @property
    def rotation(self) -> float:
        return math.degrees(math.atan2(self.b, self.a))


IDENTITY = Affine()


def _ocs_flip(e: Entity) -> bool:
    return e.f(230, 1.0) < 0


def _bulge_points(p0, p1, bulge, segs_per_rad=6):
    if abs(bulge) < 1e-9:
        return [p1]
    x0, y0 = p0
    x1, y1 = p1
    theta = 4 * math.atan(bulge)
    chord = math.hypot(x1 - x0, y1 - y0)
    if chord < 1e-12:
        return [p1]
    r = chord / (2 * math.sin(abs(theta) / 2))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    d = math.sqrt(max(r * r - (chord / 2) ** 2, 0))
    ux, uy = (x1 - x0) / chord, (y1 - y0) / chord
    sgn = 1 if (bulge > 0) == (abs(theta) < math.pi) else -1
    cx, cy = mx - uy * d * sgn, my + ux * d * sgn
    a0 = math.atan2(y0 - cy, x0 - cx)
    n = max(2, int(abs(theta) * segs_per_rad))
    pts = []
    for k in range(1, n + 1):
        a = a0 + theta * k / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts[-1] = p1
    return pts


def _arc_points(cx, cy, r, a0, a1, n=None):
    span = (a1 - a0) % 360 or 360
    n = n or max(4, int(span / 6))
    return [(cx + r * math.cos(math.radians(a0 + span * k / n)),
             cy + r * math.sin(math.radians(a0 + span * k / n))) for k in range(n + 1)]


def _bspline(ctrl, knots, degree, samples):
    n = len(ctrl)
    if degree < 1 or len(knots) != n + degree + 1:
        return ctrl
    lo, hi = knots[degree], knots[n]
    if hi <= lo:
        return ctrl
    out = []
    for s in range(samples + 1):
        t = lo + (hi - lo) * s / samples
        k = degree
        while k < n - 1 and t >= knots[k + 1]:
            k += 1
        d = [list(ctrl[j + k - degree]) for j in range(degree + 1)]
        for r_ in range(1, degree + 1):
            for j in range(degree, r_ - 1, -1):
                i_ = j + k - degree
                denom = knots[i_ + degree - r_ + 1] - knots[i_]
                alpha = 0.0 if denom == 0 else (t - knots[i_]) / denom
                d[j][0] = (1 - alpha) * d[j - 1][0] + alpha * d[j][0]
                d[j][1] = (1 - alpha) * d[j - 1][1] + alpha * d[j][1]
        out.append((d[degree][0], d[degree][1]))
    return out


_MTEXT_CODES = re.compile(r"\\[ACcFfHhQqTtWwp][^;\\]*;|\\[LlOoKkNn]|\\S([^;]*);")


def clean_text(s: str) -> str:
    s = s.replace("\\P", "\n").replace("\\~", " ")
    s = _MTEXT_CODES.sub(lambda m: (m.group(1) or "").replace("^", "/").replace("#", "/"), s)
    s = s.replace("{", "").replace("}", "").replace("\\\\", "\\")
    for k, v in (("%%c", "Ø"), ("%%C", "Ø"), ("%%d", "°"), ("%%D", "°"), ("%%p", "±"), ("%%P", "±"),
                 ("%%u", ""), ("%%U", ""), ("%%o", ""), ("%%O", "")):
        s = s.replace(k, v)
    return s.strip()


class Collector:
    def __init__(self, doc: Document):
        self.doc = doc
        self.prims: list[Prim] = []
        self.attribs: dict[str, str] = {}
        self.truncated = False

    def layer_visible(self, layer: str) -> bool:
        L = self.doc.layers.get(layer.upper())
        return not (L and (L["off"] or L["frozen"]))

    def color_of(self, e: Entity, block_color: int) -> int:
        c = e.i(62, 256)
        if c == 256:
            L = self.doc.layers.get(e.layer.upper())
            return L["color"] if L else 7
        if c == 0:
            return block_color
        return abs(c)

    def add(self, kind, data, color, layer, cat):
        if len(self.prims) >= MAX_PRIMITIVES:
            self.truncated = True
            return
        self.prims.append(Prim(kind, data, color, layer, cat))

    def run(self, include_paper=False):
        ents = [e for e in self.doc.entities if include_paper or e.i(67) != 1]
        if not include_paper and not ents:
            ents = self.doc.entities
        self.walk(ents, IDENTITY, 7, 0, None, "0")
        # attributes from paper space title blocks too
        for e in self.doc.entities:
            if e.type == "INSERT":
                for a in e.children:
                    tag = (a.get(2) or "").strip().upper()
                    val = clean_text(a.get(1) or "")
                    if tag and val and tag not in self.attribs:
                        self.attribs[tag] = val
        return self

    def walk(self, ents: Iterable[Entity], T: Affine, block_color: int, depth: int, forced_cat, parent_layer):
        for e in ents:
            layer = e.layer
            if layer == "0" and parent_layer:
                layer = parent_layer
            if not self.layer_visible(layer):
                continue
            color = self.color_of(e, block_color)
            cat = forced_cat or ("text" if e.type in ("TEXT", "MTEXT", "ATTRIB") else
                                 "dim" if e.type in ("DIMENSION", "LEADER", "MULTILEADER", "TOLERANCE") else
                                 "frame" if _ANNOT_LAYER.search(layer) else "geom")
            try:
                self.entity(e, T, color, layer, cat, depth)
            except (ValueError, ZeroDivisionError, IndexError):
                continue

    def entity(self, e: Entity, T: Affine, color: int, layer: str, cat: str, depth: int):
        t = e.type
        flip = _ocs_flip(e)
        fx = (lambda x: -x) if flip else (lambda x: x)
        if t == "LINE":
            p0 = T.apply(e.f(10), e.f(20))
            p1 = T.apply(e.f(11), e.f(21))
            self.add("line", (p0, p1), color, layer, cat)
        elif t in ("CIRCLE", "ARC"):
            cx, cy, r = fx(e.f(10)), e.f(20), e.f(40)
            if t == "ARC":
                a0, a1 = e.f(50), e.f(51)
                if flip:
                    a0, a1 = 180 - a1, 180 - a0
            if T.uniform:
                c = T.apply(cx, cy)
                rr = r * T.scale
                if t == "CIRCLE":
                    self.add("circle", (c[0], c[1], rr), color, layer, cat)
                else:
                    rot = T.rotation
                    if T.mirrored:
                        a0, a1 = rot - a1, rot - a0
                    else:
                        a0 += rot
                        a1 += rot
                    self.add("arc", (c[0], c[1], rr, a0 % 360, a1 % 360), color, layer, cat)
            else:
                pts = _arc_points(cx, cy, r, 0, 360) if t == "CIRCLE" else _arc_points(cx, cy, r, a0, a1)
                self.add("poly", ([T.apply(*p) for p in pts], t == "CIRCLE"), color, layer, cat)
        elif t == "LWPOLYLINE":
            xs = [float(v) for v in e.all(10)]
            ys = [float(v) for v in e.all(20)]
            # bulge (42) follows the vertex it belongs to; rebuild in order
            vi = -1
            bulge_by_vertex: dict[int, float] = {}
            for c, v in e.codes:
                if c == 10:
                    vi += 1
                elif c == 42 and vi >= 0:
                    bulge_by_vertex[vi] = _num(v)
            closed = bool(e.i(70) & 1)
            verts = [(fx(x), y) for x, y in zip(xs, ys)]
            self._poly(verts, bulge_by_vertex, closed, T, color, layer, cat, flip)
        elif t == "POLYLINE":
            flags = e.i(70)
            if flags & (16 | 64):  # polygon mesh / polyface: draw vertices as lines
                verts = [(v.f(10), v.f(20)) for v in e.children if not (v.i(70) & 128)]
                if len(verts) > 1:
                    self.add("poly", ([T.apply(*p) for p in verts], False), color, layer, cat)
                return
            verts = [(fx(v.f(10)), v.f(20)) for v in e.children]
            bulges = {k: v.f(42) for k, v in enumerate(e.children) if v.f(42)}
            self._poly(verts, bulges, bool(flags & 1), T, color, layer, cat, flip)
        elif t == "ELLIPSE":
            cx, cy = e.f(10), e.f(20)
            mx, my = e.f(11), e.f(21)
            ratio = e.f(40, 1.0)
            t0, t1 = e.f(41, 0.0), e.f(42, 2 * math.pi)
            if t1 <= t0:
                t1 += 2 * math.pi
            n = max(8, int((t1 - t0) / (2 * math.pi) * 72))
            pts = []
            for k in range(n + 1):
                tt = t0 + (t1 - t0) * k / n
                x = cx + math.cos(tt) * mx - math.sin(tt) * my * ratio
                y = cy + math.cos(tt) * my + math.sin(tt) * mx * ratio
                pts.append(T.apply(fx(x), y))
            self.add("poly", (pts, False), color, layer, cat)
        elif t == "SPLINE":
            fit = list(zip([float(v) for v in e.all(11)], [float(v) for v in e.all(21)]))
            if len(fit) >= 2:
                pts = fit
            else:
                ctrl = list(zip([float(v) for v in e.all(10)], [float(v) for v in e.all(20)]))
                knots = [float(v) for v in e.all(40)]
                pts = _bspline(ctrl, knots, e.i(71, 3), max(16, len(ctrl) * 8))
            if len(pts) >= 2:
                self.add("poly", ([T.apply(*p) for p in pts], bool(e.i(70) & 1)), color, layer, cat)
        elif t in ("SOLID", "TRACE", "3DFACE"):
            p = [(e.f(10), e.f(20)), (e.f(11), e.f(21)), (e.f(13), e.f(23)), (e.f(12), e.f(22))]
            self.add("poly", ([T.apply(*q) for q in p], True), color, layer, cat)
        elif t == "LEADER":
            pts = list(zip([float(v) for v in e.all(10)], [float(v) for v in e.all(20)]))
            if len(pts) >= 2:
                self.add("poly", ([T.apply(*p) for p in pts], False), color, layer, "dim")
        elif t in ("TEXT", "ATTRIB", "MTEXT"):
            if t == "ATTRIB" and (e.i(70) & 1):
                return
            s = e.get(1) or ""
            if t == "MTEXT":
                s = s + "".join(e.all(3)) if not e.all(3) else "".join(e.all(3)) + (e.get(1) or "")
            s = clean_text(s)
            if not s:
                return
            h = e.f(40, 2.5)
            rot = e.f(50, 0.0)
            if t == "MTEXT":
                rot = math.degrees(rot)
                if e.get(11) is not None and e.get(50) is None:
                    rot = math.degrees(math.atan2(e.f(21), e.f(11)))
                att = e.i(71, 1)
                anchor = {1: "start", 2: "middle", 3: "end"}[(att - 1) % 3 + 1]
                vpos = (att - 1) // 3  # 0 top, 1 middle, 2 bottom
                x, y = e.f(10), e.f(20)
            else:
                ha, va = e.i(72), e.i(73)
                anchor = {0: "start", 1: "middle", 2: "end", 4: "middle"}.get(ha, "start")
                vpos = {3: 0, 2: 1, 1: 2}.get(va, 2) if ha != 4 else 1
                if (ha or va) and e.get(11) is not None:
                    x, y = e.f(11), e.f(21)
                else:
                    x, y = e.f(10), e.f(20)
            px, py = T.apply(fx(x), y)
            self.add("text", (px, py, h * (T.scale or 1), (rot + T.rotation) % 360, s, anchor, vpos), color, layer, cat)
        elif t == "INSERT":
            if depth >= MAX_INSERT_DEPTH:
                return
            name = (e.get(2) or "").strip()
            blk = self.doc.blocks.get(name.upper())
            sx, sy = e.f(41, 1.0), e.f(42, 1.0)
            rot = math.radians(e.f(50, 0.0))
            ix, iy = fx(e.f(10)), e.f(20)
            if flip:
                sx = -sx
                rot = -rot
            cols, rows_ = max(1, e.i(70, 1)), max(1, e.i(71, 1))
            dc, dr = e.f(44), e.f(45)
            sub_cat = "frame" if (_FRAME_BLOCK.search(name) or cat == "frame") else (None if cat == "geom" else cat)
            if blk is not None:
                for r_ in range(min(rows_, 100)):
                    for c_ in range(min(cols, 100)):
                        ox, oy = c_ * dc, r_ * dr
                        cos, sin = math.cos(rot), math.sin(rot)
                        L = Affine(1, 0, 0, 1, -blk.base[0], -blk.base[1])
                        S = Affine(sx, 0, 0, sy, 0, 0)
                        R = Affine(cos, sin, -sin, cos, 0, 0)
                        Tr = Affine(1, 0, 0, 1, ix + cos * ox - sin * oy, iy + sin * ox + cos * oy)
                        M = L.then(S).then(R).then(Tr).then(T)
                        self.walk(blk.entities, M, color, depth + 1, sub_cat, layer)
            for a in e.children:
                self.entity(a, T, self.color_of(a, color), a.layer, "text", depth)
        elif t == "DIMENSION":
            name = (e.get(2) or "").strip()
            blk = self.doc.blocks.get(name.upper())
            if blk is not None and depth < MAX_INSERT_DEPTH:
                self.walk(blk.entities, T, color, depth + 1, "dim", layer)
        elif t == "POINT":
            return

    def _poly(self, verts, bulges, closed, T, color, layer, cat, flip):
        if len(verts) < 2:
            return
        pts = [verts[0]]
        seq = verts + ([verts[0]] if closed else [])
        for k in range(len(seq) - 1):
            b = bulges.get(k, 0.0)
            if flip:
                b = -b
            pts.extend(_bulge_points(seq[k], seq[k + 1], b))
        self.add("poly", ([T.apply(*p) for p in pts], closed), color, layer, cat)


def collect(doc: Document) -> Collector:
    return Collector(doc).run()


# ---------------------------------------------------------------- bounds --

def prim_points(p: Prim) -> list[tuple[float, float]]:
    if p.kind == "line":
        return list(p.data)
    if p.kind == "poly":
        return p.data[0]
    if p.kind == "circle":
        cx, cy, r = p.data
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if p.kind == "arc":
        cx, cy, r, a0, a1 = p.data
        return _arc_points(cx, cy, r, a0, a1, 8)
    if p.kind == "text":
        x, y, h = p.data[0], p.data[1], p.data[2]
        w = h * 0.6 * max(len(l) for l in p.data[4].split("\n"))
        return [(x, y), (x + w, y + h)]
    return []


def bounds(prims: Iterable[Prim]):
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for p in prims:
        for x, y in prim_points(p):
            if x < minx:
                minx = x
            if x > maxx:
                maxx = x
            if y < miny:
                miny = y
            if y > maxy:
                maxy = y
    if minx is math.inf:
        return None
    return minx, miny, maxx, maxy


# -------------------------------------------------------------------- SVG --

ACI = {1: "#d32f2f", 2: "#a07800", 3: "#2e7d32", 4: "#00838f", 5: "#1565c0", 6: "#8e24aa",
       7: "#16191d", 8: "#6b6b6b", 9: "#8d8d8d", 250: "#333333", 251: "#505050", 252: "#696969",
       253: "#828282", 254: "#bebebe", 255: "#d0d0d0"}


def _color(c: int, mono: bool) -> str:
    if mono:
        return "#16191d"
    return ACI.get(c, "#2b3036")


def _fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def to_svg(col: Collector, mono: bool = False, title: str = "") -> str:
    prims = col.prims
    b = bounds(prims)
    if b is None:
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60"><text x="50" y="32" '
                'text-anchor="middle" font-size="6" fill="#666">Empty drawing</text></svg>')
    minx, miny, maxx, maxy = b
    w, h = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    pad = max(w, h) * 0.02
    vb = (minx - pad, -(maxy + pad), w + 2 * pad, h + 2 * pad)
    paths: dict[str, list[str]] = {}
    shapes: list[str] = []
    texts: list[str] = []
    for p in prims:
        col_ = _color(p.color, mono)
        if p.kind == "line":
            (x0, y0), (x1, y1) = p.data
            paths.setdefault(col_, []).append(f"M{_fmt(x0)} {_fmt(y0)}L{_fmt(x1)} {_fmt(y1)}")
        elif p.kind == "poly":
            pts, closed = p.data
            d = "M" + "L".join(f"{_fmt(x)} {_fmt(y)}" for x, y in pts) + ("Z" if closed else "")
            paths.setdefault(col_, []).append(d)
        elif p.kind == "circle":
            cx, cy, r = p.data
            shapes.append(f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}" stroke="{col_}"/>')
        elif p.kind == "arc":
            cx, cy, r, a0, a1 = p.data
            span = (a1 - a0) % 360 or 360
            if span >= 359.999:
                shapes.append(f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}" stroke="{col_}"/>')
                continue
            x0, y0 = cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0))
            x1, y1 = cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1))
            large = 1 if span > 180 else 0
            paths.setdefault(col_, []).append(
                f"M{_fmt(x0)} {_fmt(y0)}A{_fmt(r)} {_fmt(r)} 0 {large} 1 {_fmt(x1)} {_fmt(y1)}")
        elif p.kind == "text":
            x, y, hh, rot, s, anchor, vpos = p.data
            lines = s.split("\n")
            dy0 = {0: hh, 1: hh * 0.5 - (len(lines) - 1) * hh * 0.7, 2: -(len(lines) - 1) * hh * 1.4}.get(vpos, 0)
            tsp = "".join(
                f'<tspan x="0" dy="{_fmt(dy0 if k == 0 else hh * 1.4)}">{_esc(line)}</tspan>'
                for k, line in enumerate(lines)
            )
            texts.append(
                f'<text transform="translate({_fmt(x)} {_fmt(y)}) rotate({_fmt(rot)}) scale(1 -1)" '
                f'font-size="{_fmt(hh)}" fill="{col_}" text-anchor="{anchor}">{tsp}</text>'
            )
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{" ".join(_fmt(v) for v in vb)}" '
        f'data-units="{col.doc.units}" preserveAspectRatio="xMidYMid meet">',
    ]
    if title:
        out.append(f"<title>{_esc(title)}</title>")
    out.append('<rect x="%s" y="%s" width="%s" height="%s" fill="#ffffff"/>' % tuple(_fmt(v) for v in vb))
    out.append('<g transform="scale(1 -1)" fill="none" stroke-width="1" vector-effect="non-scaling-stroke" '
               'stroke-linecap="round" stroke-linejoin="round">')
    for col_, ds in paths.items():
        # split very long path strings so browsers stay responsive
        for k in range(0, len(ds), 4000):
            out.append(f'<path d="{"".join(ds[k:k + 4000])}" stroke="{col_}" vector-effect="non-scaling-stroke"/>')
    for s in shapes:
        out.append(s.replace("/>", ' vector-effect="non-scaling-stroke"/>'))
    out.append('<g stroke="none" font-family="Consolas, \'Cascadia Mono\', monospace">')
    out.extend(texts)
    out.append("</g></g></svg>")
    return "".join(out)


# ------------------------------------------------------------- metadata --

TAG_MAP = {
    "part_number": ("PART_NO", "PARTNO", "PART_NUMBER", "PARTNUMBER", "PART NO", "ITEM_NO", "ITEMCODE",
                    "ITEM_CODE", "DRG_NO", "DRGNO", "DWG_NO", "DWGNO", "DRAWING_NO", "DRAWINGNO", "DRAWING_NUMBER",
                    "DOC_NO", "NUMBER"),
    "description": ("DESCRIPTION", "DESC", "TITLE", "TITLE1", "DRAWING_TITLE", "NAME", "PART_NAME", "PARTNAME"),
    "material": ("MATERIAL", "MATL", "MAT", "MOC"),
    "revision": ("REV", "REVISION", "REV_NO", "REVNO"),
    "weight": ("WEIGHT", "WT", "MASS"),
    "scale": ("SCALE",),
    "drawn_by": ("DRAWN", "DRAWN_BY", "DRN", "DRN_BY"),
}


def title_block(attribs: dict[str, str]) -> dict:
    out = {}
    for key, tags in TAG_MAP.items():
        for t in tags:
            if t in attribs and attribs[t].strip():
                out[key] = attribs[t].strip()
                break
    return out


# -------------------------------------------------------------- signature --

GRID = 16


def _sample(p: Prim, step: float):
    if p.kind == "line":
        (x0, y0), (x1, y1) = p.data
        L = math.hypot(x1 - x0, y1 - y0)
        n = max(1, min(400, int(L / step)))
        for k in range(n + 1):
            yield x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n
    elif p.kind == "poly":
        pts = p.data[0]
        for a, b in zip(pts, pts[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, min(400, int(L / step)))
            for k in range(n):
                yield a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n
    elif p.kind in ("circle", "arc"):
        if p.kind == "circle":
            cx, cy, r = p.data
            a0, a1 = 0, 360
        else:
            cx, cy, r, a0, a1 = p.data
        span = (a1 - a0) % 360 or 360
        n = max(4, min(720, int(math.radians(span) * r / step)))
        for k in range(n + 1):
            a = math.radians(a0 + span * k / n)
            yield cx + r * math.cos(a), cy + r * math.sin(a)


def _length(p: Prim) -> float:
    if p.kind == "line":
        (x0, y0), (x1, y1) = p.data
        return math.hypot(x1 - x0, y1 - y0)
    if p.kind == "poly":
        pts = p.data[0]
        return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    if p.kind == "circle":
        return 2 * math.pi * p.data[2]
    if p.kind == "arc":
        span = (p.data[4] - p.data[3]) % 360 or 360
        return math.radians(span) * p.data[2]
    return 0.0


def _sheet_shaped(w: float, h: float) -> bool:
    big, small = max(w, h), min(w, h)
    if small <= 0:
        return False
    r = big / small
    return any(abs(r - k) <= 0.04 for k in (1.414, 1.294, 1.545))


def _drop_frames(geom: list[Prim]) -> list[Prim]:
    """Remove drawing borders: sheet-proportioned rectangles enclosing everything."""
    for _ in range(3):
        b = bounds(geom)
        if not b:
            return geom
        minx, miny, maxx, maxy = b
        W, H = maxx - minx, maxy - miny
        if W <= 0 or H <= 0 or not _sheet_shaped(W, H):
            return geom
        tol_x, tol_y = 0.01 * W, 0.01 * H
        remove: set[int] = set()
        sides = {"top": [], "bottom": [], "left": [], "right": []}
        for idx, p in enumerate(geom):
            if p.kind not in ("poly", "line"):
                continue
            pb = bounds([p])
            if not pb:
                continue
            w, h = pb[2] - pb[0], pb[3] - pb[1]
            if p.kind == "poly" and p.data[1] and w >= 0.98 * W and h >= 0.98 * H:
                remove.add(idx)
                continue
            if w >= 0.97 * W and h <= tol_y:
                if abs(pb[3] - maxy) <= tol_y:
                    sides["top"].append(idx)
                elif abs(pb[1] - miny) <= tol_y:
                    sides["bottom"].append(idx)
            elif h >= 0.97 * H and w <= tol_x:
                if abs(pb[0] - minx) <= tol_x:
                    sides["left"].append(idx)
                elif abs(pb[2] - maxx) <= tol_x:
                    sides["right"].append(idx)
        if all(sides.values()):
            for v in sides.values():
                remove.update(v)
        if not remove or len(remove) == len(geom):
            return geom
        geom = [p for k, p in enumerate(geom) if k not in remove]
    return geom


def signature(col: Collector) -> dict | None:
    geom = [p for p in col.prims if p.cat == "geom" and p.kind != "text"]
    geom = _drop_frames(geom)
    b = bounds(geom)
    if not geom or not b:
        return None
    minx, miny, maxx, maxy = b
    W, H = maxx - minx, maxy - miny
    S = max(W, H)
    if S <= 0:
        return None
    step = S / 200.0
    grid = [0.0] * (GRID * GRID)
    ox = minx - (S - W) / 2
    oy = miny - (S - H) / 2
    total = 0
    for p in geom:
        for x, y in _sample(p, step):
            gx = min(GRID - 1, max(0, int((x - ox) / S * GRID)))
            gy = min(GRID - 1, max(0, int((y - oy) / S * GRID)))
            grid[gy * GRID + gx] += 1
            total += 1
    if total:
        grid = [round(v / total, 4) for v in grid]
    diam = sorted((round(2 * p.data[2], 2) for p in geom if p.kind == "circle"), reverse=True)
    radii = sorted((round(p.data[2], 2) for p in geom if p.kind == "arc"), reverse=True)
    counts = {
        "line": sum(1 for p in geom if p.kind == "line"),
        "poly": sum(1 for p in geom if p.kind == "poly"),
        "circle": len(diam),
        "arc": len(radii),
    }
    tot_len = sum(_length(p) for p in geom)
    return {
        "v": 1,
        "w": round(max(W, H), 3),
        "h": round(min(W, H), 3),
        "landscape": W >= H,
        "grid": grid,
        "diam": diam[:24],
        "radii": radii[:24],
        "counts": counts,
        "len_ratio": round(tot_len / math.hypot(W, H), 3),
    }


def analyse(data: bytes, name: str = "") -> dict:
    """Full pipeline for one DXF file: svg, attributes, signature."""
    doc = parse(data)
    col = collect(doc)
    svg = to_svg(col, title=name)
    attrs = col.attribs
    return {
        "svg": svg,
        "attributes": attrs,
        "title_block": title_block(attrs),
        "signature": signature(col),
        "units": doc.units,
        "entity_count": len(col.prims),
        "truncated": col.truncated,
    }
