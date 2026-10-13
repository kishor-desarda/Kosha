# SPDX-License-Identifier: AGPL-3.0-or-later
"""The logo, icons and web app manifest: every file the web client, the README and llms.txt point to exists."""
import json
import re
import struct

from kosha import config

ROOT = config.REPO_DIR
WEB = config.STATIC_DIR


def _png_size(path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", path
    return struct.unpack(">II", data[16:24])


def _static(url: str):
    assert url.startswith("/static/"), url
    return WEB / url.removeprefix("/static/")


def test_index_page_links_existing_icons_and_manifest():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    links = re.findall(r'<link rel="(?:icon|apple-touch-icon|manifest)" href="([^"]+)"', html)
    assert len(links) == 4
    for url in links:
        assert _static(url).is_file(), url
    assert 'name="description"' in html and 'name="theme-color"' in html


def test_manifest_icons_exist_with_their_stated_size():
    manifest = json.loads((WEB / "brand" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["start_url"] == "/" and manifest["name"]
    for icon in manifest["icons"]:
        path = _static(icon["src"])
        assert path.is_file(), icon["src"]
        if icon["type"] == "image/png":
            w, h = map(int, icon["sizes"].split("x"))
            assert _png_size(path) == (w, h), icon["src"]
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])


def test_sign_in_logo_exists():
    main = (WEB / "js" / "main.js").read_text(encoding="utf-8")
    for url in re.findall(r"'(/static/brand/[^']+)'", main):
        assert _static(url).is_file(), url


def test_office_addin_icons_are_square_at_their_size():
    for n in (16, 32, 64, 80):
        assert _png_size(WEB / "office-addin" / "assets" / f"icon-{n}.png") == (n, n)


def test_readme_and_llms_txt_links_resolve():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for path in re.findall(r'(?:src|srcset)="(docs/brand/[^"]+)"', readme):
        assert (ROOT / path).is_file(), path
    llms = (ROOT / "llms.txt").read_text(encoding="utf-8")
    assert llms.startswith("# Kosha\n\n> ")
    prefix = "https://github.com/kishor-desarda/Kosha/blob/main/"
    links = re.findall(r"\]\((https://[^)]+)\)", llms)
    assert links
    for url in links:
        assert url.startswith(prefix), url
        assert (ROOT / url.removeprefix(prefix)).exists(), url
