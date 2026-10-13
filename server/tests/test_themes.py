# SPDX-License-Identifier: AGPL-3.0-or-later
"""Themes of the web client (docs/themes.md), checked without a browser.

- "Colours only through CSS variables": no colour literal in web/js/*.js, and in the style sheets colours appear
  only where a variable is defined.
- The Dark theme is written twice (chosen, and "Same as my computer" on a dark computer); the two must not drift.
- Every pair of text and background colour meets WCAG AA (4.5:1) in every theme.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"

# A module allowed to hold colour literals (for a future canvas or chart palette). Nothing needs one today.
PALETTE_MODULES: set[str] = set()

COLOUR_LITERAL = re.compile(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(\s*[\d.]")


def _strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_no_colour_literals_in_the_web_client_scripts():
    found = []
    for path in sorted((WEB / "js").glob("*.js")):
        if path.name in PALETTE_MODULES:
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in COLOUR_LITERAL.finditer(line):
                found.append(f"web/js/{path.name}:{no}: {m.group(0)}")
    assert not found, ("Colours belong in CSS variables (web/app.css), not in scripts; use a class or "
                       "'var(--name)' instead:\n" + "\n".join(found))


@pytest.mark.parametrize("sheet", ["app.css", "inputs.css", "office-addin/taskpane.css"])
def test_style_sheets_use_colours_only_in_variable_definitions(sheet):
    css = _strip_css_comments((WEB / sheet).read_text(encoding="utf-8"))
    found = []
    for decl in re.split(r"[;{}]", css):
        decl = decl.strip()
        if not decl or decl.startswith("--"):
            continue
        for m in COLOUR_LITERAL.finditer(decl):
            found.append(f"{sheet}: {decl[:100]} ({m.group(0)})")
    assert not found, "Define the colour as a variable in :root and use var(--name):\n" + "\n".join(found)


# ---------------------------------------------------------------------------------------------- parsing themes --

def _blocks(css: str) -> dict[str, dict[str, str]]:
    """Variable definitions by selector, with @media blocks flattened as '@media … | selector'."""
    css = _strip_css_comments(css)
    out: dict[str, dict[str, str]] = {}
    pos, stack = 0, []
    sel_start = 0
    while pos < len(css):
        c = css[pos]
        if c == "{":
            stack.append((css[sel_start:pos].strip(), pos + 1))
            sel_start = pos + 1
        elif c == "}":
            sel, start = stack.pop()
            body = css[start:pos]
            if "{" not in body:
                key = " | ".join([s for s, _ in stack] + [sel])
                vars_ = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))
                if vars_:
                    out.setdefault(key, {}).update({k: v.strip() for k, v in vars_.items()})
            sel_start = pos + 1
        pos += 1
    return out


def _themes(sheet: str, dark_key: str, media_key: str, extra: dict[str, str] | None = None):
    blocks = _blocks((WEB / sheet).read_text(encoding="utf-8"))
    base = blocks[":root"]
    themes = {"light": dict(base), "dark": {**base, **blocks[dark_key]}}
    for name, key in (extra or {}).items():
        themes[name] = {**base, **blocks[key]}
    return blocks, themes


APP_DARK = ':root[data-theme="dark"]'
APP_MEDIA = '@media (prefers-color-scheme: dark) | :root:not([data-theme="light"]):not([data-theme="contrast"])'
PANE_DARK = ':root[data-theme="dark"]'
PANE_MEDIA = '@media (prefers-color-scheme: dark) | :root:not([data-theme="light"])'


@pytest.mark.parametrize("sheet,dark,media", [("app.css", APP_DARK, APP_MEDIA),
                                              ("office-addin/taskpane.css", PANE_DARK, PANE_MEDIA)])
def test_dark_theme_is_the_same_when_chosen_and_when_following_the_computer(sheet, dark, media):
    blocks = _blocks((WEB / sheet).read_text(encoding="utf-8"))
    assert blocks[dark], f"{sheet}: no dark theme"
    assert blocks[dark] == blocks[media], f"{sheet}: the two dark blocks differ; keep them the same"
    unknown = set(blocks[dark]) - set(blocks[":root"])
    assert not unknown, f"{sheet}: dark overrides variables the light theme does not define: {sorted(unknown)}"


def test_every_theme_sets_color_scheme_so_native_controls_follow():
    css = _strip_css_comments((WEB / "app.css").read_text(encoding="utf-8"))
    for sel in (":root {", APP_DARK + " {", ':root[data-theme="contrast"] {'):
        i = css.index(sel)
        assert "color-scheme:" in css[i:css.index("}", i)], sel
    assert "color-scheme: dark" in css[css.index("@media (prefers-color-scheme: dark)"):]


# ---------------------------------------------------------------------------------------------- contrast --

def _luminance(hex_: str) -> float:
    h = hex_.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    def chan(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# (text, background) pairs as the style sheet uses them
APP_PAIRS = [
    ("ink", "bg"), ("ink", "panel"), ("ink", "panel-2"), ("ink-2", "panel"), ("ink", "field"), ("ink", "btn-bg"),
    ("ink", "row-hover"), ("ink", "sel"), ("ink", "accent-soft"), ("ink", "info-bg"), ("ink", "ok-soft"),
    ("ink", "warn-bg"), ("muted", "bg"), ("muted", "panel"), ("muted", "panel-2"), ("muted", "field"),
    ("blue", "bg"), ("blue", "panel"), ("link-hover", "panel"), ("accent", "panel"), ("accent", "accent-soft"),
    ("on-accent", "accent"), ("on-accent", "accent-hover"), ("on-ink", "ink"),
    ("ok", "ok-bg"), ("work", "work-bg"), ("rev", "rev-bg"), ("obs", "obs-bg"), ("err", "err-bg"),
    ("warn", "warn-bg"), ("err", "panel"), ("ok-bg", "ok"), ("rev", "info-bg"),
    ("dev-ink", "dev-bg"), ("mention-ink", "mention-bg"), ("chip-type-ink", "chip-type-bg"),
    ("chip-type-ink", "type-part"), ("chip-type-ink", "type-asm"), ("chip-type-ink", "type-drw"),
    ("chip-type-ink", "type-cad2d"), ("chip-type-ink", "type-pdf"), ("chip-type-ink", "type-mesh"),
    ("toast-ink", "toast-bg"), ("toast-ink", "toast-err"), ("toast-ink", "toast-ok"), ("on-avatar", "avatar"),
    ("rail-ink", "rail"), ("rail-ink", "rail-2"), ("rail-ink-hover", "rail-2"),
    ("viewer-ink", "viewer-bg"), ("viewer-ink", "viewer-bar"), ("viewer-ink", "viewer-btn"),
    ("viewer-ink-2", "viewer-bg"), ("viewer-muted", "viewer-bar"), ("viewer-bg", "viewer-accent"),
    ("art-ink", "art-bg"), ("art-ink-2", "art-bg"), ("art-muted", "art-bg"),
]
PANE_PAIRS = [("ink", "bg"), ("ink", "panel"), ("muted", "bg"), ("muted", "panel"), ("link", "bg"),
              ("accent-ink", "accent"), ("accent-ink", "accent-hover"), ("ok", "bg"), ("error", "bg"),
              ("warn-ink", "warn-bg")]


def _check_pairs(themes: dict[str, dict[str, str]], pairs) -> list[str]:
    bad = []
    for theme, v in themes.items():
        for fg, bg in pairs:
            ratio = contrast(v["--" + fg], v["--" + bg])
            if ratio < 4.5:
                bad.append(f"{theme}: --{fg} {v['--' + fg]} on --{bg} {v['--' + bg]} is {ratio:.2f}:1 (needs 4.5:1)")
    return bad


def test_text_meets_wcag_aa_in_every_theme_of_the_web_client():
    _, themes = _themes("app.css", APP_DARK, APP_MEDIA, {"contrast": ':root[data-theme="contrast"]'})
    assert set(themes) == {"light", "dark", "contrast"}
    bad = _check_pairs(themes, APP_PAIRS)
    assert not bad, "\n".join(bad)
    # High contrast earns its name: body text at least 7:1 (WCAG AAA)
    hc = themes["contrast"]
    for fg, bg in (("ink", "panel"), ("muted", "panel"), ("muted", "panel-2"), ("blue", "panel")):
        assert contrast(hc["--" + fg], hc["--" + bg]) >= 7, (fg, bg)


def test_text_meets_wcag_aa_in_the_office_task_pane():
    _, themes = _themes("office-addin/taskpane.css", PANE_DARK, PANE_MEDIA)
    bad = _check_pairs(themes, PANE_PAIRS)
    assert not bad, "\n".join(bad)


def test_contrast_formula():
    assert round(contrast("#000000", "#FFFFFF"), 1) == 21.0
    assert round(contrast("#777777", "#FFFFFF"), 2) == 4.48


def test_theme_boot_script_runs_before_the_page_and_without_modules():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    head = html[:html.index("</head>")]
    boot = head.index('<script src="/static/js/theme-boot.js"></script>')
    assert boot < head.index("app.css"), "the theme must be set before the style sheet paints the page"
    src = (WEB / "js" / "theme-boot.js").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|export)\b", src, re.M), "theme-boot.js is a classic script, not a module"
    assert "try" in src and "localStorage" in src
