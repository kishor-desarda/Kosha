# SPDX-License-Identifier: AGPL-3.0-or-later
"""The QR encoder for job-card labels (specification 5.7)."""
import pytest

from kosha import qr

TEXTS = ["MX-100-020", "https://kosha.example.com/#/shop?pn=MX-100-020",
         "https://plm.demo-engineering.example/#/shop?pn=" + "CV-200-" * 12, "पंप हाउसिंग 80"]


def _finder_ok(m, x0, y0):
    for dy in range(7):
        for dx in range(7):
            ring = max(abs(dx - 3), abs(dy - 3))
            if m[y0 + dy][x0 + dx] != (ring != 2):
                return False
    return True


def _format(m):
    """The 15 format bits read from around the top-left finder, unmasked."""
    bits = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
    return sum(int(b) << i for i, b in enumerate(bits)) ^ 0x5412


@pytest.mark.parametrize("text", TEXTS)
def test_structure(text):
    m = qr.matrix(text)
    n = len(m)
    assert all(len(row) == n for row in m) and (n - 17) % 4 == 0 and 1 <= (n - 17) // 4 <= 10
    assert _finder_ok(m, 0, 0) and _finder_ok(m, n - 7, 0) and _finder_ok(m, 0, n - 7)
    assert [m[6][i] for i in range(8, n - 8)] == [i % 2 == 0 for i in range(8, n - 8)]      # timing pattern
    fmt = _format(m)
    assert fmt >> 13 == 0                                  # error correction level M
    assert qr._bch_format(fmt >> 10 & 7) ^ 0x5412 == fmt   # a valid format word
    assert m[n - 8][8] is True                             # the dark module


def test_versions_grow_with_the_text_and_refuse_too_long():
    assert len(qr.matrix("A")) == 21
    assert len(qr.matrix("x" * 200)) == 57                # version 10
    with pytest.raises(qr.QrTooLong):
        qr.matrix("x" * 214)


def test_every_mask_gives_a_valid_code_and_the_best_is_chosen():
    text = TEXTS[1]
    scores = {mask: qr._penalty(qr.matrix(text, mask)) for mask in range(8)}
    assert qr.matrix(text) == qr.matrix(text, min(scores, key=scores.get))


def test_svg_is_printable_and_escapes_the_caption():
    s = qr.svg("MX-100-020", label="MX-100-020 <BRACKET & PLATE>")
    assert s.startswith("<svg") and "&lt;BRACKET &amp; PLATE&gt;" in s and "<script" not in s


@pytest.mark.parametrize("text", TEXTS)
def test_a_reader_decodes_it(text):
    """With a QR reader installed (zxing-cpp, as phones use), every code reads back exactly."""
    zxingcpp = pytest.importorskip("zxingcpp")
    np = pytest.importorskip("numpy")
    m = qr.matrix(text)
    scale, quiet = 6, 4
    n = len(m) + 2 * quiet
    img = np.full((n * scale, n * scale), 255, dtype=np.uint8)
    for y, row in enumerate(m):
        for x, dark in enumerate(row):
            if dark:
                img[(y + quiet) * scale:(y + quiet + 1) * scale, (x + quiet) * scale:(x + quiet + 1) * scale] = 0
    found = zxingcpp.read_barcodes(img)
    assert [r.text for r in found] == [text]
