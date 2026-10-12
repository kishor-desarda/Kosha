# SPDX-License-Identifier: AGPL-3.0-or-later
"""STL helpers: read meshes uploaded by the SolidWorks add-in, compute
size/area/volume for common-part detection, and build demo meshes."""
from __future__ import annotations

import math
import struct


def read_stl(data: bytes) -> list[tuple[tuple[float, float, float], ...]]:
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + n * 50 == len(data):
            tris = []
            for k in range(n):
                off = 84 + k * 50 + 12
                v = struct.unpack("<9f", data[off:off + 36])
                tris.append(((v[0], v[1], v[2]), (v[3], v[4], v[5]), (v[6], v[7], v[8])))
            return tris
    text = data.decode("ascii", "ignore")
    verts = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("vertex"):
            parts = line.split()
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
    return [tuple(verts[i:i + 3]) for i in range(0, len(verts) - 2, 3)]


def stats(data: bytes) -> dict | None:
    try:
        tris = read_stl(data)
    except Exception:
        return None
    if not tris:
        return None
    xs, ys, zs = [], [], []
    area = 0.0
    vol = 0.0
    for a, b, c in tris:
        for p in (a, b, c):
            xs.append(p[0])
            ys.append(p[1])
            zs.append(p[2])
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        area += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
        vol += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) +
                a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    dims = sorted([max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)], reverse=True)
    return {"triangles": len(tris), "bbox": [round(d, 3) for d in dims], "area": round(area, 2),
            "volume": round(abs(vol), 2)}


def write_stl(tris, name: str = "kosha") -> bytes:
    out = bytearray(name.encode()[:80].ljust(80, b"\0"))
    out += struct.pack("<I", len(tris))
    for a, b, c in tris:
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        L = math.sqrt(sum(k * k for k in n)) or 1.0
        out += struct.pack("<12fH", n[0] / L, n[1] / L, n[2] / L, *a, *b, *c, 0)
    return bytes(out)


def _revolve(profile: list[tuple[float, float]], segments: int = 48):
    """Revolve (x, r) profile around the X axis -> triangles."""
    tris = []
    for (x0, r0), (x1, r1) in zip(profile, profile[1:]):
        for s in range(segments):
            a0 = 2 * math.pi * s / segments
            a1 = 2 * math.pi * (s + 1) / segments
            p00 = (x0, r0 * math.cos(a0), r0 * math.sin(a0))
            p01 = (x0, r0 * math.cos(a1), r0 * math.sin(a1))
            p10 = (x1, r1 * math.cos(a0), r1 * math.sin(a0))
            p11 = (x1, r1 * math.cos(a1), r1 * math.sin(a1))
            if r0 > 0:
                tris.append((p00, p10, p01))
            if r1 > 0:
                tris.append((p01, p10, p11))
    return tris


def shaft_mesh(lengths, diameters):
    prof = [(0.0, 0.0)]
    x = 0.0
    for L, D in zip(lengths, diameters):
        prof.append((x, D / 2))
        x += L
        prof.append((x, D / 2))
    prof.append((x, 0.0))
    return _revolve(prof)


def disc_mesh(od, bore, thickness, hub_d=None, hub_len=0.0):
    prof = [(0.0, bore / 2), (0.0, od / 2), (thickness, od / 2), (thickness, (hub_d or bore * 1.6) / 2)]
    if hub_len:
        prof += [(thickness + hub_len, (hub_d or bore * 1.6) / 2), (thickness + hub_len, bore / 2)]
    else:
        prof += [(thickness, bore / 2)]
    prof.append((0.0, bore / 2))
    return _revolve(prof, 64)


def box_mesh(w, d, h, ox=0.0, oy=0.0, oz=0.0):
    x0, y0, z0, x1, y1, z1 = ox, oy, oz, ox + w, oy + d, oz + h
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return [(v[a], v[b], v[c]) for a, b, c in f]


def translate(tris, dx=0.0, dy=0.0, dz=0.0):
    return [tuple((p[0] + dx, p[1] + dy, p[2] + dz) for p in t) for t in tris]
