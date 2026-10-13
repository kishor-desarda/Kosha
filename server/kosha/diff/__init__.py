# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measured change list (spec 7.7): the facts that differ between two versions of a file.

    from kosha.diff import engine, Side
    diff = engine().compare(Side.of_bytes("a.dxf", old), Side.of_bytes("a.dxf", new))
    for c in diff.changes:
        print(c.severity_hint, c.text)      # "Material changed from C45 to 42CrMo4"

Add a comparer for a new file type by subclassing ``Comparer`` and calling ``engine().register(MyComparer)``
(an add-in can do the same at load time).
"""
from __future__ import annotations

from .core import Change, Comparer, Diff, DiffEngine, Side, render

__all__ = ["Change", "Comparer", "Diff", "DiffEngine", "Side", "engine", "render"]

_ENGINE: DiffEngine | None = None


def engine() -> DiffEngine:
    """The shared engine with Kosha's built-in comparers registered by file type."""
    global _ENGINE
    if _ENGINE is None:
        from .bom import BomComparer
        from .drawing import DrawingComparer, DwgComparer
        from .model import ModelComparer
        from .props import PropertiesComparer
        from .text import TextComparer
        e = DiffEngine()
        for cls in (DrawingComparer, DwgComparer, ModelComparer, TextComparer, BomComparer, PropertiesComparer):
            e.register(cls)
        _ENGINE = e
    return _ENGINE
