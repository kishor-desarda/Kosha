# SPDX-License-Identifier: AGPL-3.0-or-later
"""Item properties: description, material, weight, unit, make or buy, type.

``Side.props`` holds the properties as they were for that version: the snapshot taken when its
revision was released (see ``snapshots.py``), or the item as it is now for work in progress.
"""
from __future__ import annotations

from .core import Change, Comparer, Side, fmt

FIELDS = (
    ("description", "Description", "minor", ""),
    ("material", "Material", "major", ""),
    ("weight_kg", "Weight", "minor", "kg"),
    ("uom", "Unit of measure", "minor", ""),
    ("make_buy", "Make or buy", "major", ""),
    ("item_type", "Item type", "minor", ""),
    ("item_group", "Item group", "info", ""),
)


def _val(v):
    if v is None or v == "":
        return None
    if isinstance(v, float):
        return fmt(v)
    return str(v).strip() or None


def compare_props(pa: dict, pb: dict) -> list[Change]:
    out = []
    for key, label, sev, unit in FIELDS:
        if key not in pa and key not in pb:
            continue
        va, vb = _val(pa.get(key)), _val(pb.get(key))
        if (va or "") == (vb or ""):
            continue
        out.append(Change("property", "Part", va, vb, sev, None, {"label": label, "field": key, "unit": unit}))
    return out


class PropertiesComparer(Comparer):
    name = "properties"
    label = "Part properties"
    exts = ("*",)

    def applies(self, a: Side, b: Side) -> bool:
        return a.props is not None and b.props is not None

    def compare(self, a: Side, b: Side) -> list[Change]:
        return compare_props(a.props or {}, b.props or {})
