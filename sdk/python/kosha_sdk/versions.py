# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Small version-range checker for the manifest's `kosha` field (for example ">=0.5,<2.0")."""
from __future__ import annotations

import re

_PART = re.compile(r"^\s*(==|!=|>=|<=|>|<)\s*([0-9]+(?:\.[0-9]+)*)\s*$")


def parse(version: str) -> tuple[int, ...]:
    """'1.2.0.dev0' -> (1, 2, 0). Pre-release tags are ignored for range checks."""
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)*)", version or "")
    if not m:
        raise ValueError(f"not a version: {version!r}")
    nums = [int(x) for x in m.group(1).split(".")]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def check_range(spec: str) -> list[tuple[str, tuple[int, ...]]]:
    parts = []
    for raw in (spec or "").split(","):
        if not raw.strip():
            continue
        m = _PART.match(raw)
        if not m:
            raise ValueError(f"cannot read version rule {raw.strip()!r}; use forms like >=0.5 or <2.0")
        parts.append((m.group(1), parse(m.group(2))))
    if not parts:
        raise ValueError("the version range is empty")
    return parts


def satisfies(version: str, spec: str) -> bool:
    v = parse(version)
    for op, target in check_range(spec):
        ok = {"==": v == target, "!=": v != target, ">=": v >= target, "<=": v <= target,
              ">": v > target, "<": v < target}[op]
        if not ok:
            return False
    return True
