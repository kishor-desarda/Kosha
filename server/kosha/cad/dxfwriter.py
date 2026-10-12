# SPDX-License-Identifier: AGPL-3.0-or-later
"""Minimal R12 ASCII DXF writer (used for demo data and tests).

The output opens in AutoCAD, GStarCAD and LibreCAD.
"""
from __future__ import annotations

import math


class DxfWriter:
    def __init__(self):
        self.entities: list[str] = []
        self.blocks: list[str] = []
        self.layers: dict[str, int] = {"0": 7}

    # -- helpers
    @staticmethod
    def _g(code, value) -> str:
        if isinstance(value, float):
            value = f"{value:.6f}".rstrip("0").rstrip(".")
        return f"{code}\n{value}\n"

    def layer(self, name: str, color: int = 7) -> str:
        self.layers.setdefault(name, color)
        return name

    def _ent(self, kind: str, layer: str, *codes, target=None) -> None:
        self.layer(layer)
        s = self._g(0, kind) + self._g(8, layer) + "".join(self._g(c, v) for c, v in codes)
        (target if target is not None else self.entities).append(s)

    # -- entities
    def line(self, x1, y1, x2, y2, layer="0", target=None):
        self._ent("LINE", layer, (10, float(x1)), (20, float(y1)), (30, 0.0),
                  (11, float(x2)), (21, float(y2)), (31, 0.0), target=target)

    def circle(self, x, y, r, layer="0", target=None):
        self._ent("CIRCLE", layer, (10, float(x)), (20, float(y)), (30, 0.0), (40, float(r)), target=target)

    def arc(self, x, y, r, a0, a1, layer="0", target=None):
        self._ent("ARC", layer, (10, float(x)), (20, float(y)), (30, 0.0), (40, float(r)),
                  (50, float(a0)), (51, float(a1)), target=target)

    def text(self, x, y, h, s, layer="TEXT", align=0, target=None):
        codes = [(10, float(x)), (20, float(y)), (30, 0.0), (40, float(h)), (1, s)]
        if align:
            codes += [(72, align), (11, float(x)), (21, float(y)), (31, 0.0)]
        self._ent("TEXT", layer, *codes, target=target)

    def polyline(self, pts, closed=False, layer="0", target=None):
        self.layer(layer)
        s = self._g(0, "POLYLINE") + self._g(8, layer) + self._g(66, 1) + self._g(10, 0.0) + \
            self._g(20, 0.0) + self._g(30, 0.0) + self._g(70, 1 if closed else 0)
        for x, y in pts:
            s += self._g(0, "VERTEX") + self._g(8, layer) + self._g(10, float(x)) + self._g(20, float(y)) + self._g(30, 0.0)
        s += self._g(0, "SEQEND") + self._g(8, layer)
        (target if target is not None else self.entities).append(s)

    def rect(self, x, y, w, h, layer="0", target=None):
        self.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], closed=True, layer=layer, target=target)

    def dim_h(self, x1, x2, y, offset, label, layer="DIM"):
        """Simple linear dimension drawn with lines and text (no DIMENSION entity)."""
        yy = y + offset
        self.line(x1, y, x1, yy + 2, layer)
        self.line(x2, y, x2, yy + 2, layer)
        self.line(x1, yy, x2, yy, layer)
        self.text((x1 + x2) / 2, yy + 1.5, 3.5, label, layer, align=1)

    # -- title block
    def define_title_block(self, name="KOSHA_TITLE", company="DEMO ENGINEERING"):
        body: list[str] = []
        self.rect(0, 0, 180, 40, "TITLE", target=body)
        self.line(0, 20, 180, 20, "TITLE", target=body)
        self.line(90, 0, 90, 40, "TITLE", target=body)
        self.line(135, 0, 135, 20, "TITLE", target=body)
        self.text(4, 30, 5, company, "TITLE", target=body)
        for tag, x, y in (("DESCRIPTION", 94, 30), ("PART_NO", 4, 10), ("MATERIAL", 94, 10), ("REV", 139, 10)):
            body.append(
                self._g(0, "ATTDEF") + self._g(8, "TITLE") + self._g(10, float(x)) + self._g(20, float(y)) +
                self._g(30, 0.0) + self._g(40, 4.0) + self._g(1, "") + self._g(3, tag) + self._g(2, tag) +
                self._g(70, 0)
            )
        self.blocks.append(
            self._g(0, "BLOCK") + self._g(8, "0") + self._g(2, name) + self._g(70, 2) +
            self._g(10, 0.0) + self._g(20, 0.0) + self._g(30, 0.0) + self._g(3, name) +
            "".join(body) + self._g(0, "ENDBLK") + self._g(8, "0")
        )
        return name

    def insert_title_block(self, x, y, values: dict, name="KOSHA_TITLE"):
        positions = {"DESCRIPTION": (94, 30), "PART_NO": (4, 10), "MATERIAL": (94, 10), "REV": (139, 10)}
        s = self._g(0, "INSERT") + self._g(8, "TITLE") + self._g(66, 1) + self._g(2, name) + \
            self._g(10, float(x)) + self._g(20, float(y)) + self._g(30, 0.0)
        for tag, (px, py) in positions.items():
            s += (self._g(0, "ATTRIB") + self._g(8, "TITLE") + self._g(10, float(x + px)) +
                  self._g(20, float(y + py)) + self._g(30, 0.0) + self._g(40, 4.0) +
                  self._g(1, values.get(tag, "")) + self._g(2, tag) + self._g(70, 0))
        s += self._g(0, "SEQEND") + self._g(8, "TITLE")
        self.layer("TITLE")
        self.entities.append(s)

    def sheet(self, w=420, h=297, values: dict | None = None):
        self.rect(0, 0, w, h, "BORDER")
        self.rect(10, 10, w - 20, h - 20, "BORDER")
        self.define_title_block()
        self.insert_title_block(w - 190, 10, values or {})

    # -- output
    def tostring(self) -> str:
        out = [self._g(0, "SECTION"), self._g(2, "HEADER"), self._g(9, "$ACADVER"), self._g(1, "AC1009"),
               self._g(9, "$INSUNITS"), self._g(70, 4), self._g(0, "ENDSEC")]
        out += [self._g(0, "SECTION"), self._g(2, "TABLES"), self._g(0, "TABLE"), self._g(2, "LAYER"),
                self._g(70, len(self.layers))]
        for name, color in self.layers.items():
            out += [self._g(0, "LAYER"), self._g(2, name), self._g(70, 0), self._g(62, color), self._g(6, "CONTINUOUS")]
        out += [self._g(0, "ENDTAB"), self._g(0, "ENDSEC")]
        out += [self._g(0, "SECTION"), self._g(2, "BLOCKS")] + self.blocks + [self._g(0, "ENDSEC")]
        out += [self._g(0, "SECTION"), self._g(2, "ENTITIES")] + self.entities + [self._g(0, "ENDSEC")]
        out.append(self._g(0, "EOF"))
        return "".join(out)

    def tobytes(self) -> bytes:
        return self.tostring().encode("cp1252", errors="replace")


# ------------------------------------------------------------ demo parts --

def bearing_housing(bore=80.0, base_w=220.0, height=120.0, bolt=18.0, values=None, sheet=True) -> bytes:
    """Front + side view of a plummer-block style bearing housing."""
    d = DxfWriter()
    d.layer("DIM", 3)
    d.layer("TEXT", 2)
    d.layer("CENTER", 1)
    if sheet:
        d.sheet(values=values)
    ox, oy = 60.0, 70.0
    # front view
    base_h = 30.0
    d.rect(ox, oy, base_w, base_h)
    cx = ox + base_w / 2
    cy = oy + height - bore * 0.35
    R = bore / 2 + 35
    d.line(cx - R, oy + base_h, cx - R, cy)
    d.line(cx + R, oy + base_h, cx + R, cy)
    d.arc(cx, cy, R, 0, 180)
    d.circle(cx, cy, bore / 2)
    d.circle(cx, cy, bore / 2 + 8)
    for sx in (-1, 1):
        d.circle(cx + sx * (base_w / 2 - 22), oy + base_h / 2, bolt / 2)
    d.line(cx - R - 12, cy, cx + R + 12, cy, "CENTER")
    d.line(cx, oy - 8, cx, cy + R + 10, "CENTER")
    d.dim_h(ox, ox + base_w, oy, -18, f"{base_w:g}")
    d.text(cx + bore / 2 + 4, cy + 4, 3.5, f"%%c{bore:g} H7", "DIM")
    # side view
    sx0 = ox + base_w + 50
    width = bore * 0.9
    d.rect(sx0, oy, width, base_h)
    d.rect(sx0 + 10, oy + base_h, width - 20, height - base_h + R * 0.4)
    d.line(sx0 - 6, cy, sx0 + width + 6, cy, "CENTER")
    return d.tobytes()


def shaft(lengths=(60, 80, 220, 80, 80), diameters=(80, 95, 110, 95, 80), keyway=True, values=None, sheet=True) -> bytes:
    d = DxfWriter()
    d.layer("DIM", 3)
    d.layer("TEXT", 2)
    d.layer("CENTER", 1)
    if sheet:
        d.sheet(values=values)
    x = 50.0
    cy = 170.0
    scale = 0.5
    pts_top = []
    for L, D in zip(lengths, diameters):
        pts_top.append((x, cy + D * scale / 2))
        x += L * scale
        pts_top.append((x, cy + D * scale / 2))
    pts_bot = [(px, cy - (py - cy)) for px, py in reversed(pts_top)]
    outline = [(50.0, cy)] + pts_top + [(x, cy)] + pts_bot
    d.polyline(outline, closed=True)
    xx = 50.0
    for L, D in zip(lengths[:-1], diameters[:-1]):
        xx += L * scale
        d.line(xx, cy - 60 * scale, xx, cy + 60 * scale)
    if keyway:
        k0 = 50 + (lengths[0] + lengths[1]) * scale + 20
        d.rect(k0, cy + diameters[2] * scale / 2 - 8 * scale, 60, 8 * scale)
    d.line(40, cy, x + 10, cy, "CENTER")
    d.dim_h(50, x, cy - 60, -12, f"{sum(lengths):g}")
    xx = 50.0
    for L, D in zip(lengths, diameters):
        d.text(xx + L * scale / 2, cy + D * scale / 2 + 4, 3, f"%%c{D:g}", "DIM", align=1)
        xx += L * scale
    return d.tobytes()


def flange(od=200.0, pcd=160.0, holes=8, hole_d=18.0, bore=100.0, values=None, sheet=True) -> bytes:
    d = DxfWriter()
    d.layer("DIM", 3)
    d.layer("CENTER", 1)
    if sheet:
        d.sheet(values=values)
    cx, cy = 140.0, 160.0
    d.circle(cx, cy, od / 2 * 0.6)
    d.circle(cx, cy, bore / 2 * 0.6)
    d.circle(cx, cy, pcd / 2 * 0.6)
    for k in range(holes):
        a = 2 * math.pi * k / holes
        d.circle(cx + math.cos(a) * pcd / 2 * 0.6, cy + math.sin(a) * pcd / 2 * 0.6, hole_d / 2 * 0.6)
    # side section
    sx = cx + od * 0.6 / 2 + 50
    d.rect(sx, cy - od * 0.3, 20, od * 0.6)
    d.text(cx, cy - od * 0.3 - 12, 3.5, f"%%c{od:g} PCD {pcd:g} {holes}x%%c{hole_d:g}", "DIM", align=1)
    return d.tobytes()
