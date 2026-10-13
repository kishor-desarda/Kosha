# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hinglish and short forms in search (specification 5.1).

People type the way they speak: *"32 teeth wala gear ka drawing dikhao"*, *"brg hsg 80"*, *"SS316 flange DN100"*.
Before searching, Kosha

* drops filler words (*wala, ka, ki, dikhao, chahiye, show, me* ...), which describe the request, not the part;
* reads a few words as the kind of record wanted (*drawing*, *file*, *pdf*): they are dropped from the text and
  returned as a hint;
* turns short forms into every way the word is written (*brg* → bearing or brg, *hsg* → housing or hsg);
* reads a tooth count however it is written (*32 teeth*, *32 dant*, *32T*, *Z32*).

Each remaining word becomes a group of alternatives; a record matches when every group matches somewhere. The India
localisation pack adds words through its ``search_short_forms`` and ``search_fillers`` dictionaries.
"""
from __future__ import annotations

import re

FILLERS = {
    # Hinglish
    "wala", "wali", "wale", "ka", "ki", "ke", "ko", "mein", "me", "se", "aur", "hai", "hain", "dikhao", "dikha", "do",
    "chahiye", "batao", "dekhna", "dekho", "kaun", "kahan", "konsa", "kaunsa", "ek", "sab", "sabhi", "wo", "woh", "yeh",
    "ye", "liye", "par", "pe", "kya", "nikalo", "bhejo", "dhundo", "khojo",
    # English request words
    "show", "find", "get", "give", "open", "the", "a", "an", "of", "for", "with", "please", "all", "list", "search",
}
DOC_WORDS = {"drawing", "drawings", "dwg", "file", "files", "pdf", "model", "naksha"}
SHORT_FORMS = {
    "brg": ["bearing"], "brgs": ["bearing"], "hsg": ["housing"], "hsng": ["housing"], "flg": ["flange"],
    "flng": ["flange"], "shft": ["shaft"], "sft": ["shaft"], "gr": ["gear"], "gear": ["gear", "ger"],
    "assy": ["assembly", "asm"], "asm": ["assembly", "assy"], "asmbly": ["assembly"], "plt": ["plate"],
    "brkt": ["bracket"], "bkt": ["bracket"], "cvr": ["cover"], "wshr": ["washer"], "blt": ["bolt"],
    "imp": ["impeller"], "imlr": ["impeller"], "cpl": ["coupling"], "cplg": ["coupling"], "pmp": ["pump"],
    "mtr": ["motor"], "vlv": ["valve"], "spr": ["spring"], "pully": ["pulley"], "puli": ["pulley"],
    "chakka": ["wheel", "gear"], "dhakkan": ["cover", "lid"], "patti": ["strip", "plate"], "naya": ["new"],
    "ss": ["ss", "stainless"], "ms": ["ms", "mild steel"],
}
TEETH = re.compile(r"^(\d{1,3})\s*(?:t|teeth|tooth|dant|daant|dante)$", re.I)


def _pack_words() -> tuple[dict[str, list[str]], set[str]]:
    try:
        from .packs import PackConfig
        cfg = PackConfig.current()
        shorts: dict[str, list[str]] = {}
        for c in cfg.dictionary("search_short_forms"):
            shorts.setdefault(c.code.lower(), []).extend(w.strip().lower() for w in c.name.split("|") if w.strip())
        fillers = {c.code.lower() for c in cfg.dictionary("search_fillers")}
        return shorts, fillers
    except Exception:       # a broken pack must not stop search
        return {}, set()


def parse(q: str) -> dict:
    """{"groups": [[alternative, ...], ...], "documents": True when the words ask for drawings or files}."""
    pack_shorts, pack_fillers = _pack_words()
    words = re.findall(r"[^\s,;]+", (q or "").strip())
    groups: list[list[str]] = []
    documents = False
    i = 0
    while i < len(words):
        w = words[i]
        low = w.lower().strip(".?!")
        nxt = words[i + 1].lower() if i + 1 < len(words) else ""
        m = TEETH.match(low) or (re.fullmatch(r"\d{1,3}", low) and TEETH.match(low + nxt))
        if m:
            n = m.group(1)
            groups.append([f"{n}T", f"Z{n}", f"{n} teeth", f"{n} tooth", f"{n}-tooth"])
            i += 1 if TEETH.match(low) else 2
            continue
        if low in DOC_WORDS:
            documents = True
        elif low in FILLERS or low in pack_fillers:
            pass
        elif low in pack_shorts or low in SHORT_FORMS:
            alts = [w] + pack_shorts.get(low, []) + SHORT_FORMS.get(low, [])
            groups.append(list(dict.fromkeys(a for a in alts if a)))
        elif low:
            groups.append([w.strip(".?!")])
        i += 1
    if not groups and words:            # only filler words: search for what was typed rather than for everything
        groups = [[w] for w in words]
    return {"groups": groups, "documents": documents}


def understood(parsed: dict) -> str:
    """How Kosha read the search, in plain words, for the search page (empty when nothing was changed)."""
    parts = [" or ".join(g) if len(g) > 1 else g[0] for g in parsed["groups"]]
    return ", ".join(parts) + (" (drawings and files)" if parsed["documents"] else "")
