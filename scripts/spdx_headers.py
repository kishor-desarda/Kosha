#!/usr/bin/env python3
# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check (or add, with --fix) the SPDX licence header in every source file.

The licence follows the folder: sdk/ and addins/ are Apache-2.0, packs/ is CC-BY-4.0,
everything else is AGPL-3.0-or-later.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMENT = {".py": "#", ".sh": "#", ".yml": "#", ".yaml": "#", ".toml": "#", ".js": "//", ".cs": "//",
           ".css": "/*", ".bat": "REM", ".html": "<!--", ".csproj": "<!--", ".service": "#", ".timer": "#"}
SKIP_DIRS = {"LICENSES", ".git", "node_modules", "bin", "obj", "__pycache__"}
SKIP_FILES = {"LICENSE"}


def licence_for(path: Path) -> str:
    top = path.relative_to(ROOT).parts[0]
    return {"sdk": "Apache-2.0", "addins": "Apache-2.0", "packs": "CC-BY-4.0"}.get(top, "AGPL-3.0-or-later")


def files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
        paths = [ROOT / p for p in out]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in ROOT.rglob("*") if p.is_file()]
    return [p for p in paths if p.suffix in COMMENT and p.name not in SKIP_FILES
            and not SKIP_DIRS.intersection(p.relative_to(ROOT).parts) and p.exists()]


def header(path: Path) -> list[str]:
    c = COMMENT[path.suffix]
    lic = licence_for(path)
    if c == "/*":
        return ["/* SPDX-FileCopyrightText: Kosha contributors */", f"/* SPDX-License-Identifier: {lic} */"]
    if c == "<!--":
        return [f"<!-- SPDX-License-Identifier: {lic} -->"]
    return [f"{c} SPDX-License-Identifier: {lic}"]


def main(fix: bool) -> int:
    missing = []
    for p in files():
        text = p.read_text(encoding="utf-8")
        if "SPDX-License-Identifier:" in text[:600]:
            continue
        missing.append(p)
        if fix:
            lines = text.splitlines(keepends=True)
            at = 0
            if lines and (lines[0].startswith("#!") or lines[0].lower().startswith("<!doctype") or lines[0].startswith("<?xml")
                          or lines[0].lower().startswith("@echo off") or lines[0].startswith("<Project")):
                at = 1
            new = "".join(h + "\n" for h in header(p))
            p.write_text("".join(lines[:at]) + new + "".join(lines[at:]), encoding="utf-8")
    for p in missing:
        print(("added header: " if fix else "missing SPDX header: ") + str(p.relative_to(ROOT)))
    return 0 if fix or not missing else 1


if __name__ == "__main__":
    sys.exit(main("--fix" in sys.argv))
