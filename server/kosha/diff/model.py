# SPDX-License-Identifier: AGPL-3.0-or-later
"""3D models: size, volume, area and mass from the mesh, plus CAD custom properties and components.

STL meshes are measured from the file (or the figures stored at check-in). SolidWorks files are
measured from the mesh and properties the Kosha add-in uploads at check-in; Kosha cannot open the
SolidWorks file itself.
"""
from __future__ import annotations

import re

from ..cad import mesh
from .core import Change, Comparer, Side, fmt

MASS_KEYS = re.compile(r"^(mass|weight|weight_kg|wt|gewicht)$", re.I)
SKIP_PROPS = re.compile(r"^(sw-|\$prp)", re.I)


def mesh_stats(side: Side) -> dict | None:
    st = (side.meta or {}).get("mesh")
    if st:
        return st
    if side.ext == "stl" and side.has_content:
        return mesh.stats(side.data())
    return None


def _number(v) -> float | None:
    if v is None:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v))
    return float(m.group(0).replace(",", ".")) if m else None


class ModelComparer(Comparer):
    name = "model"
    label = "3D model comparison"
    exts = ("stl", "sldprt", "sldasm", "step", "stp", "igs", "iges", "x_t")

    def compare(self, a: Side, b: Side) -> list[Change]:
        out: list[Change] = []
        sa, sb = mesh_stats(a), mesh_stats(b)
        if sa and sb:
            out += self.measures(sa, sb)
        elif a.ext not in ("stl",) and not (sa or sb):
            self.note("No 3D figures for these versions: check them in with the Kosha CAD add-in to compare "
                      "size, volume and mass.")
        out += self.properties((a.meta or {}).get("properties") or {}, (b.meta or {}).get("properties") or {})
        out += self.components(a.meta or {}, b.meta or {}, a, b)
        return out

    def measures(self, sa: dict, sb: dict) -> list[Change]:
        out = []
        ba, bb = sa.get("bbox") or [], sb.get("bbox") or []
        if ba and bb and any(abs(x - y) > 1e-3 * max(abs(x), abs(y), 1) for x, y in zip(ba, bb)):
            out.append(Change("model_size", "Model", " × ".join(fmt(v, 1) for v in ba), " × ".join(fmt(v, 1) for v in bb),
                              "major", None, {"from": ba, "to": bb}))
        for key, kind, div, unit in (("volume", "volume", 1000.0, "cm³"), ("area", "area", 100.0, "cm²")):
            va, vb = sa.get(key), sb.get(key)
            if va is None or vb is None:
                continue
            if abs(va - vb) > 1e-4 * max(abs(va), abs(vb), 1):
                out.append(Change(kind, "Model", round(va / div, 1), round(vb / div, 1),
                                  "major" if kind == "volume" else "minor", None, {"unit": unit}))
        return out

    def properties(self, pa: dict, pb: dict) -> list[Change]:
        out = []
        ka = {str(k).strip().lower(): (str(k).strip(), v) for k, v in pa.items() if not SKIP_PROPS.match(str(k))}
        kb = {str(k).strip().lower(): (str(k).strip(), v) for k, v in pb.items() if not SKIP_PROPS.match(str(k))}
        for low in dict.fromkeys(list(ka) + list(kb)):
            k = (kb.get(low) or ka.get(low))[0]
            va = ka.get(low, ("", None))[1]
            vb = kb.get(low, ("", None))[1]
            if str(va if va is not None else "").strip() == str(vb if vb is not None else "").strip():
                continue
            if MASS_KEYS.match(k):
                na, nb = _number(va), _number(vb)
                if na is not None and nb is not None:
                    out.append(Change("mass", "Model", na, nb, "minor", None, {"unit": "kg", "property": k}))
                    continue
            sev = "major" if k.lower() in ("material", "partno", "part number", "part_number") else "minor"
            out.append(Change("custom_property", "CAD properties", va if va not in ("",) else None,
                              vb if vb not in ("",) else None, sev, None, {"label": k}))
        return out

    def components(self, ma: dict, mb: dict, a: Side, b: Side) -> list[Change]:
        """Assembly structure from the add-in, or referenced file names, when there is no BOM to compare."""
        if a.bom_lines and b.bom_lines:
            return []
        out = []
        sa, sb = ma.get("structure"), mb.get("structure")
        if isinstance(sa, list) and isinstance(sb, list):
            def key(c):
                return str(c.get("part_number") or c.get("file") or "").strip().upper()
            qa = {}
            qb = {}
            for c in sa:
                qa[key(c)] = qa.get(key(c), 0) + float(c.get("qty") or 1)
            for c in sb:
                qb[key(c)] = qb.get(key(c), 0) + float(c.get("qty") or 1)
            for k in qb:
                if k and k not in qa:
                    out.append(Change("component", "Assembly", None, k, "major", None, {"qty": qb[k]}))
            for k in qa:
                if k and k not in qb:
                    out.append(Change("component", "Assembly", k, None, "major", None, {"qty": qa[k]}))
            for k in qa:
                if k in qb and abs(qa[k] - qb[k]) > 1e-9:
                    out.append(Change("bom_qty", "Assembly", qa[k], qb[k], "major", None, {"part_number": k}))
            return out
        ra, rb = ma.get("references"), mb.get("references")
        if isinstance(ra, list) and isinstance(rb, list):
            la, lb = {r.lower(): r for r in ra}, {r.lower(): r for r in rb}
            out += [Change("component", "Assembly", None, lb[k], "major") for k in lb if k not in la]
            out += [Change("component", "Assembly", la[k], None, "major") for k in la if k not in lb]
        return out
