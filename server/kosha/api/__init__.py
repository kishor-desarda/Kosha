# SPDX-License-Identifier: AGPL-3.0-or-later
"""API modules. Every module in this package registers its routes when imported; ``load_all`` imports them all,
so a new feature adds a new module here and nothing else needs to change."""
from __future__ import annotations

import importlib
import pkgutil


def load_all() -> list[str]:
    names = sorted(m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_"))
    for name in names:
        importlib.import_module(f"{__name__}.{name}")
    return names
