# SPDX-License-Identifier: AGPL-3.0-or-later
"""Language and locale (specification 5.1): Hinglish search end to end, the company locale, each person's language,
and translation files that stay complete."""
import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


def _root(admin) -> int:
    return next(f["id"] for f in admin.call("GET", "/api/folders")["folders"] if not f["parent_id"])


def _part(client, folder, pn, description):
    client.call("POST", "/api/documents/upload", files={"file": (f"{pn}.dxf", b"0\nSECTION\n0\nEOF\n")},
                fields={"folder_id": folder, "part_number": pn, "description": description})


def test_hinglish_search_locale_and_language(kosha):
    admin = kosha.client()
    eng = kosha.add_user("lang-eng", "engineer")
    root = _root(admin)
    _part(eng, root, "LG-100-001", "BEARING HOUSING 80 BORE FG260")
    _part(eng, root, "LG-100-002", "SPUR GEAR 32T MODULE 2")
    _part(eng, root, "LG-100-003", "SPUR GEAR 40T MODULE 2")

    r = eng.call("GET", "/api/search?q=brg%20hsg%2080")
    assert [i["part_number"] for i in r["items"]] == ["LG-100-001"]
    assert r["understood"] == "brg or bearing, hsg or housing, 80"
    r = eng.call("GET", "/api/search?q=" + "32 teeth wala gear ka drawing dikhao".replace(" ", "%20"))
    assert [i["part_number"] for i in r["items"]] == ["LG-100-002"]
    assert [d["part_number"] for d in r["documents"]] == ["LG-100-002"] and r["documents_first"] is True
    assert eng.call("GET", "/api/search?q=LG-100-001")["understood"] == ""           # nothing to explain

    loc = eng.call("GET", "/api/locale")
    assert loc["locale"]["code"] == "en-IN" and loc["locale"]["currency"] == "INR"
    langs = {x["code"]: x for x in loc["languages"]}
    assert langs["en"]["covered"] == 100 and 0 < langs["hi"]["covered"] < 100
    eng.call("PUT", "/api/locale", {"locale": "de-DE"}, expect=403)
    assert "India (en-IN)" in admin.call("PUT", "/api/locale", {"locale": "xx"}, expect=400)["error"]
    assert admin.call("PUT", "/api/locale", {"locale": "en-GB"})["locale"]["currency"] == "GBP"

    assert eng.call("PUT", "/api/me/prefs", {"language": "hi"})["prefs"]["language"] == "hi"
    eng.call("PUT", "/api/me/prefs", {"language": "tlh"}, expect=400)


def test_translation_files_are_complete_and_consistent():
    en = json.loads((WEB / "locales" / "en.json").read_text(encoding="utf-8"))
    used = set()
    for f in (WEB / "js").glob("*.js"):
        used |= set(re.findall(r"\bt\('([a-z_]+\.[a-z_.]+)'", f.read_text(encoding="utf-8")))
    missing = sorted(k for k in used if k not in en)
    assert not missing, f"keys used in web/js but not in en.json: {missing}"
    for f in (WEB / "locales").glob("*.json"):
        other = json.loads(f.read_text(encoding="utf-8"))
        assert "_name" in other, f.name
        extra = sorted(k for k in other if k not in en)
        assert not extra, f"{f.name} has keys English does not: {extra}"
        for k, v in other.items():
            if not k.startswith("_"):
                assert set(re.findall(r"\{(\w+)\}", v)) == set(re.findall(r"\{(\w+)\}", en[k])), (f.name, k)
