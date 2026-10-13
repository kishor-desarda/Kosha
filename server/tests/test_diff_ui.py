# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser test of the measured change list, the change-log editor and the copy design dialog.
Skipped when Playwright with Chromium is not installed (the CI browser job runs scripts/smoke_ui.py)."""
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def _wait_until(page, expression, timeout=15.0):
    """Poll an expression (page.wait_for_function uses eval in the page, which Kosha's CSP refuses)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if page.evaluate(expression):
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for: {expression}")


@pytest.fixture(scope="module")
def browser():
    try:
        with sync_api.sync_playwright() as p:
            b = p.chromium.launch()
            yield b
            b.close()
    except Exception as e:  # no browser binary in this environment
        pytest.skip(f"Chromium is not available: {e}")


@pytest.fixture
def page(browser, demo_kosha):
    pg = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(f"script error: {e}"))
    pg.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    pg.goto(demo_kosha.base + "/#/login")
    pg.fill("input[autocomplete=username]", "asha")
    pg.fill("input[type=password]", "demo-kosha-1")
    pg.keyboard.press("Enter")
    pg.wait_for_selector("nav.rail")
    yield pg
    pg.close()
    assert errors == []


def test_change_log_tab_viewer_changes_and_change_order(page, demo_kosha):
    base = demo_kosha.base
    asha = demo_kosha.client("asha")
    shaft = next(d for d in asha.call("GET", "/api/search?q=MX-100-020")["documents"] if d["ext"] == "dxf")
    page.goto(f"{base}/#/doc/{shaft['id']}")
    page.get_by_role("tab", name="Change log").click()
    page.wait_for_selector("text=Revision D has no checked-in version yet")
    page.select_option("select[aria-label=Revision]", "C")
    page.wait_for_selector("text=Diameter Ø105 changed to Ø110")
    assert page.get_by_role("button", name="Save change log").is_disabled()        # released: approvers only, and it says so
    page.wait_for_selector("text=Revision C is released; only an approver can change its change log.")
    page.select_option("select[aria-label=Revision]", "D")
    page.fill("textarea[placeholder^='In one or two']", "Fretting at the hub seat.")
    page.get_by_role("button", name="Build draft").click()
    _wait_until(page, "[...document.querySelectorAll('textarea')].some(t => t.value === 'Fretting at the hub seat.' && t.id.startsWith('cl-why'))")
    page.get_by_role("button", name="Save change log").click()
    page.wait_for_selector("text=Saved by Asha Menon")

    page.get_by_role("button", name="View").first.click()
    page.wait_for_selector(".viewer .svgwrap svg")
    page.select_option("select[aria-label='Version']", "3")
    page.wait_for_timeout(200)
    page.select_option("select[aria-label='Compare with version']", "1")
    panel = page.locator("aside[aria-label='Measured changes']")
    panel.locator("text=Material changed from C45 to 42CrMo4").wait_for()
    before = page.eval_on_selector(".viewer .svgwrap svg:last-child", "e => e.getAttribute('viewBox')")
    panel.locator("button", has_text="Diameter Ø100 changed to Ø110").click()
    after = page.eval_on_selector(".viewer .svgwrap svg:last-child", "e => e.getAttribute('viewBox')")
    assert before != after and float(after.split()[2]) < float(before.split()[2]) / 3      # zoomed in on the change
    assert page.locator(".viewer .svgwrap svg:first-child rect").count() == 1               # the change zone is marked
    page.keyboard.press("Escape")

    co = next(c for c in asha.call("GET", "/api/changes")["changes"] if c["number"].endswith("-0001"))
    page.goto(f"{base}/#/change/{co['id']}")
    page.wait_for_selector("text=No version of revision B has been checked in yet.")
    _wait_until(page, "[...document.querySelectorAll('textarea')].some(t => t.value.startsWith('Field report: fretting'))")


def test_explorer_offers_copy_design_on_models(page, demo_kosha):
    asm = next(d for d in demo_kosha.client("asha").call("GET", "/api/search?q=MX-100-000")["documents"] if d["ext"] == "stl")
    page.goto(f"{demo_kosha.base}/#/files/{asm['folder_id']}")
    page.locator(".ex-list tr", has_text="MX-100-000 Agitator Assembly.stl").click(button="right")
    item = page.get_by_role("menuitem", name="Copy design…")
    assert item.is_enabled()
    item.click()
    page.wait_for_selector("dialog h2:has-text('Copy design: MX-100-000 AGITATOR ASSEMBLY')")
    page.keyboard.press("Escape")
    page.locator(".ex-list tr", has_text="MX-100-020 Agitator Shaft.dxf").click(button="right")
    assert page.get_by_role("menuitem", name="Copy design…").count() == 0      # drawings are copied with their part


def test_copy_design_dialog(page, demo_kosha):
    asm = next(i for i in demo_kosha.client("asha").call("GET", "/api/items?q=MX-100-000")["items"])
    page.goto(f"{demo_kosha.base}/#/item/{asm['id']}")
    page.get_by_role("button", name="Copy design…").click()
    nxt = page.get_by_role("button", name="Next: review the parts")
    assert nxt.is_disabled() and nxt.get_attribute("title") == "Type the prefix for the new part numbers first."
    page.fill("#cd-prefix", "MX-300-")
    page.fill("#cd-newfolder", "MX-VA-3000 Vertical Agitator")
    nxt.click()
    page.wait_for_selector("text=STD-BRG-22220")
    page.select_option("select[aria-label='Action for MX-100-031']", "reuse")
    page.get_by_role("button", name="Copy 5 parts").wait_for()
    page.get_by_role("button", name="Copy 5 parts").click()
    page.wait_for_selector("text=5 part(s) copied into Kosha › Products › Mixers › MX-VA-3000 Vertical Agitator")
    page.get_by_role("link", name="Open the new assembly").click()
    page.wait_for_selector("h1:has-text('MX-300-001')")
