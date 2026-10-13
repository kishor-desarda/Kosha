# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser test of directory sign-in: the "Sign in with" button through a test OpenID Connect provider, directory
passwords in the normal form, the admin's Sign-in & sessions screen and the Users › Directory dialog.
Skipped when Playwright with Chromium is not installed (the CI browser job runs scripts/smoke_ui.py)."""
import sys
import time
from pathlib import Path

import pytest

from erp_helpers import serve

sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "addins" / "oidc" / "tests", REPO / "addins" / "oidc", REPO / "addins" / "ldap" / "tests",
          REPO / "addins" / "ldap"):
    if str(p) not in sys.path:
        sys.path.append(str(p))

import fake_ldap as FL  # noqa: E402
import fake_oidc as FO  # noqa: E402


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


@pytest.fixture(scope="module")
def configured(kosha):
    server, url = serve(FO.app)
    FO.reset(url)
    ldap = FL.FakeLdap(FL.ad_entries(), FL.passwords()).start()
    admin = kosha.client()
    admin.call("PUT", "/api/addins/org.kosha.oidc/settings", {"issuer": FO.issuer(), "client_id": FO.CLIENT_ID,
                                                              "client_secret": FO.CLIENT_SECRET,
                                                              "label": "Demo Engineering account"})
    admin.call("PUT", "/api/addins/org.kosha.ldap/settings", {
        "server_url": ldap.url, "base_dn": FL.AD_BASE, "bind_dn": f"CN=svc-kosha,OU=Service,{FL.AD_BASE}",
        "bind_password": FL.SERVICE_PASSWORD, "starttls": False, "timeout_s": 3, "label": "Demo Engineering network"})
    admin.call("PUT", "/api/admin/directory", {"group_roles": [{"group": "kosha-engineers", "role": "engineer"},
                                                               {"group": "Kosha Approvers", "role": "approver"}]})
    yield kosha
    ldap.stop()
    server.should_exit = True


@pytest.fixture
def page(browser):
    pg = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(f"script error: {e}"))
    pg.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" and "status of 4" not in m.text else None)
    yield pg
    pg.close()
    assert errors == []


def test_sign_in_with_button_and_directory_password(configured, page):
    base = configured.base
    page.goto(base + "/#/login")
    button = page.get_by_role("link", name="Sign in with Demo Engineering account")
    button.wait_for()
    page.wait_for_selector("text=Use your Demo Engineering network user name and password")
    button.click()                                          # Kosha → provider → back to Kosha with a ticket
    page.wait_for_selector("nav.rail")
    assert page.evaluate("location.hash") != "" and "/sso/" not in page.evaluate("location.href")
    page.click("button.avatar")
    page.wait_for_selector(".ctxmenu >> text=Asha Rao (engineer)")
    page.click(".ctxmenu >> text=Sign out")
    page.wait_for_selector("text=Sign in with Demo Engineering account")

    page.fill("input[autocomplete=username]", "daniel")     # an LDAP account through the normal form
    page.fill("input[type=password]", FL.PASSWORD)
    page.keyboard.press("Enter")
    page.wait_for_selector("nav.rail")
    page.click("button.avatar")
    page.wait_for_selector(".ctxmenu >> text=Daniel Okafor (approver)")


def test_failed_round_trips_explain_themselves(configured, page):
    page.goto(configured.base + "/#/sso-error/expired")
    page.wait_for_selector("text=This sign-in has expired or was started in another browser window")
    assert page.evaluate("location.hash") == "#/login"
    FO.STATE["user"] = "tom"                                # in no group that gives access
    page.get_by_role("link", name="Sign in with Demo Engineering account").click()
    page.wait_for_selector("text=you are not in a group that gives access to Kosha")
    FO.STATE["user"] = "asha"


def test_admin_screens(configured, page):
    page.goto(configured.base + "/#/login")
    page.fill("input[autocomplete=username]", "admin")
    page.fill("input[type=password]", "admin-test-pass-1")
    page.keyboard.press("Enter")
    page.wait_for_selector("nav.rail")
    page.goto(configured.base + "/#/admin?tab=security")
    page.wait_for_selector("text=Groups and Kosha roles")
    page.wait_for_selector(f"text={configured.base}/api/auth/sso/org.kosha.oidc/callback")
    _wait_until(page, "[...document.querySelectorAll('input')].some(i => i.value === 'Kosha Approvers')")
    page.fill("#dir-chk-u", "asha")
    page.fill("#dir-chk-pw", FL.PASSWORD)
    page.get_by_role("button", name="Check account").click()
    page.wait_for_selector("text=Groups: Kosha Engineers")
    page.wait_for_selector("text=Kosha role: none, so this person cannot sign in")    # only kosha-engineers (OIDC) is listed
    page.get_by_role("button", name="Add group").click()
    page.locator("input[aria-label='Directory group']").last.fill("Kosha Engineers")
    page.get_by_role("button", name="Save groups and roles").click()
    page.wait_for_selector("text=Saved. The roles apply from each person's next sign-in.")

    page.goto(configured.base + "/#/admin?tab=users")
    page.locator("tr:has(td.mono:text-is('admin'))").get_by_role("button", name="Directory").click()
    page.wait_for_selector("text=The built-in admin account stays a local account")
    assert page.get_by_role("button", name="Link", exact=True).is_disabled()
    page.keyboard.press("Escape")
    page.locator("tr:has(td.mono:text-is('asha'))").get_by_role("button", name="Directory").click()
    page.wait_for_selector("dialog[open] >> text=Linked, last sign-in")
