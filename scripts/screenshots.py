#!/usr/bin/env python3
# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regenerate docs/screenshots from the Demo Engineering data set (needs Playwright with Chromium).

    python scripts/screenshots.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
PORT = 18555
BASE = f"http://127.0.0.1:{PORT}"


def wait_up():
    for _ in range(200):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=1)
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("server did not start")


def main() -> int:
    data = tempfile.mkdtemp(prefix="kosha-shots-")
    env = dict(os.environ, KOSHA_DATA_DIR=data, KOSHA_ADMIN_PASSWORD="admin-demo-1", KOSHA_PORT=str(PORT),
               KOSHA_HOST="127.0.0.1")
    proc = subprocess.Popen([sys.executable, "-m", "kosha", "--demo"], cwd=ROOT / "server", env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        wait_up()
        OUT.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page = ctx.new_page()

            def shot(name, settle=900):
                page.wait_for_timeout(settle)
                page.screenshot(path=str(OUT / f"{name}.png"))
                print("saved", name)

            def login(user, pw):
                page.goto(BASE + "/#/login")
                page.evaluate("localStorage.clear(); sessionStorage.clear()")
                page.reload()
                page.wait_for_selector("input[type=password]")
                page.fill("input[autocomplete=username]", user)
                page.fill("input[type=password]", pw)
                page.keyboard.press("Enter")
                page.wait_for_selector("nav.rail")

            page.goto(BASE + "/#/login")
            page.wait_for_selector("input[type=password]")
            shot("00-sign-in")

            login("asha", "demo-kosha-1")
            page.goto(BASE + "/#/home")
            shot("01-home-guided")
            page.goto(BASE + "/#/files")
            page.wait_for_selector("text=Products")
            page.click("text=Products")
            page.wait_for_timeout(500)
            page.dblclick("text=Mixers")
            page.wait_for_timeout(500)
            page.dblclick("text=MX-VA-1200 Vertical Agitator")
            shot("02-explorer-guided", 1500)
            row = page.locator("text=MX-100-020 Agitator Shaft.dxf").first
            row.click(button="right")
            shot("03-right-click-menu", 600)
            page.keyboard.press("Escape")

            login("daniel", "demo-kosha-1")
            page.goto(BASE + "/#/files")
            page.wait_for_timeout(600)
            page.dblclick("text=Products")
            page.wait_for_timeout(400)
            page.dblclick("text=Mixers")
            page.wait_for_timeout(400)
            page.dblclick("text=MX-VA-1200 Vertical Agitator")
            shot("04-explorer-expert", 1500)
            page.goto(BASE + "/#/boms")
            page.wait_for_timeout(800)
            page.locator("text=MX-100-000").first.click()
            shot("07-engineering-bom", 1500)
            page.goto(BASE + "/#/changes")
            page.wait_for_timeout(800)
            page.locator("text=Increase agitator shaft hub seat").first.click()
            shot("08-change-order", 1500)
            page.goto(BASE + "/#/common")
            shot("09-duplicates", 1800)
            page.goto(BASE + "/#/erp")
            shot("10-erp-sync", 1200)

            login("admin", "admin-demo-1")
            page.goto(BASE + "/#/admin?tab=addins")
            page.wait_for_timeout(600)
            page.locator("button[role=tab]", has_text="Add-ins").click()
            shot("11-admin-addins", 1200)
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        shutil.rmtree(data, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
