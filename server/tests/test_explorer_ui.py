# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser test of adding files to an empty folder with its own "Add files" button.
Skipped when Playwright with Chromium is not installed (the CI browser job runs scripts/smoke_ui.py)."""
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    try:
        with sync_api.sync_playwright() as p:
            b = p.chromium.launch()
            yield b
            b.close()
    except Exception as e:  # no browser binary in this environment
        pytest.skip(f"Chromium is not available: {e}")


def test_add_files_button_of_an_empty_folder_uploads_into_that_folder(browser, kosha):
    eng = kosha.add_user("ella", "engineer")
    root = eng.call("GET", "/api/folders")["folders"][0]["id"]
    empty = eng.call("POST", "/api/folders", {"parent_id": root, "name": "Empty for upload"})["folder"]["id"]
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"script error: {e}"))
    page.goto(kosha.base + "/#/login")
    page.fill("input[autocomplete=username]", "ella")
    page.fill("input[type=password]", "password-123")
    page.keyboard.press("Enter")
    page.wait_for_selector("nav.rail")
    page.goto(f"{kosha.base}/#/files/{empty}")
    page.wait_for_selector("text=This folder is empty.")
    with page.expect_file_chooser() as fc:
        page.locator(".empty button", has_text="Add files").click()     # the click must not be taken for the folder
    fc.value.set_files([{"name": "PU-200-001 Impeller.dxf", "mimeType": "application/dxf", "buffer": b"0\nEOF\n"}])
    page.get_by_role("button", name="Add to Kosha").click()
    deadline = time.time() + 15
    docs = []
    while time.time() < deadline and not docs:
        docs = eng.call("GET", f"/api/folders/{empty}/contents")["documents"]
        time.sleep(0.2)
    page.close()
    assert [d["filename"] for d in docs] == ["PU-200-001 Impeller.dxf"]
    assert errors == []
