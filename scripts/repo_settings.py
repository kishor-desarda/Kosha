#!/usr/bin/env python3
# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep the GitHub repository's description, website and topics in .github/repository.json.

    python scripts/repo_settings.py --check     check the file against GitHub's rules (CI does this)
    python scripts/repo_settings.py --apply     send it to GitHub (the "Repository settings" workflow does this)

--apply needs a token that may change the repository's settings, in REPO_ADMIN_TOKEN, and the repository as
owner/name in GITHUB_REPOSITORY. Without a token it explains what to set up and changes nothing.
GitHub has no API for the social preview image; that is set once by hand (Settings > Social preview).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SETTINGS = Path(__file__).resolve().parent.parent / ".github" / "repository.json"
TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")     # GitHub: lowercase letters, digits and hyphens, at most 50
MAX_TOPICS = 20
MAX_DESCRIPTION = 350


def problems(s: dict) -> list[str]:
    out = []
    unknown = set(s) - {"description", "homepage", "topics"}
    if unknown:
        out.append(f"unknown settings: {', '.join(sorted(unknown))}")
    d = s.get("description", "")
    if not isinstance(d, str) or not d.strip():
        out.append("description is missing")
    elif len(d) > MAX_DESCRIPTION:
        out.append(f"description has {len(d)} characters; GitHub allows {MAX_DESCRIPTION}")
    h = s.get("homepage", "")
    if not isinstance(h, str) or (h and not h.startswith("https://")):
        out.append("homepage must be empty or start with https://")
    t = s.get("topics", [])
    if not isinstance(t, list) or not t:
        out.append("topics must be a list with at least one topic")
        return out
    if len(t) > MAX_TOPICS:
        out.append(f"{len(t)} topics; GitHub allows {MAX_TOPICS}")
    out += [f"topic {x!r} is not lowercase letters, digits and hyphens (at most 50)" for x in t
            if not isinstance(x, str) or not TOPIC.match(x)]
    if len(set(t)) != len(t):
        out.append("a topic is listed twice")
    return out


def load() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def _call(method: str, url: str, token: str, body: dict) -> None:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def apply(s: dict, repo: str, token: str) -> None:
    base = f"https://api.github.com/repos/{repo}"
    _call("PATCH", base, token, {"description": s["description"], "homepage": s.get("homepage", "")})
    _call("PUT", f"{base}/topics", token, {"names": s["topics"]})


def main(argv: list[str]) -> int:
    if argv not in (["--check"], ["--apply"]):
        print(__doc__)
        return 2
    s = load()
    bad = problems(s)
    for p in bad:
        print(f"::error file=.github/repository.json::{p}")
    if bad:
        return 1
    if argv == ["--check"]:
        print(f"repository.json is fine: {len(s['topics'])} topics, description {len(s['description'])} characters")
        return 0
    token, repo = os.environ.get("REPO_ADMIN_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token:
        print("::warning::Repository settings were not changed: the secret REPO_ADMIN_TOKEN is not set. Create a "
              "fine-grained token for this repository with 'Administration: Read and write' and add it under "
              "Settings > Secrets and variables > Actions.")
        return 0
    try:
        apply(s, repo, token)
    except urllib.error.HTTPError as e:
        print(f"::error::GitHub refused the change ({e.code} {e.reason}). Check that REPO_ADMIN_TOKEN has "
              "'Administration: Read and write' for this repository and has not expired. Nothing else changed.")
        return 1
    print(f"Updated the description and {len(s['topics'])} topics of {repo}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
