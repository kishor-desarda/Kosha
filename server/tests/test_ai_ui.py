# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser test of the AI screens with a fake provider: Admin › AI, "Suggest with AI" on a part, "Draft with AI"
in a change order, and Ask Kosha. Skipped when Playwright with Chromium is not installed."""
import time

import pytest

from ai_fake import FAKE, make_fake_provider
from harness import ADMIN_PASSWORD, KoshaServer

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
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("addins")
    make_fake_provider(root)
    s = KoshaServer(demo=True, env={"KOSHA_ADDIN_DIR": str(root)}).start()
    yield s
    s.stop()


@pytest.fixture(scope="module")
def browser():
    try:
        with sync_api.sync_playwright() as p:
            b = p.chromium.launch()
            yield b
            b.close()
    except Exception as e:  # no browser binary in this environment
        pytest.skip(f"Chromium is not available: {e}")


def _sign_in(browser, server, user, password):
    pg = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(f"script error: {e}"))
    pg.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    pg.goto(server.base + "/#/login")
    pg.fill("input[autocomplete=username]", user)
    pg.fill("input[type=password]", password)
    pg.keyboard.press("Enter")
    pg.wait_for_selector("nav.rail")
    return pg, errors


def test_ai_screens(browser, server):
    # --- off by default: no extra buttons anywhere, and Ask Kosha says so plainly
    asha_api = server.client("asha")
    key = next(i for i in asha_api.call("GET", "/api/items?q=MX-100-050")["items"] if i["part_number"] == "MX-100-050")
    pg, errors = _sign_in(browser, server, "asha", "demo-kosha-1")
    pg.goto(f"{server.base}/#/item/{key['id']}")
    pg.wait_for_selector("text=Where used")
    assert pg.locator("text=Suggest with AI").count() == 0 and pg.locator("text=Ask Kosha").count() == 0
    pg.goto(f"{server.base}/#/ask")
    pg.wait_for_selector("text=Ask Kosha is switched off")
    pg.close()

    # --- the admin switches it on under Admin › AI
    server.client().call("POST", f"/api/addins/{FAKE}/enable")
    adm, adm_errors = _sign_in(browser, server, "admin", ADMIN_PASSWORD)
    adm.goto(f"{server.base}/#/admin?tab=ai")
    adm.wait_for_selector("text=What may be sent")
    adm.select_option("#ai-provider", FAKE)
    for label in ("Classification", "Change-log drafts", "Ask Kosha: answer"):
        adm.locator("label.check", has_text=label).locator("input").check()
    adm.get_by_role("button", name="Save settings").click()
    adm.wait_for_selector("text=AI settings saved")
    adm.get_by_role("button", name="Test the provider").click()
    adm.wait_for_selector("text=The test request was answered")
    adm.close()
    assert adm_errors == []

    # --- Asha asks for suggestions on a part and accepts the category
    pg, errors = _sign_in(browser, server, "asha", "demo-kosha-1")
    pg.goto(f"{server.base}/#/item/{key['id']}")
    pg.get_by_role("button", name="Suggest with AI").click()
    pg.wait_for_selector("text=Review the suggestions")
    assert "category" not in asha_api.call("GET", f"/api/items/{key['id']}")["item"]["props"]   # nothing written yet
    pg.get_by_role("button", name="Accept ticked").click()
    pg.wait_for_selector("text=Accepted. The change is in the audit log.")
    assert asha_api.call("GET", f"/api/items/{key['id']}")["item"]["props"].get("category") == "SHF"

    # --- a draft change log from the AI, edited and saved
    co = next(c for c in asha_api.call("GET", "/api/changes")["changes"] if c["number"].endswith("-0001"))
    pg.goto(f"{server.base}/#/change/{co['id']}")
    pg.get_by_role("button", name="Draft with AI").click()
    _wait_until(pg, "[...document.querySelectorAll('textarea')].some(t => t.id.startsWith('cl-what') && "
                    "t.value.startsWith('- Hub seat from 110'))")
    pg.wait_for_selector("text=Check every line before you save")
    pg.get_by_role("button", name="Save change log").click()
    pg.wait_for_selector("text=Saved by Asha Menon")
    audit = server.client().call("GET", f"/api/audit?entity=change_order&entity_id={co['id']}")["entries"]
    assert any(e["action"] == "ai_accept" for e in audit)

    # --- Ask Kosha from the top bar
    pg.goto(f"{server.base}/#/home")
    pg.fill("#global-search", "Which material is the agitator shaft MX-100-020?")
    pg.get_by_role("button", name="Ask Kosha").click()
    pg.wait_for_selector("text=Records cited")
    assert pg.locator("a.ai-cite").count() >= 1
    pg.locator("a.ai-cite").first.click()
    _wait_until(pg, "/^#\\/(item|doc|change|input)\\//.test(location.hash)")
    pg.close()
    assert errors == []
