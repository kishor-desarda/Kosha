# SPDX-License-Identifier: AGPL-3.0-or-later
"""The small language of option rules, read without ``eval``.

A rule says when a BOM line is used in a configurable product, in the product's option codes::

    MOTOR = 7K5
    MOTOR in (5K5, 7K5) and VOLTAGE = 415
    not SEAL = PTFE
    VOLTAGE != 230 or (MOTOR = 11K and FRAME = HEAVY)

Codes and values are letters, digits and ``. _ - +``, compared without regard to case. ``and`` binds closer than
``or``; brackets group. An empty rule means "always".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TOKEN = re.compile(r"\s*(?:(\()|(\))|(,)|(!=|=)|([A-Za-z0-9_.+\-]+))")
WORDS = {"AND", "OR", "NOT", "IN"}
MAX_LEN = 500


class RuleError(ValueError):
    """The rule cannot be read; the message says where and what to write instead."""


@dataclass(frozen=True)
class Cond:
    option: str
    values: frozenset
    negate: bool = False

    def eval(self, choice: dict) -> bool:
        return (choice.get(self.option) in self.values) != self.negate


@dataclass(frozen=True)
class Not:
    inner: object

    def eval(self, choice: dict) -> bool:
        return not self.inner.eval(choice)


@dataclass(frozen=True)
class All:
    parts: tuple

    def eval(self, choice: dict) -> bool:
        return all(p.eval(choice) for p in self.parts)


@dataclass(frozen=True)
class Any:
    parts: tuple

    def eval(self, choice: dict) -> bool:
        return any(p.eval(choice) for p in self.parts)


class Always:
    def eval(self, choice: dict) -> bool:
        return True


ALWAYS = Always()


def _tokens(text: str) -> list[str]:
    out, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise RuleError(f"Kosha can't read \"{text[pos:pos + 12].strip()}\" in the rule. Write rules like "
                            "MOTOR = 7K5 or MOTOR in (5K5, 7K5) and VOLTAGE = 415.")
        tok = next(g for g in m.groups() if g is not None)
        out.append(tok.upper())
        pos = m.end()
    return out


def parse(text: str | None):
    """The rule as a tree with ``eval(choice)``; ``ALWAYS`` for an empty rule. Raises RuleError."""
    text = (text or "").strip()
    if not text:
        return ALWAYS
    if len(text) > MAX_LEN:
        raise RuleError(f"A rule can be at most {MAX_LEN} characters long.")
    toks = _tokens(text)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take(expected=None):
        nonlocal pos
        tok = peek()
        if tok is None:
            raise RuleError("The rule ends too early" + (f": {expected} is missing." if expected else "."))
        if expected and tok != expected:
            raise RuleError(f"Expected {expected} but found \"{tok}\" in the rule.")
        pos += 1
        return tok

    def name(what):
        tok = take()
        if tok in WORDS or tok in ("(", ")", ",", "=", "!="):
            raise RuleError(f"Expected {what} but found \"{tok}\" in the rule.")
        return tok

    def expr():
        parts = [term()]
        while peek() == "OR":
            take()
            parts.append(term())
        return parts[0] if len(parts) == 1 else Any(tuple(parts))

    def term():
        parts = [factor()]
        while peek() == "AND":
            take()
            parts.append(factor())
        return parts[0] if len(parts) == 1 else All(tuple(parts))

    def factor():
        tok = peek()
        if tok == "NOT":
            take()
            return Not(factor())
        if tok == "(":
            take()
            inner = expr()
            take(")")
            return inner
        option = name("an option code, such as MOTOR")
        op = peek()
        negate = False
        if op == "NOT":
            take()
            negate = True
            op = peek()
            if op != "IN":
                raise RuleError(f"After {option} not, write in (…).")
        if op in ("=", "!="):
            take()
            return Cond(option, frozenset([name(f"a choice of {option}")]), op == "!=")
        if op == "IN":
            take()
            take("(")
            values = [name(f"a choice of {option}")]
            while peek() == ",":
                take()
                values.append(name(f"a choice of {option}"))
            take(")")
            return Cond(option, frozenset(values), negate)
        raise RuleError(f"After {option}, write = (or !=) and a choice, or in (…) with choices.")

    tree = expr()
    if pos != len(toks):
        raise RuleError(f"Kosha didn't expect \"{toks[pos]}\" there. Join conditions with and / or.")
    return tree


def conditions(tree) -> list[Cond]:
    """Every condition in a rule (to check that its options and choices exist)."""
    if isinstance(tree, Cond):
        return [tree]
    if isinstance(tree, Not):
        return conditions(tree.inner)
    if isinstance(tree, (All, Any)):
        return [c for p in tree.parts for c in conditions(p)]
    return []


def check(text: str | None, options: dict[str, set[str]]):
    """Parse a rule and check it against the product's options ({OPTION: {CHOICE, ...}}). Returns the tree."""
    tree = parse(text)
    for c in conditions(tree):
        if c.option not in options:
            raise RuleError(f"There is no option {c.option}. The options are "
                            f"{', '.join(sorted(options)) or 'not set up yet'}.")
        for v in sorted(c.values - options[c.option]):
            raise RuleError(f"{c.option} has no choice {v}. Its choices are {', '.join(sorted(options[c.option]))}.")
    return tree


def normal(text: str | None) -> str:
    """The rule written the same way every time: codes in capitals, and / or / not / in in small letters."""
    toks = _tokens((text or "").strip()) if (text or "").strip() else []
    out = ""
    for t in toks:
        if t in (")", ","):
            out = out.rstrip() + t + " "
        elif t == "(":
            out += "("
        else:
            out += (t.lower() if t in WORDS else t) + " "
    return out.strip().replace("( ", "(")
