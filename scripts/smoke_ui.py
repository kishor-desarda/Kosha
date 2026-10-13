#!/usr/bin/env python3
# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser smoke test: start Kosha with demo data, sign in, open every main screen, fail on any script error.

Every screen is opened twice per user: in the light theme and in the dark one (asha chooses Dark in the account
menu; admin keeps "Same as my computer" on a computer set to dark). In dark, a screen fails when the page is not
dark or when something still has a plain white background (a colour that bypasses the variables in app.css).

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
SCREENS = ["#/home", "#/files", "#/boms", "#/changes", "#/erp", "#/common", "#/admin", "#/admin?tab=backups",
           "#/admin?tab=security", "#/search?q=MX-100", "#/library", "#/library?q=M16x60",
           "#/admin?tab=packs", "#/deviations", "#/deviation/1", "#/inputs", "#/inputs?q=bearing",
           "#/input/1", "#/projects", "#/project/1", "#/item/1", "#/erp?tab=reconcile", "#/erp?tab=settings",
           "#/admin?tab=notify", "#/ask", "#/admin?tab=ai", "#/admin?tab=access",
           "#/ecrs", "#/ecr/1", "#/change/1", "#/admin?tab=approvals",
           "#/admin?tab=webhooks", "#/bin", "#/shares", "#/doc/1?tab=outputs",
           "#/machines", "#/bom-view/1?view=in_work&cost=1", "#/transmittals", "#/erp?tab=orders",
           "#/erp?tab=mapping", "#/shop", "#/shop?pn=MX-100-020"]
ADMIN_ONLY = ("#/admin", "#/erp?tab=")


def wait_until(page, expression: str, timeout: float = 10.0) -> bool:
    """Poll a JavaScript expression. (page.wait_for_function evaluates its predicate with eval inside the page,
    which Kosha's Content-Security-Policy rightly refuses.)"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.evaluate(expression):
                return True
        except Exception:
            pass    # the page is navigating; try again
        time.sleep(0.1)
    return False


# In the dark theme: is the page dark, and which elements still have a white background (at most 5)?
DARK_CHECK = """(() => {
  const rgb = c => (c.match(/[\\d.]+/g) || []).map(Number);
  const [r, g, b] = rgb(getComputedStyle(document.body).backgroundColor);
  const name = e => e.tagName.toLowerCase() + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
  const white = [...document.querySelectorAll('.app *, dialog[open] *')]
    .filter(e => !['IMG', 'svg', 'CANVAS', 'IFRAME', 'VIDEO'].includes(e.tagName))
    .filter(e => { const c = rgb(getComputedStyle(e).backgroundColor); return c.length >= 3 && c[0] > 245 && c[1] > 245 && c[2] > 245 && (c[3] === undefined || c[3] > 0.5); })
    .map(name).slice(0, 5);
  return { dark: r + g + b < 200, theme: document.documentElement.dataset.theme || '', white };
})()"""


# Counts the page's calls to the server still under way (the live event stream is not a fetch, so not counted).
PENDING_FETCHES = """(() => {
  window.__koshaPending = 0;
  const orig = window.fetch;
  window.fetch = (...a) => { window.__koshaPending++; return orig(...a).finally(() => { window.__koshaPending--; }); };
})()"""


def visit_screens(page, user: str, errors: list[str], dark: bool) -> None:
    label = f"{user} (dark)" if dark else user
    for hash_ in SCREENS:
        if hash_.startswith(ADMIN_ONLY) and user != "admin":
            continue
        page.goto(BASE + "/" + hash_)
        if not wait_until(page, "document.querySelector('#page') && "
                                "document.querySelector('#page').innerText.trim().length > 20"):
            errors.append(f"{label} {hash_}: the screen stayed empty")
        # "networkidle" never comes: the live updates connection stays open. Wait for the page's own calls instead.
        if not wait_until(page, "window.__koshaPending === 0"):
            errors.append(f"{label} {hash_}: calls to the server did not finish")
        page.wait_for_timeout(300)
        if dark:
            r = page.evaluate(DARK_CHECK)
            if not r["dark"]:
                errors.append(f"{label} {hash_}: the page is not dark (theme {r['theme']!r})")
            if r["white"]:
                errors.append(f"{label} {hash_}: white background in the dark theme: {', '.join(r['white'])}")


RESUMABLE = """async () => {
  const core = await import('/static/js/core.js');
  const folders = (await core.get('/api/folders')).folders;
  const top = folders.find(f => !f.parent_id).id;
  const bytes = new Uint8Array(9 * 1024 * 1024 + 123);             // two pieces of 8 MB
  for (let i = 0; i < bytes.length; i++) bytes[i] = (i * 31 + 7) % 251;
  const file = new File([bytes], 'Smoke resumable.bin');
  let last = 0;
  const doc = await core.resumableUpload(file, { folder_id: top }, p => { last = p; });
  const back = new Uint8Array(await (await fetch(core.fileUrl(doc.id, 'file'))).arrayBuffer());
  const same = back.length === bytes.length && back.every((b, i) => b === bytes[i]);
  return { same, version: doc.version, last };
}"""


MAKE_SHARE = """async () => {
  const core = await import('/static/js/core.js');
  const doc = (await core.get('/api/documents?state=released&limit=1')).documents[0];
  const r = await core.post('/api/shares', { document_ids: [doc.id], recipient: 'Smoke test, Example Castings', days: 3 });
  return { token: r.token, filename: doc.filename };
}"""


def check_share_page(browser, page, errors: list[str]) -> None:
    """A share link opens in a browser that never signed in, and lists the shared file."""
    made = page.evaluate(MAKE_SHARE)
    outside = browser.new_page()
    outside.on("pageerror", lambda e: errors.append(f"share page script error: {e}"))
    outside.goto(f"{BASE}/#/share/{made['token']}")
    if not wait_until(outside, f"document.body.innerText.includes({made['filename']!r})"):
        errors.append("the share link page did not list the shared file for someone who is not signed in")
    outside.close()


def check_drafts(page, errors: list[str]) -> None:
    """Unsaved property edits survive a reload and are offered again."""
    doc_id = page.evaluate("""async () => {
  const core = await import('/static/js/core.js');
  const doc = (await core.get('/api/documents?state=in_work&limit=50')).documents.find(d => !d.checked_out_by);
  await core.post(`/api/documents/${doc.id}/checkout`);
  return doc.id;
}""")
    page.goto(f"{BASE}/#/doc/{doc_id}")
    page.wait_for_selector("#f-desc:not([disabled])", timeout=15000)
    page.fill("#f-desc", "SMOKE DRAFT DESCRIPTION")
    time.sleep(0.8)                                   # the draft is kept 0.4 s after the last key
    page.reload()
    try:
        page.wait_for_selector(".draft-banner >> text=Restore them", timeout=15000)
        page.click(".draft-banner >> text=Restore them")
        if page.input_value("#f-desc") != "SMOKE DRAFT DESCRIPTION":
            errors.append("restoring the kept draft did not put the description back")
    except Exception:
        errors.append("unsaved property edits were not offered again after reloading the page")
    page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('kosha.draft.')).forEach(k => localStorage.removeItem(k))")


def check_workbench(page, errors: list[str]) -> None:
    """Ctrl+K finds a part and opens it; the star makes it a favourite; a pinned page gets its tab; text size grows."""
    page.goto(f"{BASE}/#/home")
    page.wait_for_selector("#go-to", timeout=15000)
    page.keyboard.press("Control+k")
    try:
        page.wait_for_selector("dialog.palette[open] .palette-q", timeout=10000)
        page.fill("dialog.palette .palette-q", "MX-100")
        page.wait_for_selector("dialog.palette .palette-group >> text=Parts", timeout=10000)
        page.click("xpath=//dialog[contains(@class,'palette')]//div[@class='palette-group' and text()='Parts']"
                   "/following-sibling::div[1]")
        if not wait_until(page, "location.hash.startsWith('#/item/') || location.hash.startsWith('#/doc/')"):
            errors.append("choosing a part in Ctrl+K did not open it")
    except Exception as e:
        errors.append(f"Ctrl+K did not find and open a part: {e}")
        page.keyboard.press("Escape")
        return
    try:
        page.click("h1 .fav-btn")
        if not wait_until(page, "document.querySelector('h1 .fav-btn').getAttribute('aria-pressed') === 'true'"):
            errors.append("the star did not make the record a favourite")
        page.keyboard.press("Control+k")
        page.wait_for_selector("dialog.palette .palette-group >> text=Favourites", timeout=10000)
        page.click("dialog.palette .palette-item >> text=Pin this page")
        page.wait_for_selector(".pin-strip:not([hidden]) .pin-tab", timeout=10000)
        page.click(".pin-strip .pin-tab button")                       # unpin; the toast offers Undo
        page.wait_for_selector(".toast >> text=Undo", timeout=10000)
        page.click("h1 .fav-btn")                                       # and remove the favourite again
    except Exception as e:
        errors.append(f"favourites or pinned pages did not work: {e}")
    page.evaluate("""async () => { const w = await import('/static/js/workbench.js'); await w.savePrefs({ text_size: 115 }); }""")
    if not wait_until(page, "document.documentElement.dataset.text === '115'"):
        errors.append("a larger text size was not applied")
    page.evaluate("""async () => { const w = await import('/static/js/workbench.js'); await w.savePrefs({ text_size: 100, pins: [] }); }""")
    check_language(page, errors)
    check_shop(page, errors)
    check_legacy_batch(page, errors)
    check_drawing_check(page, errors)


def close_first_tour(page, user: str, errors: list[str]) -> None:
    """The first sign-in starts the tour in a corner; closing it is remembered (it would cover the corner)."""
    try:
        page.wait_for_selector("#tour", timeout=10000)
        page.click("#tour-next")
        page.wait_for_selector("#tour >> text=Step 2", timeout=5000)
        page.click("#tour-close")
    except Exception as e:
        errors.append(f"{user}: the first-login tour did not show or close: {e}")
    if not wait_until(page, "!document.querySelector('#tour')"):
        errors.append(f"{user}: the tour stayed open")


def check_shop(page, errors: list[str]) -> None:
    """The shop view shows the released drawing large; the part page offers its QR label; the tour starts again."""
    page.goto(f"{BASE}/#/shop?pn=MX-100-020")
    try:
        page.wait_for_selector("#shop-result .shop-pn", timeout=15000)
        page.wait_for_selector("#shop-result img.shop-drawing", timeout=15000)
        if not wait_until(page, "[...document.querySelectorAll('img.shop-drawing')].some(i => i.complete && i.naturalWidth > 0)"):
            errors.append("the shop view's drawing did not load")
    except Exception as e:
        errors.append(f"the shop view did not show the released drawing: {e}")
    page.goto(f"{BASE}/#/item/1")
    try:
        page.click("#item-qr", timeout=15000)
        page.wait_for_selector("dialog[open] img#qr-label", timeout=10000)
        if not wait_until(page, "document.querySelector('img#qr-label').naturalWidth > 0"):
            errors.append("the QR label picture did not load")
    except Exception as e:
        errors.append(f"the QR label did not open: {e}")
    page.keyboard.press("Escape")
    page.click("button.avatar")
    page.click(".ctxmenu >> text=Take the tour")
    try:
        page.wait_for_selector("#tour >> text=Find a drawing", timeout=10000)
        page.click("#tour-close")
    except Exception as e:
        errors.append(f"Take the tour did not start the tour again: {e}")


FIRST_DXF = """async () => {
  const core = await import('/static/js/core.js');
  const r = await core.get('/api/search?q=MX-100');
  const d = (r.documents || []).find(x => /\\.dxf$/i.test(x.filename));
  return d ? d.id : null;
}"""


def check_drawing_check(page, errors: list[str]) -> None:
    """A drawing's page checks the title block, material, revision and notes before approval (no AI needed)."""
    doc = page.evaluate(FIRST_DXF)
    if not doc:
        errors.append("no DXF drawing found for the drawing check")
        return
    page.goto(f"{BASE}/#/doc/{doc}")
    try:
        page.click("#drawing-check", timeout=15000)
        page.wait_for_selector(".check-list li", timeout=15000)
        if not wait_until(page, "document.querySelectorAll('.check-list li').length >= 6"):
            errors.append("the drawing check showed too few checks")
    except Exception as e:
        errors.append(f"the drawing check did not run: {e}")


IMPORT_BATCH = """async (source) => {
  const core = await import('/static/js/core.js');
  const fd = new FormData();
  fd.append('source', source); fd.append('target', 'Legacy/Smoke'); fd.append('groups', 'cad2d');
  const r = await core.upload('/api/import/start', fd);
  for (let i = 0; i < 300; i++) {
    const j = (await core.get(`/api/import/jobs/${r.job.id}`)).job;
    if (j.status !== 'running') return j;
    await new Promise(res => setTimeout(res, 100));
  }
  return null;
}"""


def check_legacy_batch(page, errors: list[str]) -> None:
    """A small legacy import: its review grid, a row confirmed, the count check that adds up."""
    src = Path(tempfile.mkdtemp(prefix="kosha-smoke-legacy-"))
    try:
        (src / "SM-100-001 Plate.dxf").write_bytes(b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
        (src / "notes.dxf.bak").write_bytes(b"junk")
        job = page.evaluate(IMPORT_BATCH, str(src))
        if not job or job["status"] != "done":
            errors.append(f"the smoke legacy import did not finish: {job}")
            return
        page.goto(f"{BASE}/#/legacy/{job['id']}?queue=")
        page.wait_for_selector("#legacy-grid tbody tr", timeout=15000)
        page.wait_for_selector("#legacy-check >> text=Every file is accounted for", timeout=10000)
        page.click("#legacy-grid tbody tr >> button:has-text('Confirm')")
        page.wait_for_selector("#legacy-grid >> text=Confirmed by", timeout=10000)
    except Exception as e:
        errors.append(f"the legacy batch screen did not work: {e}")
    finally:
        shutil.rmtree(src, ignore_errors=True)


def check_language(page, errors: list[str]) -> None:
    """India formats, F1 help, and a Hinglish search that says how it was read."""
    fmt = page.evaluate("""async () => { const l = await import('/static/js/locale.js');
  return [l.fmtShortMoney(240000), l.fmtMoney(125000), l.fmtDay('2026-10-10T08:30:00Z'), l.fmtWhen('2026-10-10T08:30:00Z')]; }""")
    if fmt != ["₹2.4 lakh", "₹1,25,000.00", "10-Oct-2026", "10-Oct-2026 14:00"]:
        errors.append(f"India formats are wrong: {fmt}")
    page.keyboard.press("F1")
    try:
        page.wait_for_selector("dialog[open] >> text=Help:", timeout=10000)
    except Exception:
        errors.append("F1 did not open the help for the screen")
    page.keyboard.press("Escape")
    page.goto(f"{BASE}/#/search?q=brg%20hsg")
    try:
        page.wait_for_selector("#search-understood", timeout=15000)
    except Exception:
        errors.append("a search with short forms did not say how it was read")


def check_resumable_upload(page, errors: list[str]) -> None:
    """A file larger than one piece goes up in pieces, with the browser's SHA-256, and comes back byte for byte."""
    r = page.evaluate(RESUMABLE)
    if not (r["same"] and r["version"] == 1 and r["last"] > 0.999):
        errors.append(f"resumable upload from the browser did not round-trip: {r}")


def choose_theme(page, theme: str, errors: list[str]) -> None:
    """Account menu › Theme, as a user does it."""
    page.click("button.avatar")
    page.click(".ctxmenu >> text=Theme:")
    page.check(f"dialog[open] input[name=theme][value={theme}]")
    page.click("dialog[open] .dlg-f >> text=Done")
    if not wait_until(page, f"document.documentElement.dataset.theme === '{theme}'"):
        errors.append(f"choosing the {theme} theme in the account menu did not apply it")
    # the choice is saved with the account in the background; a reload before that answer arrives would read the
    # old choice back from the server
    if not wait_until(page, "window.__koshaPending === 0"):
        errors.append(f"saving the {theme} theme with the account did not finish")


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
            page.add_init_script(PENDING_FETCHES)
            page.on("pageerror", lambda e: errors.append(f"script error: {e}"))
            page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
            for user, pw in (("asha", "demo-kosha-1"), ("admin", "admin-smoke-1")):
                resp = page.goto(BASE + "/#/login")   # None when only the #hash changed
                if resp is not None and "script-src 'self'" not in (resp.headers.get("content-security-policy") or ""):
                    errors.append("the page has no Content-Security-Policy; CSP violations would go unnoticed")
                wait_until(page, "window.__koshaPending === 0")     # nothing of the last person still on its way
                page.evaluate("localStorage.clear(); sessionStorage.clear()")
                page.reload()
                page.wait_for_selector("input[type=password]", timeout=15000)
                page.fill("input[autocomplete=username]", user)
                page.fill("input[type=password]", pw)
                page.keyboard.press("Enter")
                page.wait_for_selector("nav.rail", timeout=15000)
                close_first_tour(page, user, errors)
                if not wait_until(page, "window.koshaLive && window.koshaLive.readyState === 1"):
                    errors.append(f"{user}: the live updates connection did not open")
                visit_screens(page, user, errors, dark=False)
                if user == "asha":
                    choose_theme(page, "dark", errors)
                    page.reload()                     # the choice is remembered and applied before the page draws
                    page.wait_for_selector("nav.rail", timeout=15000)
                    if page.evaluate("document.documentElement.dataset.theme") != "dark":
                        errors.append("asha: the dark theme was not applied again after reloading the page")
                else:
                    page.emulate_media(color_scheme="dark")   # "Same as my computer" on a dark computer
                visit_screens(page, user, errors, dark=True)
                page.click("button.avatar")                       # the account menu opens "My notifications"
                page.click(".ctxmenu >> text=My notifications")
                try:
                    page.wait_for_selector("dialog[open] table", timeout=10000)
                except Exception:
                    errors.append(f"{user}: My notifications did not open")
                page.keyboard.press("Escape")
                page.click("button.avatar")                       # the account menu sets up two-step sign-in
                page.click(".ctxmenu >> text=Two-step sign-in")
                try:
                    page.wait_for_selector("dialog[open] input[autocomplete=one-time-code]", timeout=10000)
                except Exception:
                    errors.append(f"{user}: the two-step sign-in set-up did not open")
                page.keyboard.press("Escape")
                if user == "admin":
                    check_resumable_upload(page, errors)
                    check_share_page(browser, page, errors)
                    check_drafts(page, errors)
                    check_workbench(page, errors)
                if user == "asha":
                    choose_theme(page, "system", errors)
                page.emulate_media(color_scheme="light")
                print(f"ok: {user} opened {len(SCREENS)} screens in the light and the dark theme, and My notifications")
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
