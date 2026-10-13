# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hinglish and short forms in search (specification 5.1)."""
from kosha import searchwords


def test_hinglish_request_becomes_the_words_that_matter():
    p = searchwords.parse("32 teeth wala gear ka drawing dikhao")
    assert p["documents"] is True
    assert p["groups"][0][:2] == ["32T", "Z32"] and "32 teeth" in p["groups"][0]
    assert p["groups"][1] == ["gear", "ger"]
    assert len(p["groups"]) == 2


def test_short_forms_and_plain_words():
    p = searchwords.parse("brg hsg 80")
    assert p["groups"] == [["brg", "bearing"], ["hsg", "housing"], ["80"]] and not p["documents"]
    assert searchwords.parse("SS316 flange DN100")["groups"] == [["SS316"], ["flange"], ["DN100"]]
    assert searchwords.parse("32T gear")["groups"][0][0] == "32T"
    assert searchwords.parse("Z32")["groups"] == [["Z32"]]


def test_only_filler_words_still_search_for_them():
    assert searchwords.parse("dikhao")["groups"] == [["dikhao"]]
    assert searchwords.parse("")["groups"] == []


def test_understood_says_how_it_was_read():
    assert searchwords.understood(searchwords.parse("brg hsg 80")) == "brg or bearing, hsg or housing, 80"
