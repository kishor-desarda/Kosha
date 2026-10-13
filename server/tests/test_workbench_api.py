# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workbench, end to end (specification 9.1): favourites that follow renames and hide what a person may not see,
saved views, screen preferences, and personal-data export and erase."""
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"


def _root(admin) -> int:
    return next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])


def test_favourites_views_and_prefs(kosha):
    admin = kosha.client()
    eng = kosha.add_user("wb-eng", "engineer")
    other = kosha.add_user("wb-other", "engineer")
    doc = eng.call("POST", "/api/documents/upload", files={"file": ("WB-100-001 Bracket.dxf", b"0\nSECTION\n0\nEOF\n")},
                   fields={"folder_id": _root(admin), "part_number": "WB-100-001", "description": "BRACKET"})["documents"][0]
    item_id = eng.call("GET", "/api/items?q=WB-100-001")["items"][0]["id"]

    assert eng.call("GET", "/api/me/workbench") == {"favourites": [], "views": [],
                                                    "prefs": {"accent": "blue", "text_size": 100, "pins": []}}
    f = eng.call("POST", "/api/me/favourites", {"kind": "doc", "ref_id": doc["id"]})["favourite"]
    assert (f["label"], f["hash"], f["state"]) == ("WB-100-001 Bracket.dxf", f"#/doc/{doc['id']}", "in_work")
    eng.call("POST", "/api/me/favourites", {"kind": "doc", "ref_id": doc["id"]})          # twice is one favourite
    eng.call("POST", "/api/me/favourites", {"kind": "item", "ref_id": item_id})
    assert len(eng.call("GET", "/api/me/workbench")["favourites"]) == 2
    assert other.call("GET", "/api/me/workbench")["favourites"] == []                       # each person their own
    assert "cannot see" in eng.call("POST", "/api/me/favourites", {"kind": "item", "ref_id": 99999}, expect=404)["error"]
    eng.call("POST", "/api/me/favourites", {"kind": "user", "ref_id": 1}, expect=400)

    # a deleted file drops out of the list (and comes back if it is restored)
    eng.call("DELETE", f"/api/documents/{doc['id']}")
    assert [x["kind"] for x in eng.call("GET", "/api/me/workbench")["favourites"]] == ["item"]
    eng.call("POST", f"/api/recycle-bin/{doc['id']}/restore")
    assert len(eng.call("GET", "/api/me/workbench")["favourites"]) == 2
    eng.call("DELETE", f"/api/me/favourites/doc/{doc['id']}")
    assert [x["kind"] for x in eng.call("GET", "/api/me/workbench")["favourites"]] == ["item"]

    # saved views: Kosha's own screens only; the same name replaces
    v = eng.call("POST", "/api/me/views", {"name": "  Released   brackets ", "hash": "#/search?q=WB-100"})["view"]
    assert v["name"] == "Released brackets"
    eng.call("POST", "/api/me/views", {"name": "Released brackets", "hash": "#/files/1?state=released"})
    views = eng.call("GET", "/api/me/workbench")["views"]
    assert [(x["name"], x["hash"]) for x in views] == [("Released brackets", "#/files/1?state=released")]
    assert "own screens" in eng.call("POST", "/api/me/views", {"name": "x", "hash": "https://example.com"}, expect=400)["error"]
    assert "name" in eng.call("POST", "/api/me/views", {"name": " ", "hash": "#/home"}, expect=400)["error"]
    other.call("DELETE", f"/api/me/views/{v['id']}", expect=404)                            # not someone else's

    # preferences
    p = eng.call("PUT", "/api/me/prefs", {"accent": "teal", "text_size": 115,
                                         "pins": [{"hash": "#/boms", "title": "BOMs"}, {"hash": "#/boms", "title": "x"}]})["prefs"]
    assert p == {"accent": "teal", "text_size": 115, "pins": [{"hash": "#/boms", "title": "BOMs"}]}
    eng.call("PUT", "/api/me/prefs", {"accent": "pink"}, expect=400)
    eng.call("PUT", "/api/me/prefs", {"text_size": 300}, expect=400)
    eng.call("PUT", "/api/me/prefs", {"pins": [{"hash": "javascript:alert(1)"}]}, expect=400)
    assert eng.call("PUT", "/api/me/prefs", {"pins": [{"hash": f"#/doc/{i}", "title": str(i)} for i in range(9)]},
                    expect=400)["error"].startswith("Pin at most 8")

    # personal data: exported, then erased with the account
    me = eng.call("GET", "/api/auth/me")["user"]["id"]
    data = admin.call("GET", f"/api/users/{me}/personal-data")
    assert data["favourites"][0]["kind"] == "item" and data["saved_views"][0]["name"] == "Released brackets"
    admin.call("POST", f"/api/users/{me}/erase", {"confirm": "wb-eng"})
    with kosha.db() as c:
        assert c.execute("SELECT COUNT(*) FROM user_favourites WHERE user_id = ?", (me,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM saved_views WHERE user_id = ?", (me,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM user_prefs WHERE user_id = ?", (me,)).fetchone()[0] == 0


def test_every_accent_colour_meets_wcag_aa():
    from test_themes import APP_DARK, APP_MEDIA, _themes, contrast
    blocks, themes = _themes("app.css", APP_DARK, APP_MEDIA)
    accents = ("teal", "green", "purple", "orange")
    pairs = [("accent", "panel"), ("accent", "accent-soft"), ("on-accent", "accent"), ("on-accent", "accent-hover"),
             ("ink", "accent-soft")]
    bad = []
    for name in accents:
        light = {**themes["light"], **blocks[f':root[data-accent="{name}"]:not([data-theme="contrast"])']}
        dark = {**themes["dark"], **blocks[f':root[data-theme="dark"][data-accent="{name}"]']}
        media = blocks['@media (prefers-color-scheme: dark) | '
                       f':root[data-accent="{name}"]:not([data-theme="light"]):not([data-theme="contrast"])']
        assert media == blocks[f':root[data-theme="dark"][data-accent="{name}"]'], name    # keep the two dark copies equal
        for theme, v in (("light", light), ("dark", dark)):
            for fg, bg in pairs:
                if contrast(v["--" + fg], v["--" + bg]) < 4.5:
                    bad.append(f"{name} {theme}: --{fg} on --{bg}")
    assert not bad, bad


@pytest.mark.parametrize("name", ["teal", "green", "purple", "orange"])
def test_theme_boot_knows_every_accent(name):
    assert name in (WEB / "js" / "theme-boot.js").read_text(encoding="utf-8")
