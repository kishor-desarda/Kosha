#!/usr/bin/env python3
# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser smoke test: start Kosha with demo data, sign in, open every main screen, fail on any script error.

    python scripts/smoke_ui.py          (needs Playwright with Chromium)
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()   # parallel runs never collide
BASE = f"http://127.0.0.1:{PORT}"
SCREENS = ["#/home", "#/files", "#/boms", "#/changes", "#/erp", "#/erp?tab=reconcile", "#/erp?tab=settings", "#/common",
           "#/admin", "#/admin?tab=notify", "#/search?q=MX-100"]
ADMIN_ONLY = ("#/admin", "#/erp?tab=")


def main() -> int:
    data = tempfile.mkdtemp(prefix="kosha-smoke-")
    env = dict(os.environ, KOSHA_DATA_DIR=data, KOSHA_ADMIN_PASSWORD="admin-smoke-1", KOSHA_PORT=str(PORT),
               KOSHA_HOST="127.0.0.1", KOSHA_DISABLE_WORKER="1")
    proc = subprocess.Popen([sys.executable, "-m", "kosha", "--demo"], cwd=ROOT / "server", env=env)
    errors: list[str] = []
    try:
        for _ in range(300):
            try:
                urllib.request.urlopen(BASE + "/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.on("pageerror", lambda e: errors.append(f"script error: {e}"))
            page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
            for user, pw in (("asha", "demo-kosha-1"), ("admin", "admin-smoke-1")):
                page.goto(BASE + "/#/login")
                page.evaluate("localStorage.clear(); sessionStorage.clear()")
                page.reload()
                page.wait_for_selector("input[type=password]", timeout=15000)
                page.fill("input[autocomplete=username]", user)
                page.fill("input[type=password]", pw)
                page.keyboard.press("Enter")
                page.wait_for_selector("nav.rail", timeout=15000)
                for hash_ in SCREENS:
                    if hash_.startswith(ADMIN_ONLY) and user != "admin":
                        continue
                    page.goto(BASE + "/" + hash_)
                    try:   # each screen shows its title in the top bar once it has loaded
                        page.wait_for_function("document.querySelector('#app') && document.querySelector('#app').innerText.trim().length > 40",
                                               timeout=10000)
                    except Exception:
                        errors.append(f"{user} {hash_}: the screen stayed empty")
                    page.wait_for_load_state("networkidle")
                page.click("button.avatar")                       # the account menu opens "My notifications"
                page.click(".ctxmenu >> text=My notifications")
                try:
                    page.wait_for_selector("dialog[open] table", timeout=10000)
                except Exception:
                    errors.append(f"{user}: My notifications did not open")
                page.keyboard.press("Escape")
                print(f"ok: {user} opened {len(SCREENS)} screens and My notifications")
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        shutil.rmtree(data, ignore_errors=True)
    for e in errors:
        print(("::error title=Browser smoke test::" if os.environ.get("GITHUB_ACTIONS") else "FAIL ") + e)
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # make the reason visible as a CI annotation, not only in the log
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=Browser smoke test::{type(exc).__name__}: {str(exc).splitlines()[0][:300]}")
        raise
