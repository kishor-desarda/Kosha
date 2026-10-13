# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pieces of the AI jobs that need no server: the PNG writer for title-block crops, reading title-block values
from OCR text, and the revision and job-routing rules."""
import struct
import zlib

import pytest

from kosha import ai, ai_jobs


def test_png_is_a_valid_image():
    data = ai_jobs.png(2, 2, [b"\xff\x00\x00\x00\xff\x00", b"\x00\x00\xff\xff\xff\xff"])
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (2, 2)
    idat = data.index(b"IDAT")
    n = struct.unpack(">I", data[idat - 4: idat])[0]
    assert zlib.decompress(data[idat + 4: idat + 4 + n]) == b"\x00\xff\x00\x00\x00\xff\x00\x00\x00\x00\xff\xff\xff\xff"
    assert ai_jobs.png(1, 1, [b"\x80"], channels=1)[25] == 0          # grey


def test_title_block_values_from_ocr_text():
    text = "DEMO ENGINEERING\nDRG NO: MX-100-020\nTITLE\nSHAFT D80 X 520\nMATERIAL 42CrMo4\nREV. B\nSCALE 1:2\nDRAWN asha"
    assert ai_jobs.parse_title_text(text) == {"part_number": "MX-100-020", "description": "SHAFT D80 X 520",
                                              "material": "42CrMo4", "revision": "B", "scale": "1:2",
                                              "drawn_by": "asha"}
    # a label with nothing readable after it gives nothing, not the next label
    assert ai_jobs.parse_title_text("MATERIAL\nREV A") == {"revision": "A"}
    assert ai_jobs.parse_title_text("") == {}


@pytest.mark.parametrize("a,b", [("Rev. 2", "2"), ("REV-B", "b"), ("Revision C", "C"), ("A", " a ")])
def test_revisions_compare_without_their_label(a, b):
    assert ai_jobs._rev(a) == ai_jobs._rev(b)


def test_routes_for_a_job(kdb):
    cfg = ai.settings()
    cfg.update(provider="p.main", local_provider="")
    cfg["jobs"]["classify"] = {"model": "small", "fallback_provider": "", "fallback_model": "big", "timeout_s": 0}
    assert ai.route("classify", cfg) == [("p.main", "small", False), ("p.main", "big", True)]
    cfg["jobs"]["classify"] = {"model": "small", "fallback_provider": "p.local", "fallback_model": "", "timeout_s": 0}
    assert ai.route("classify", cfg) == [("p.main", "small", False), ("p.local", "", True)]
    assert ai.route("ask", cfg) == [("p.main", "", False)]
    with pytest.raises(ai.ApiError) as e:
        ai.route("classify", cfg, local=True)
    assert e.value.code == "ai_local_only"
    cfg["local_provider"] = "p.local"
    assert ai.route("classify", cfg, local=True) == [("p.local", "", False)]


def test_cost_from_prices_per_million_tokens(kdb):
    cfg = {"prices": {"m": {"in": 0.5, "out": 2.0}}}
    assert ai.cost_of("m", {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}, cfg) == 1.5
    assert ai.cost_of("other", {"prompt_tokens": 10}, cfg) == 0
