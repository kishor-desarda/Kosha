#!/usr/bin/env python3
# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser test of the Office task pane (web/office-addin/taskpane.html) against a Kosha server with demo data.

1. Test mode (no Office, as in a plain browser): sign in, search parts, the results render, Insert writes into the
   test document, a chosen file is checked in, then checked out and checked in again as version 2.
2. A simulated Word (a small stand-in for office.js): the document is read slice by slice, every byte reaches
   Kosha, the file handle is closed again, Insert writes at the cursor, Open in Kosha opens the file's page.

office.js is never fetched from the internet here: the test answers that request itself. The page's own
Content-Security-Policy stays on, so a policy that blocks office.js on this page makes the Word part fail.

    python scripts/test_office_taskpane.py          (needs Playwright with Chromium)
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "tests"))

from harness import DEMO_PASSWORD, KoshaServer  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

PANE = "/static/office-addin/taskpane.html"
OFFICE_JS = "https://appsforoffice.microsoft.com/**"
FOLDER = "Kosha/Products/Pumps"
DOC_SIZE = 250_123      # three slices of the stand-in's 100 000 bytes


def doc_bytes() -> bytes:
    return bytes((i * 31 + 7) % 256 for i in range(DOC_SIZE))


# A stand-in for office.js inside Word: just enough of the Office JavaScript API for the pane.
FAKE_WORD = """
(function () {
  const size = %(size)d, sliceSize = 100000;
  const bytes = new Uint8Array(size);
  for (let i = 0; i < size; i++) bytes[i] = (i * 31 + 7) %% 256;
  const log = { opened: 0, slices: 0, closed: 0, inserted: [], browser: [] };
  window.__fakeOffice = log;
  const ok = (value) => ({ status: 'succeeded', value: value });
  window.Office = {
    HostType: { Word: 'Word', Excel: 'Excel', PowerPoint: 'PowerPoint' },
    FileType: { Compressed: 'compressed' },
    CoercionType: { Text: 'text' },
    AsyncResultStatus: { Succeeded: 'succeeded', Failed: 'failed' },
    onReady: function () { return Promise.resolve({ host: 'Word', platform: 'PC' }); },
    EventType: { OfficeThemeChanged: 'officeThemeChanged' },
    context: {
      officeTheme: { bodyBackgroundColor: '#262626', bodyForegroundColor: '#FFFFFF',
                     controlBackgroundColor: '#404040', controlForegroundColor: '#FFFFFF' },   // Office "Black"
      ui: { openBrowserWindow: function (url) { log.browser.push(url); } },
      document: {
        addHandlerAsync: function (type, handler) { log.themeHandler = type; window.__officeThemeChanged = handler; },
        url: 'C:\\\\Users\\\\asha\\\\Documents\\\\MX-100 Mixer manual.doc',
        getFileAsync: function (type, options, cb) {
          log.opened++;
          const count = Math.ceil(size / sliceSize);
          cb(ok({
            size: size, sliceCount: count,
            getSliceAsync: function (i, cb2) {
              log.slices++;
              const part = bytes.slice(i * sliceSize, Math.min(size, (i + 1) * sliceSize));
              setTimeout(function () { cb2(ok({ index: i, size: part.length, data: Array.from(part) })); }, 5);
            },
            closeAsync: function (cb3) { log.closed++; if (cb3) cb3(ok()); },
          }));
        },
      },
    },
  };
  window.Word = {
    run: async function (fn) {
      const ctx = {
        document: { getSelection: function () {
          return { insertText: function (text) { log.inserted.push(text); return { select: function () {} }; } };
        } },
        sync: async function () {},
      };
      return fn(ctx);
    },
  };
})();
""" % {"size": DOC_SIZE}


def watch(page, errors: list[str]) -> None:
    """Every script error fails the test. Chromium also logs answers such as 400 (the wrong password tried on
    purpose) as console errors; the pane shows those itself, so only other console errors count."""
    def console(m) -> None:
        if m.type == "error" and "the server responded with a status of 4" not in m.text:
            errors.append(f"console {m.type}: {m.text}")

    page.on("pageerror", lambda e: errors.append(f"script error: {e}"))
    page.on("console", console)


def sign_in(page, user: str, password: str) -> None:
    expect(page.locator("#signin-view")).to_be_visible(timeout=15000)
    page.fill("#username", user)
    page.fill("#password", password)
    page.click("#signin-button")


def search_and_insert(page) -> str:
    page.fill("#search", "MX-100")
    page.click("#search-button")
    rows = page.locator("#results li")
    expect(rows.first).to_be_visible(timeout=15000)
    assert rows.count() >= 2, f"expected several MX-100 parts, got {rows.count()}"
    first_pn = rows.first.locator(".pn").inner_text().strip()
    assert first_pn.startswith("MX-100"), first_pn
    expect(rows.first.locator(".meta")).to_contain_text("Revision")
    expect(page.locator("#parts-message")).to_contain_text("parts found")
    rows.first.get_by_role("button", name=f"Insert {first_pn} into the document").click()
    expect(page.locator("#parts-message")).to_contain_text(f"Inserted {first_pn}")
    return first_pn


# The pane's background, and whether it is dark (the colour Kosha shows, not the one Office reports).
PANE_LOOK = """(() => {
  const c = (getComputedStyle(document.body).backgroundColor.match(/[\\d.]+/g) || []).map(Number);
  return { theme: document.documentElement.getAttribute('data-theme'), dark: c[0] + c[1] + c[2] < 200 };
})()"""


def test_mode(browser, kosha: KoshaServer, errors: list[str]) -> None:
    dark = browser.new_context(color_scheme="dark")      # without Office the pane follows the computer
    dark.route(OFFICE_JS, lambda route: route.fulfill(status=200, content_type="text/javascript", body="/* no Office */"))
    page = dark.new_page()
    watch(page, errors)
    page.goto(kosha.base + PANE)
    expect(page.locator("#testmode")).to_be_visible()
    look = page.evaluate(PANE_LOOK)
    assert look == {"theme": None, "dark": True}, f"test mode on a dark computer: {look}"
    dark.close()

    context = browser.new_context()
    context.route(OFFICE_JS, lambda route: route.fulfill(status=200, content_type="text/javascript", body="/* no Office here */"))
    page = context.new_page()
    watch(page, errors)
    page.goto(kosha.base + PANE)
    expect(page.locator("#testmode")).to_be_visible()
    assert page.evaluate(PANE_LOOK)["dark"] is False, "test mode on a light computer is light"

    sign_in(page, "asha", "not-the-password")
    expect(page.locator("#signin-message")).to_contain_text("Wrong user name or password")
    sign_in(page, "asha", DEMO_PASSWORD)
    expect(page.locator("#main-view")).to_be_visible(timeout=15000)
    expect(page.locator("#who")).to_contain_text("Asha")

    pn = search_and_insert(page)
    assert pn in page.locator("#test-text").input_value(), "Insert writes into the test document"

    # check-in explains what is missing, then works
    expect(page.locator("#checkin-button")).to_be_disabled()
    expect(page.locator("#checkin-why")).not_to_be_empty()
    page.select_option("#folder", FOLDER)
    page.set_input_files("#test-file", files=[{"name": "Pump test plan.xlsx", "mimeType": "application/octet-stream",
                                                "buffer": b"first issue of the test plan"}])
    expect(page.locator("#filename")).to_have_value("Pump test plan.xlsx")
    expect(page.locator("#doc-status")).to_contain_text("New in Kosha", timeout=15000)
    page.fill("#comment", "First issue")
    page.click("#checkin-button")
    expect(page.locator("#checkin-message")).to_contain_text("Checked in Pump test plan.xlsx as version 1 of revision A",
                                                             timeout=30000)

    # already in Kosha and not checked out: check out first, then a new version
    expect(page.locator("#checkin-why")).to_contain_text("Check it out first")
    expect(page.locator("#checkin-button")).to_be_disabled()
    page.click("#checkout-button")
    expect(page.locator("#doc-status")).to_contain_text("Checked out to you", timeout=15000)
    page.set_input_files("#test-file", files=[{"name": "Pump test plan.xlsx", "mimeType": "application/octet-stream",
                                                "buffer": b"second issue of the test plan"}])
    page.click("#checkin-button")
    expect(page.locator("#checkin-message")).to_contain_text("version 2 of revision A", timeout=30000)

    asha = kosha.client("asha", DEMO_PASSWORD)
    st = asha.call("GET", f"/api/addin/status?folder={FOLDER}&filename=Pump%20test%20plan.xlsx")
    assert st["exists"] and st["version"] == 2 and not st["checked_out_by"], st
    doc = asha.call("GET", f"/api/documents/{st['id']}")
    assert doc["versions"][0]["meta"]["source"] == "Office add-in (test mode)", doc["versions"][0]["meta"]
    assert doc["versions"][-1]["comment"] == "First issue"
    print("ok: test mode signs in, finds parts, inserts, checks in, checks out and checks in again")
    context.close()


def word(browser, kosha: KoshaServer, errors: list[str]) -> None:
    context = browser.new_context()
    context.route(OFFICE_JS, lambda route: route.fulfill(status=200, content_type="text/javascript", body=FAKE_WORD))
    page = context.new_page()
    watch(page, errors)
    page.goto(kosha.base + PANE)
    sign_in(page, "asha", DEMO_PASSWORD)
    expect(page.locator("#main-view")).to_be_visible(timeout=15000)
    if page.locator("#testmode").is_visible():
        raise AssertionError("The pane did not see Office: office.js did not load. Does the Content-Security-Policy of "
                             "/static/office-addin/ allow https://appsforoffice.microsoft.com?")
    # Office's theme wins over the computer's (this browser is light): Black makes the pane dark ...
    assert page.evaluate(PANE_LOOK) == {"theme": "dark", "dark": True}, page.evaluate(PANE_LOOK)
    assert page.evaluate("window.__fakeOffice.themeHandler") == "officeThemeChanged"
    # ... and switching Office to a light theme follows at once
    page.evaluate("Office.context.officeTheme.bodyBackgroundColor = '#FFFFFF'; window.__officeThemeChanged()")
    assert page.evaluate(PANE_LOOK) == {"theme": "light", "dark": False}, page.evaluate(PANE_LOOK)
    expect(page.locator("#filename")).to_have_value("MX-100 Mixer manual.docx")   # an old .doc goes in as .docx

    page.select_option("#folder", FOLDER)
    expect(page.locator("#doc-status")).to_contain_text("New in Kosha", timeout=15000)
    page.check("#keep")
    page.click("#checkin-button")
    expect(page.locator("#checkin-message")).to_contain_text("Checked in MX-100 Mixer manual.docx as version 1",
                                                             timeout=30000)
    expect(page.locator("#checkin-message")).to_contain_text("stays checked out")
    log = page.evaluate("window.__fakeOffice")
    assert log["opened"] == 1 and log["slices"] == 3 and log["closed"] == 1, log

    asha = kosha.client("asha", DEMO_PASSWORD)
    st = asha.call("GET", f"/api/addin/status?folder={FOLDER}&filename=MX-100%20Mixer%20manual.docx")
    assert st["exists"] and st["checked_out_by"], st
    stored, _ = asha.call("GET", f"/api/documents/{st['id']}/file", raw=True)
    assert hashlib.sha256(stored).hexdigest() == hashlib.sha256(doc_bytes()).hexdigest(), "every byte arrives, in order"
    assert st["latest_sha"] == hashlib.sha256(doc_bytes()).hexdigest()

    pn = search_and_insert(page)
    log = page.evaluate("window.__fakeOffice")
    assert log["inserted"] and log["inserted"][-1].startswith(pn), log["inserted"]
    page.click("#open-button")
    log = page.evaluate("window.__fakeOffice")
    assert log["browser"] and log["browser"][-1].endswith(f"/#/doc/{st['id']}"), log["browser"]
    print("ok: in Word the pane follows Office's theme, the document is read in slices, checked in byte for byte, "
          "and parts are inserted at the cursor")
    context.close()


def main() -> int:
    kosha = KoshaServer(demo=True).start()
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                test_mode(browser, kosha, errors)
                word(browser, kosha, errors)
            finally:
                browser.close()
    finally:
        kosha.stop()
    for e in errors:
        print(("::error title=Office task pane::" if os.environ.get("GITHUB_ACTIONS") else "FAIL ") + e)
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # make the reason visible as a CI annotation, not only in the log
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=Office task pane::{type(exc).__name__}: {str(exc).splitlines()[0][:300]}")
        raise
