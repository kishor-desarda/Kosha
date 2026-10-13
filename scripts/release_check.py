#!/usr/bin/env python3
# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check that the repository is ready to be released, before a tag builds anything.

    python scripts/release_check.py               check the version, the changelog and the add-ins
    python scripts/release_check.py --tag v1.0.0  also check that the tag names the server's version

It checks that the server's version is a valid release number, that CHANGELOG.md has a section for it, that every
official add-in's "kosha" range accepts it, and that the user and admin guides exist. The release workflow
(.github/workflows/release.yml) runs it first; it prints every problem, not only the first.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from kosha_sdk.versions import satisfies  # noqa: E402

# PEP 440 subset Kosha uses: 1.0.0, 1.0.0rc1, 1.0.1.dev0
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?(?:\.dev(\d+))?$")
GUIDES = ("docs/user-guide.md", "docs/admin-guide.md")


def server_version(root: Path = ROOT) -> str:
    text = (root / "server" / "kosha" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("server/kosha/__init__.py has no __version__ line")
    return m.group(1)


def changelog_versions(root: Path = ROOT) -> list[str]:
    path = root / "CHANGELOG.md"
    if not path.exists():
        return []
    return re.findall(r"^## \[?v?([0-9][^\]\s]*)\]?", path.read_text(encoding="utf-8"), re.M)


def addin_ranges(root: Path = ROOT) -> dict[str, str]:
    out = {}
    for manifest in sorted((root / "addins").glob("*/kosha-addin.yaml")):
        m = re.search(r'^kosha:\s*"?([^"\n]+)"?\s*$', manifest.read_text(encoding="utf-8"), re.M)
        out[manifest.parent.name] = m.group(1).strip() if m else ""
    return out


def problems(root: Path = ROOT, tag: str | None = None) -> list[str]:
    out = []
    version = server_version(root)
    if not VERSION.match(version):
        out.append(f"The server version {version!r} is not a release number like 1.0.0 or 1.0.0rc1.")
    elif ".dev" in version:
        out.append(f"The server version {version} is a development version; set the release number first.")
    if tag is not None and tag.removeprefix("v") != version:
        out.append(f"The tag {tag} does not match the server version {version}; tag v{version} instead.")
    if version not in changelog_versions(root):
        out.append(f"CHANGELOG.md has no section '## [{version}]'; describe what changed in this release.")
    for name, spec in addin_ranges(root).items():
        if not spec:
            out.append(f"addins/{name}/kosha-addin.yaml has no 'kosha' version range.")
            continue
        try:
            ok = satisfies(version, spec)
        except ValueError as e:
            out.append(f"addins/{name}/kosha-addin.yaml: {e}")
            continue
        if not ok:
            out.append(f"The {name} add-in says it works with Kosha {spec}, which leaves out {version}.")
    for guide in GUIDES:
        if not (root / guide).exists():
            out.append(f"{guide} is missing.")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", help="the git tag being released, for example v1.0.0")
    args = ap.parse_args(argv)
    found = problems(tag=args.tag)
    for p in found:
        print(f"::error::{p}" if "GITHUB_ACTIONS" in os.environ else f"- {p}")
    if found:
        print(f"Not ready to release: {len(found)} problem(s).")
        return 1
    print(f"Ready to release Kosha {server_version()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
