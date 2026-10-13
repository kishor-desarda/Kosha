# SPDX-License-Identifier: AGPL-3.0-or-later
"""Numbering service: part numbers from number series, and spec codes built from templates.

* A **number series** (``SequenceScheme``) has a pattern such as ``{prefix}{seq:3}`` or ``P-{yyyy}-{seq:3}``.
  Numbers are handed out inside one write transaction and written to the allocation ledger
  (``number_allocations``, part number unique), so a number is never issued twice and never reused,
  even when many people ask at the same moment. The running counter is the same ``numbering`` table the
  CAD add-ins use through ``/api/numbering/next``.
* A **spec code** (``SpecTemplate``) is built from typed values, for example a spur gear
  ``GR-{module}-{teeth}-{face_width_mm}-{material}``. The same values always give the same code, and
  "find before you create" applies: if an item with that code exists, it is returned instead of a duplicate.

Templates come from industry packs (``kosha.packs``); this module only needs a configuration object
with ``dictionary(name)``, ``category(code)``, ``records(kind)`` and ``item_type(code)``.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable

from . import boms, db

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?(?::(\d{1,2}))?\}")
CODE_OK = re.compile(r"^[A-Z0-9][A-Z0-9._/\-]{0,63}$")
ID_OK = re.compile(r"^[a-z0-9][a-z0-9\-]{0,47}$")
NAME_OK = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
FIELD_TYPES = ("int", "decimal", "choice", "text")
CATEGORIES_DICTIONARY = "categories"     # a choice field with this dictionary lists the categories


class DataError(ValueError):
    """A pack file, template or record is not valid. The message says where and what, in plain words."""


class FieldError(ValueError):
    """A value typed by a user does not fit the field."""


# ---------------------------------------------------------------- helpers --

def fmt_decimal(d: Decimal) -> str:
    """2.50 -> '2.5', 100 -> '100' (never '1E+2')."""
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "") else s


def humanize(name: str) -> str:
    words = name.replace("_", " ").split()
    if words and words[-1] in ("mm", "kw", "um"):
        words = words[:-1]
    return (" ".join(words) or name).capitalize()


def _number(v: Any, where: str, what: str) -> Decimal:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise DataError(f"{where}: {what} must be a number, found {v!r}")
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation:
        raise DataError(f"{where}: {what} must be a number, found {v!r}") from None
    if not d.is_finite():
        raise DataError(f"{where}: {what} must be a number, found {v!r}")
    return d


def _check_keys(data: dict, allowed: Iterable[str], where: str) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise DataError(f"{where}: unknown setting {', '.join(repr(u) for u in unknown)} "
                        f"(allowed: {', '.join(sorted(allowed))})")


def _text(data: dict, key: str, where: str, required: bool = True, default: str = "") -> str:
    v = data.get(key)
    if v is None or v == "":
        if required:
            raise DataError(f"{where}: '{key}' is missing")
        return default
    if not isinstance(v, str):
        raise DataError(f"{where}: '{key}' must be text; write it in quotes (found {v!r})")
    return v.strip()


@dataclass
class Choice:
    code: str
    name: str
    extra: dict = field(default_factory=dict)

    def attr(self, name: str | None) -> str:
        if not name or name == "code":
            return self.code
        if name == "name":
            return self.name
        v = self.extra.get(name, "")
        return "" if v is None else str(v)

    def to_dict(self) -> dict:
        n, c = self.name.upper(), self.code.upper()
        same = n == c or n.startswith(c + " ")
        return {"value": self.code, "label": self.name if same else f"{self.code} — {self.name}"}


def choice_from(entry: Any, where: str) -> Choice:
    """A dictionary entry or inline choice: "8.8" or {code: ZZ, name: Two metal shields, suffix: -ZZ}."""
    if isinstance(entry, str):
        return Choice(entry.strip(), entry.strip())
    if isinstance(entry, dict):
        code = entry.get("code")
        if not isinstance(code, str) or not code.strip():
            raise DataError(f"{where}: every entry needs a 'code' written as text in quotes (found {code!r})")
        name = entry.get("name", code)
        if not isinstance(name, str):
            raise DataError(f"{where}: the name of '{code}' must be text")
        extra = {k: v for k, v in entry.items() if k not in ("code", "name")}
        for k, v in extra.items():
            if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                raise DataError(f"{where}: '{code}' has a value for '{k}' that is not plain text or a number")
        return Choice(code.strip(), name.strip() or code.strip(), extra)
    raise DataError(f"{where}: a choice must be text in quotes or a mapping with code and name (found {entry!r})")


class Template:
    """Text with placeholders: {name}, {name.attr} (a dictionary entry's name, code or other value) and {seq:3}."""

    def __init__(self, text: Any, where: str):
        if not isinstance(text, str) or not text.strip():
            raise DataError(f"{where} is missing or empty")
        rest = PLACEHOLDER.sub("", text)
        if "{" in rest or "}" in rest:
            raise DataError(f"{where}: '{text}' has a brace that is not a placeholder such as {{module}}")
        self.text = text
        self.refs = [(m.group(1), m.group(2), m.group(3)) for m in PLACEHOLDER.finditer(text)]

    def names(self) -> set[str]:
        return {r[0] for r in self.refs}

    def render(self, resolve: Callable[[str, str | None, str | None], str]) -> str:
        return PLACEHOLDER.sub(lambda m: resolve(m.group(1), m.group(2), m.group(3)), self.text)


# ------------------------------------------------------------------ fields --

class FieldSpec:
    """One typed value: a whole number, a decimal with unit, a choice (inline or from a dictionary) or text."""

    KEYS = {"type", "label", "unit", "hint", "min", "max", "choices", "dictionary", "places", "pattern", "max_length",
            "more_than", "message", "key"}

    def __init__(self, name: str, spec: Any, where: str, dictionaries: dict[str, list[Choice]] | None = None):
        where = f"{where}, field '{name}'"
        if not isinstance(name, str) or not NAME_OK.match(name):
            raise DataError(f"{where}: field names use small letters, digits and _ and start with a letter")
        if not isinstance(spec, dict):
            raise DataError(f"{where}: must be a mapping such as {{type: int, min: 1}}")
        _check_keys(spec, self.KEYS, where)
        self.name = name
        self.type = spec.get("type")
        if self.type not in FIELD_TYPES:
            raise DataError(f"{where}: type must be one of {', '.join(FIELD_TYPES)} (found {self.type!r})")
        self.label = _text(spec, "label", where, False) or humanize(name)
        self.unit = _text(spec, "unit", where, False)
        self.hint = _text(spec, "hint", where, False)
        self.message = _text(spec, "message", where, False)
        self.more_than = _text(spec, "more_than", where, False)
        self.min = self.max = None
        self.places = None
        self.pattern = None
        self.max_length = None
        self.choices: list[Choice] | None = None
        self.numeric_choices: list[Decimal] | None = None
        self.dictionary = None
        if self.type in ("int", "decimal"):
            for k in ("min", "max"):
                if spec.get(k) is not None:
                    v = _number(spec[k], where, k)
                    if self.type == "int" and v != v.to_integral_value():
                        raise DataError(f"{where}: {k} must be a whole number for an int field")
                    setattr(self, k, v)
            if self.min is not None and self.max is not None and self.min > self.max:
                raise DataError(f"{where}: min ({fmt_decimal(self.min)}) is greater than max ({fmt_decimal(self.max)})")
            if spec.get("choices") is not None:
                if not isinstance(spec["choices"], list) or not spec["choices"]:
                    raise DataError(f"{where}: choices must be a list of numbers")
                nums = [_number(c, where, "every choice") for c in spec["choices"]]
                if self.type == "int" and any(n != n.to_integral_value() for n in nums):
                    raise DataError(f"{where}: an int field can only have whole-number choices")
                if len({n.normalize() for n in nums}) != len(nums):
                    raise DataError(f"{where}: a choice appears twice")
                self.numeric_choices = nums
            if spec.get("places") is not None:
                p = spec["places"]
                if self.type != "decimal" or isinstance(p, bool) or not isinstance(p, int) or not 0 <= p <= 6:
                    raise DataError(f"{where}: places (decimal places allowed) must be 0 to 6, on a decimal field")
                self.places = p
            for k in ("dictionary", "pattern", "max_length"):
                if spec.get(k) is not None:
                    raise DataError(f"{where}: '{k}' is not used with type {self.type}")
        elif self.type == "choice":
            has_dict, has_list = spec.get("dictionary") is not None, spec.get("choices") is not None
            if has_dict == has_list:
                raise DataError(f"{where}: a choice field needs either 'dictionary' or a list of 'choices'")
            if has_dict:
                self.dictionary = _text(spec, "dictionary", where)
                if dictionaries is not None and self.dictionary != CATEGORIES_DICTIONARY \
                        and self.dictionary not in dictionaries:
                    raise DataError(f"{where}: dictionary '{self.dictionary}' is not defined "
                                    f"(known: {', '.join(sorted(dictionaries)) or 'none'})")
            else:
                if not isinstance(spec["choices"], list) or not spec["choices"]:
                    raise DataError(f"{where}: choices must be a list")
                self.choices = [choice_from(c, f"{where}, choices") for c in spec["choices"]]
                codes = [c.code.upper() for c in self.choices]
                if len(set(codes)) != len(codes):
                    raise DataError(f"{where}: a choice appears twice")
        else:  # text
            if spec.get("pattern") is not None:
                pat = _text(spec, "pattern", where)
                try:
                    self.pattern = re.compile(pat)
                except re.error as e:
                    raise DataError(f"{where}: the pattern is not a valid regular expression ({e})") from None
            if spec.get("max_length") is not None:
                ml = spec["max_length"]
                if isinstance(ml, bool) or not isinstance(ml, int) or ml < 1:
                    raise DataError(f"{where}: max_length must be a whole number above 0")
                self.max_length = ml

    # -- choices
    def choice_list(self, config) -> list[Choice] | None:
        if self.type != "choice":
            return None
        if self.choices is not None:
            return self.choices
        if self.dictionary == CATEGORIES_DICTIONARY:
            return [Choice(c["code"], c.get("name", c["code"])) for c in (config.categories() if config else [])]
        return config.dictionary(self.dictionary) if config else []

    def to_dict(self, config) -> dict:
        out = {"name": self.name, "label": self.label, "type": self.type, "unit": self.unit, "hint": self.hint}
        if self.min is not None:
            out["min"] = fmt_decimal(self.min)
        if self.max is not None:
            out["max"] = fmt_decimal(self.max)
        if self.places is not None:
            out["places"] = self.places
        if self.numeric_choices is not None:
            out["choices"] = [{"value": fmt_decimal(n), "label": fmt_decimal(n) + (f" {self.unit}" if self.unit else "")}
                              for n in self.numeric_choices]
        elif self.type == "choice":
            out["choices"] = [c.to_dict() for c in self.choice_list(config) or []]
            out["dictionary"] = self.dictionary
        if self.pattern is not None:
            out["pattern"] = self.pattern.pattern
        return out

    # -- values
    def _range_text(self) -> str:
        u = f" {self.unit}" if self.unit else ""
        if self.min is not None and self.max is not None:
            return f"between {fmt_decimal(self.min)} and {fmt_decimal(self.max)}{u}"
        if self.min is not None:
            return f"at least {fmt_decimal(self.min)}{u}"
        return f"at most {fmt_decimal(self.max)}{u}"

    def parse(self, raw: Any, config=None, context: dict | None = None) -> Any:
        """Check one value. Returns a Decimal (int/decimal), a Choice or a str; raises FieldError in plain words."""
        empty = raw is None or (isinstance(raw, str) and not raw.strip())
        if empty:
            raise FieldError(f"{self.label}: {'choose one' if self.type == 'choice' else 'enter a value'}.")
        if self.type in ("int", "decimal"):
            if isinstance(raw, bool):
                raise FieldError(f"{self.label} must be a number.")
            text = str(raw).strip().replace(",", ".")
            try:
                d = Decimal(text)
            except InvalidOperation:
                raise FieldError(f"{self.label} must be a number (found '{raw}').") from None
            if not d.is_finite():
                raise FieldError(f"{self.label} must be a number (found '{raw}').")
            if self.type == "int" and d != d.to_integral_value():
                raise FieldError(f"{self.label} must be a whole number (found '{raw}').")
            if self.type == "int":
                d = d.quantize(Decimal(1))
            if self.places is not None and -d.normalize().as_tuple().exponent > self.places:
                raise FieldError(f"{self.label} may have at most {self.places} decimal place{'s' if self.places != 1 else ''}.")
            if self.numeric_choices is not None and all(d != c for c in self.numeric_choices):
                u = f" {self.unit}" if self.unit else ""
                raise FieldError(f"{self.label} must be one of {', '.join(fmt_decimal(c) for c in self.numeric_choices)}{u}.")
            if (self.min is not None and d < self.min) or (self.max is not None and d > self.max):
                raise FieldError(f"{self.label} must be {self._range_text()}.")
            if self.more_than and context is not None and context.get(self.more_than) not in (None, ""):
                limit = Decimal(str(context[self.more_than]))
                if d <= limit:
                    raise FieldError(self.message.replace("{" + self.more_than + "}", fmt_decimal(limit)) if self.message
                                     else f"{self.label} must be more than {fmt_decimal(limit)}.")
            return d
        if self.type == "choice":
            text = str(raw).strip()
            options = self.choice_list(config) or []
            for c in options:
                if c.code.upper() == text.upper():
                    return c
            for c in options:
                if c.name.upper() == text.upper():
                    return c
            if not options:
                raise FieldError(f"{self.label}: the list is empty. Ask your admin to add entries under Admin › Packs.")
            where = f"the {self.dictionary.replace('_', ' ')} list" if self.dictionary else "the list"
            raise FieldError(f"{self.label} '{text}' is not in {where}. Pick one from the list"
                             + (", or ask your admin to add it under Admin › Packs." if self.dictionary else "."))
        text = str(raw).strip().upper()
        if self.max_length and len(text) > self.max_length:
            raise FieldError(f"{self.label} may have at most {self.max_length} characters.")
        if self.pattern is not None and not self.pattern.search(text):
            raise FieldError(f"{self.label} '{text}' is not in the expected form" + (f" ({self.hint})." if self.hint else "."))
        return text


def value_text(v: Any, attr: str | None = None) -> str:
    if isinstance(v, Choice):
        return v.attr(attr)
    if isinstance(v, Decimal):
        return fmt_decimal(v)
    return "" if v is None else str(v)


def plain_values(values: dict) -> dict:
    """Values as JSON-friendly text, for the ledger and the item's properties."""
    return {k: value_text(v) for k, v in values.items()}


def check_refs(template: Template, where: str, fields: dict[str, FieldSpec], extra: Iterable[str] = (),
               attr_ok: Callable[[str, str], bool] | None = None) -> None:
    known = set(fields) | set(extra)
    for name, attr, width in template.refs:
        if name not in known:
            raise DataError(f"{where}: '{{{name}}}' is not one of the fields ({', '.join(sorted(known)) or 'none'})")
        if width and name != "seq":
            raise DataError(f"{where}: '{{{name}:{width}}}': only {{seq:N}} takes a width")
        if attr:
            f = fields.get(name)
            if f is None or f.type != "choice":
                raise DataError(f"{where}: '{{{name}.{attr}}}' only works on a choice field")
            if attr_ok and not attr_ok(name, attr):
                raise DataError(f"{where}: '{{{name}.{attr}}}': the entries of '{name}' have no '{attr}'")


def _attr_checker(fields: dict[str, FieldSpec], dictionaries: dict[str, list[Choice]] | None):
    def ok(name: str, attr: str) -> bool:
        if attr in ("code", "name"):
            return True
        f = fields[name]
        entries = f.choices if f.choices is not None else (dictionaries or {}).get(f.dictionary)
        if entries is None:      # dictionary not known yet: checked when used
            return True
        return bool(entries) and all(attr in c.extra for c in entries)
    return ok


# --------------------------------------------------------------- templates --

ITEM_KEYS = ("material", "uom", "make_buy", "item_type")


@dataclass
class BuildResult:
    ok: bool
    code: str = ""
    description: str = ""
    values: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    item: dict = field(default_factory=dict)

    def first_error(self) -> str:
        return next(iter(self.errors.values()), "")


class SpecTemplate:
    """A spec-code template, e.g. spur gear GR-{module}-{teeth}-{face_width_mm}-{material}."""

    KEYS = {"id", "category", "label", "hint", "code", "fields", "description", "item"}

    def __init__(self, data: Any, where: str, dictionaries: dict[str, list[Choice]] | None = None,
                 categories: set[str] | None = None):
        if not isinstance(data, dict):
            raise DataError(f"{where}: a spec-code template must be a mapping")
        _check_keys(data, self.KEYS, where)
        self.data = data
        self.id = _text(data, "id", where)
        if not ID_OK.match(self.id):
            raise DataError(f"{where}: id '{self.id}' may use small letters, digits and dashes only")
        where = f"{where} (template '{self.id}')"
        self.label = _text(data, "label", where)
        self.hint = _text(data, "hint", where, False)
        self.category = _text(data, "category", where)
        if categories is not None and self.category not in categories:
            raise DataError(f"{where}: category '{self.category}' is not defined "
                            f"(known: {', '.join(sorted(categories)) or 'none'})")
        fields = data.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise DataError(f"{where}: 'fields' must list at least one field")
        self.fields = {n: FieldSpec(n, s, where, dictionaries) for n, s in fields.items()}
        self.code = Template(data.get("code"), f"{where}: 'code'")
        self.description = Template(data.get("description"), f"{where}: 'description'")
        attr_ok = _attr_checker(self.fields, dictionaries)
        check_refs(self.code, f"{where}: 'code'", self.fields, attr_ok=attr_ok)
        check_refs(self.description, f"{where}: 'description'", self.fields, attr_ok=attr_ok)
        missing = [n for n in self.fields if n not in self.code.names()]
        if missing:
            raise DataError(f"{where}: every field must be part of the code, otherwise different parts would get "
                            f"the same code; add {', '.join('{' + m + '}' for m in missing)} to 'code'")
        item = data.get("item") or {}
        if not isinstance(item, dict):
            raise DataError(f"{where}: 'item' must be a mapping such as {{material: \"{{material.name}}\"}}")
        _check_keys(item, ITEM_KEYS, f"{where}: 'item'")
        self.item = {k: Template(v, f"{where}: item '{k}'") for k, v in item.items()}
        for k, t in self.item.items():
            check_refs(t, f"{where}: item '{k}'", self.fields, attr_ok=attr_ok)

    def parse_values(self, values: dict, config) -> tuple[dict, dict]:
        parsed, errors = {}, {}
        values = values if isinstance(values, dict) else {}
        for name, f in self.fields.items():
            try:
                parsed[name] = f.parse(values.get(name), config)
            except FieldError as e:
                errors[name] = str(e)
        return parsed, errors

    def build(self, values: dict, config) -> BuildResult:
        parsed, errors = self.parse_values(values, config)
        if errors:
            return BuildResult(False, errors=errors, values=plain_values(parsed))

        def resolve(name, attr, _w):
            return value_text(parsed[name], attr)
        code = self.code.render(resolve).strip().upper()
        if not CODE_OK.match(code):
            return BuildResult(False, code=code, errors={"_": f"The code '{code}' has characters a part number cannot "
                                                              "use (only letters, digits, - . _ /). Check the "
                                                              "dictionary codes under Admin › Packs."})
        desc = " ".join(self.description.render(resolve).split())
        item = {k: " ".join(t.render(resolve).split()) for k, t in self.item.items()}
        return BuildResult(True, code, desc, plain_values(parsed), {}, item)

    def to_dict(self, config) -> dict:
        cat = config.category(self.category) if config else None
        return {"id": f"spec:{self.id}", "kind": "spec", "template": self.id, "label": self.label, "hint": self.hint,
                "category": self.category, "category_name": (cat or {}).get("name", self.category),
                "code": self.code.text, "description": self.description.text,
                "fields": [f.to_dict(config) for f in self.fields.values()], "creates_item": True}


class SequenceScheme:
    """A number series such as {prefix}{seq:3}, {category}-{seq:5} or P-{yyyy}-{seq:3}."""

    KEYS = {"id", "label", "hint", "pattern", "fields", "creates_item", "item_type", "description"}
    AUTO = ("seq", "yyyy", "yy")

    def __init__(self, data: Any, where: str, dictionaries: dict[str, list[Choice]] | None = None):
        if not isinstance(data, dict):
            raise DataError(f"{where}: a number scheme must be a mapping")
        _check_keys(data, self.KEYS, where)
        self.data = data
        self.id = _text(data, "id", where)
        if not ID_OK.match(self.id):
            raise DataError(f"{where}: id '{self.id}' may use small letters, digits and dashes only")
        where = f"{where} (scheme '{self.id}')"
        self.label = _text(data, "label", where)
        self.hint = _text(data, "hint", where, False)
        self.item_type = _text(data, "item_type", where, False)
        ci = data.get("creates_item", True)
        if not isinstance(ci, bool):
            raise DataError(f"{where}: creates_item must be true or false")
        self.creates_item = ci
        fields = data.get("fields") or {}
        if not isinstance(fields, dict):
            raise DataError(f"{where}: 'fields' must be a mapping")
        for n in fields:
            if n in self.AUTO:
                raise DataError(f"{where}: '{n}' is filled in by Kosha and cannot be a field")
        self.fields = {n: FieldSpec(n, s, where, dictionaries) for n, s in fields.items()}
        self.pattern = Template(data.get("pattern"), f"{where}: 'pattern'")
        check_refs(self.pattern, f"{where}: 'pattern'", self.fields, extra=self.AUTO)
        seqs = [r for r in self.pattern.refs if r[0] == "seq"]
        if len(seqs) != 1:
            raise DataError(f"{where}: 'pattern' needs exactly one {{seq:N}} for the running number")
        self.width = int(seqs[0][2] or 3)
        if not 1 <= self.width <= 9:
            raise DataError(f"{where}: {{seq:N}} takes 1 to 9 digits")
        missing = [n for n in self.fields if n not in self.pattern.names()]
        if missing:
            raise DataError(f"{where}: field {', '.join(missing)} is not used in 'pattern'")
        self.description = Template(data["description"], f"{where}: 'description'") if data.get("description") else None
        if self.description:
            check_refs(self.description, f"{where}: 'description'", self.fields, extra=("yyyy", "yy"))

    def parts(self, values: dict, config, today: datetime | None = None) -> tuple[str, str, dict, dict]:
        """(text before the number, text after it, parsed values, errors)."""
        parsed, errors = {}, {}
        values = values if isinstance(values, dict) else {}
        for name, f in self.fields.items():
            try:
                parsed[name] = f.parse(values.get(name), config)
            except FieldError as e:
                errors[name] = str(e)
        if errors:
            return "", "", parsed, errors
        today = today or datetime.now(timezone.utc)
        marker = "\x00"

        def resolve(name, attr, _w):
            if name == "seq":
                return marker
            if name == "yyyy":
                return f"{today.year:04d}"
            if name == "yy":
                return f"{today.year % 100:02d}"
            return value_text(parsed[name], attr)
        text = self.pattern.render(resolve).strip().upper()
        before, after = text.split(marker)
        return before, after, parsed, errors

    def default_description(self, parsed: dict) -> str:
        if not self.description:
            return ""
        today = datetime.now(timezone.utc)
        return " ".join(self.description.render(
            lambda n, a, w: f"{today.year:04d}" if n == "yyyy" else f"{today.year % 100:02d}" if n == "yy"
            else value_text(parsed.get(n), a)).split())

    def to_dict(self, config) -> dict:
        out = {"id": self.id, "kind": "sequence", "label": self.label, "hint": self.hint, "pattern": self.pattern.text,
               "fields": [f.to_dict(config) for f in self.fields.values()], "creates_item": self.creates_item,
               "item_type": self.item_type, "available": True, "why": ""}
        for f in self.fields.values():
            if f.type == "choice" and not f.choice_list(config):
                out["available"] = False
                out["why"] = (f"There are no {f.label.lower()} entries yet. An admin can install a pack that has them "
                              "under Admin › Packs.")
        return out


BUILTIN_SCHEMES = [
    {"id": "prefix", "label": "Next number in a series",
     "hint": "Type the start of the number, for example MX-100- gives MX-100-001, MX-100-002 ... "
             "The same series the CAD add-ins use.",
     "pattern": "{prefix}{seq:3}",
     "fields": {"prefix": {"type": "text", "label": "Series (prefix)", "pattern": r"^[A-Z0-9][A-Z0-9\-_/]{0,20}$",
                           "hint": "letters, digits and dashes, for example MX-100-"}}},
    {"id": "category", "label": "Category number",
     "hint": "A running number per category, for example SHF-001234 for a shaft. The number says what the part is, "
             "never where it is used.",
     "pattern": "{category}-{seq:6}",
     "fields": {"category": {"type": "choice", "label": "Category", "dictionary": CATEGORIES_DICTIONARY}}},
    {"id": "document", "label": "Document number",
     "hint": "Specifications, calculations, manuals, procedures and test reports, for example DOC-004120.",
     "pattern": "DOC-{seq:6}", "item_type": "document"},
    {"id": "kit", "label": "Kit number",
     "hint": "Maintenance, shutdown, service and breakdown kits, for example KIT-000027.",
     "pattern": "KIT-{seq:6}", "item_type": "kit"},
    {"id": "machine-build", "label": "Machine serial number",
     "hint": "One number for each machine built for a customer order, for example MX26-0042 for the 42nd mixer "
             "built in 2026. It goes on the nameplate and links the as-built BOM.",
     "pattern": "{family}{yy}-{seq:4}", "item_type": "machine-build",
     "fields": {"family": {"type": "text", "label": "Product family code", "pattern": "^[A-Z]{2}$",
                           "hint": "Two letters, for example MX for mixers or PU for pumps"}}},
]
# series whose running counter keeps the width it started with, so numbers already issued stay in one format
KEEP_WIDTH = ("prefix", "category")
USAGE_WARNING = 0.8          # tell the data steward when a series has used this share of its numbers
VOID_AFTER_DAYS = 30         # a reserved number with nothing made from it is voided (never reused) after this
MAX_RESERVE = 500


# ------------------------------------------------------------------ service --

class NumberingError(ValueError):
    def __init__(self, message: str, errors: dict | None = None, code: str = "invalid_values"):
        super().__init__(message)
        self.errors = errors or {}
        self.code = code


def _upper(pn: str) -> str:
    return (pn or "").strip().upper()


class NumberingService:
    """Allocates part numbers. Every method that writes runs in one database transaction."""

    MAX_TRIES = 5

    def __init__(self, config=None):
        if config is None:
            from .packs import PackConfig
            config = PackConfig.current()
        self.config = config

    # -- schemes
    def sequence_schemes(self) -> dict[str, SequenceScheme]:
        out: dict[str, SequenceScheme] = {}
        dicts = self.config.dictionaries_with_entries()
        for data in BUILTIN_SCHEMES:
            out[data["id"]] = SequenceScheme(data, "built-in scheme", dicts)
        for rec in self.config.records("numbering_scheme"):
            try:
                out[rec.key] = SequenceScheme(rec.value, f"number scheme '{rec.key}'", dicts)
            except DataError:
                continue      # checked when saved; a broken one is left out rather than breaking the list
        return out

    def spec_templates(self) -> dict[str, SpecTemplate]:
        out: dict[str, SpecTemplate] = {}
        dicts = self.config.dictionaries_with_entries()
        for rec in self.config.records("spec_template"):
            try:
                out[rec.key] = SpecTemplate(rec.value, f"spec-code template '{rec.key}'", dicts)
            except DataError:
                continue
        return out

    def describe(self) -> list[dict]:
        out = [s.to_dict(self.config) for s in self.sequence_schemes().values()]
        out += [t.to_dict(self.config) for t in self.spec_templates().values()]
        return out

    def scheme(self, scheme_id: str):
        sid = (scheme_id or "").strip()
        if sid.startswith("spec:"):
            t = self.spec_templates().get(sid[5:])
            if t:
                return t
        else:
            s = self.sequence_schemes().get(sid)
            if s:
                return s
        raise NumberingError(f"There is no number scheme '{sid}'. Reload the page; an admin may have removed its pack.",
                             code="unknown_scheme")

    # -- lookups
    @staticmethod
    def taken(pn: str) -> bool:
        pn = _upper(pn)
        return bool(db.scalar("SELECT 1 FROM items WHERE part_number = ? COLLATE NOCASE", (pn,)) or
                    db.scalar("SELECT 1 FROM number_allocations WHERE part_number = ?", (pn,)))

    @staticmethod
    def find_item(code: str) -> dict | None:
        code = _upper(code)
        led = db.one("SELECT item_id FROM number_allocations WHERE (spec_code = ? OR part_number = ?) "
                     "AND item_id IS NOT NULL", (code, code))
        if led:
            it = db.one("SELECT * FROM items WHERE id = ?", (led["item_id"],))
            if it:
                return it
        return db.one("SELECT * FROM items WHERE part_number = ? COLLATE NOCASE", (code,))

    @staticmethod
    def _counter(key: str) -> dict | None:
        return db.one("SELECT next_value, width FROM numbering WHERE prefix = ?", (key,))

    def _next(self, before: str, after: str, width: int, keep_row_width: bool) -> tuple[str, int, int, str]:
        key = before if not after else f"{before}#{after}"
        row = self._counter(key)
        n = row["next_value"] if row else 1
        if row and keep_row_width:
            width = row["width"]
        while self.taken(f"{before}{n:0{width}d}{after}"):
            n += 1
        return f"{before}{n:0{width}d}{after}", n, width, key

    # -- preview
    def preview(self, scheme_id: str, values: dict, description: str = "") -> dict:
        s = self.scheme(scheme_id)
        if isinstance(s, SpecTemplate):
            r = s.build(values, self.config)
            if not r.ok:
                return {"ok": False, "errors": r.errors, "error": r.first_error(), "part_number": r.code,
                        "description": ""}
            existing = self.find_item(r.code)
            near = self.near_matches(s, r.values, r.code)
            msg = (f"{existing['part_number']} already exists. Kosha uses it instead of making a duplicate."
                   if existing else "This spec code is new. Kosha will create the part.")
            if near and not existing:
                msg += (f" {len(near)} similar part{'s' if len(near) > 1 else ''} differ in one value only; "
                        "check them first, one may do the job.")
            return {"ok": True, "errors": {}, "part_number": r.code, "description": r.description, "provisional": False,
                    "existing": _item_brief(existing), "near": near, "message": msg}
        before, after, parsed, errors = s.parts(values, self.config)
        if errors:
            return {"ok": False, "errors": errors, "error": next(iter(errors.values())), "part_number": "",
                    "description": description}
        pn, _n, _w, _k = self._next(before, after, s.width, s.id in KEEP_WIDTH)
        return {"ok": True, "errors": {}, "part_number": pn, "provisional": True, "existing": None,
                "description": description or s.default_description(parsed),
                "message": "This is the next free number. It is fixed when you press Create; if someone takes it "
                           "first, you get the one after."}

    # -- allocation
    def allocate(self, scheme_id: str, values: dict, user_id: int | None, description: str = "",
                 item_type: str = "") -> dict:
        s = self.scheme(scheme_id)
        if isinstance(s, SpecTemplate):
            r = s.build(values, self.config)
            if not r.ok:
                raise NumberingError(r.first_error(), r.errors)
            cat = self.config.category(s.category) or {}
            out = self.ensure_coded_item(
                scheme=f"spec:{s.id}", code=r.code, description=r.description, values=r.values, user_id=user_id,
                uom=r.item.get("uom") or cat.get("uom") or "", material=r.item.get("material", ""),
                make_buy=r.item.get("make_buy") or cat.get("make_buy") or "make",
                item_type=item_type or r.item.get("item_type", ""), props={"category": s.category})
            exact = [] if not out["existing"] else [{**_item_brief(out["item"]), "spec_code": r.code, "differs": ""}]
            out["existing_matches"] = exact + self.near_matches(s, r.values, r.code)
            return out
        return self._allocate_sequence(s, values, user_id, description, item_type)

    def _allocate_sequence(self, s: SequenceScheme, values: dict, user_id, description: str, item_type: str) -> dict:
        before, after, parsed, errors = s.parts(values, self.config)
        if errors:
            raise NumberingError(next(iter(errors.values())), errors)
        description = (description or "").strip() or s.default_description(parsed)
        plain = plain_values(parsed)
        cat_field = next((n for n, f in s.fields.items() if f.dictionary == CATEGORIES_DICTIONARY), None)
        cat_code = plain.get(cat_field, "") if cat_field else ""
        cat = (self.config.category(cat_code) or {}) if cat_code else {}
        for attempt in range(self.MAX_TRIES):
            try:
                with db.tx():
                    pn, n, width, key = self._next(before, after, s.width, s.id in KEEP_WIDTH)
                    db.execute("INSERT INTO numbering(prefix, next_value, width) VALUES (?, ?, ?) "
                               "ON CONFLICT(prefix) DO UPDATE SET next_value = excluded.next_value", (key, n + 1, width))
                    item_id = None
                    if s.creates_item:
                        item_id = self._create_item(pn, description, user_id, uom=cat.get("uom", ""), material="",
                                                    make_buy=cat.get("make_buy") or None,
                                                    item_type=item_type or s.item_type,
                                                    props={"numbering": {"scheme": s.id, "values": plain},
                                                           **({"category": cat_code} if cat_code else {})})
                    self._ledger(pn, s.id, key, n, None, plain, item_id, user_id)
                    db.audit(user_id, "number_allocate", "item" if item_id else "number", item_id,
                             f"{pn} ({s.label})")
                break
            except sqlite3.IntegrityError:
                if attempt == self.MAX_TRIES - 1:
                    raise
        item = db.one("SELECT * FROM items WHERE id = ?", (item_id,)) if item_id else None
        return {"part_number": pn, "item": item, "existing": False, "scheme": s.id, "existing_matches": [],
                "warning": self._usage_warning(key, before, after, n, width, user_id),
                "message": f"{pn} is yours." + (" The part has been created." if item else "")}

    def reserve(self, scheme_id: str, values: dict, count: int, user_id: int | None, description: str = "") -> dict:
        """Reserve ``count`` numbers of one series in one go (configurators, copy design, imports). No items are
        made yet; a reserved number nothing is made from within 30 days is voided and never handed out again."""
        s = self.scheme(scheme_id)
        if isinstance(s, SpecTemplate):
            raise NumberingError("Spec codes are made from their values, so they cannot be reserved in advance. "
                                 "Choose a number series instead.", code="not_a_series")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_RESERVE:
            raise NumberingError(f"Reserve between 1 and {MAX_RESERVE} numbers at a time.", code="bad_count")
        before, after, parsed, errors = s.parts(values, self.config)
        if errors:
            raise NumberingError(next(iter(errors.values())), errors)
        plain = plain_values(parsed)
        if description:
            plain = {**plain, "_note": description.strip()[:200]}
        for attempt in range(self.MAX_TRIES):
            numbers = []
            try:
                with db.tx():
                    for _ in range(count):
                        pn, n, width, key = self._next(before, after, s.width, s.id in KEEP_WIDTH)
                        db.execute("INSERT INTO numbering(prefix, next_value, width) VALUES (?, ?, ?) "
                                   "ON CONFLICT(prefix) DO UPDATE SET next_value = excluded.next_value",
                                   (key, n + 1, width))
                        self._ledger(pn, s.id, key, n, None, plain, None, user_id)
                        numbers.append(pn)
                    db.audit(user_id, "number_reserve", "number", None,
                             f"{count} numbers {numbers[0]} to {numbers[-1]} ({s.label})")
                break
            except sqlite3.IntegrityError:
                if attempt == self.MAX_TRIES - 1:
                    raise
        return {"numbers": numbers, "scheme": s.id,
                "warning": self._usage_warning(key, before, after, n, width, user_id),
                "message": (f"{count} number{'s' if count > 1 else ''} reserved, {numbers[0]}"
                            + (f" to {numbers[-1]}" if count > 1 else "") + f". Numbers nothing is made from within "
                            f"{VOID_AFTER_DAYS} days are voided and never used again.")}

    # -- near matches
    def near_matches(self, template: SpecTemplate, values: dict, code: str, limit: int = 5) -> list[dict]:
        """Parts made from the same template whose values differ in exactly one field, e.g. the same gear with 41
        instead of 40 teeth. Shown before a new spec code is created, so an existing part can be reused."""
        code = _upper(code)
        out = []
        for row in db.rows("SELECT a.spec_code, a.values_json, i.id, i.part_number, i.description, i.state, i.revision "
                           "FROM number_allocations a JOIN items i ON i.id = a.item_id "
                           "WHERE a.scheme = ? AND a.spec_code IS NOT NULL AND a.spec_code <> ? ORDER BY a.id",
                           (f"spec:{template.id}", code)):
            try:
                theirs = json.loads(row["values_json"] or "{}")
            except ValueError:
                continue
            diff = [n for n in template.fields if str(theirs.get(n, "")).upper() != str(values.get(n, "")).upper()]
            if len(diff) != 1:
                continue
            f = template.fields[diff[0]]
            out.append({"id": row["id"], "part_number": row["part_number"], "description": row["description"],
                        "state": row["state"], "revision": row["revision"], "spec_code": row["spec_code"],
                        "differs": f"{f.label}: {theirs.get(diff[0])} instead of {values.get(diff[0])}"})
            if len(out) >= limit:
                break
        return out

    # -- capacity
    @staticmethod
    def _usage_warning(key: str, before: str, after: str, n: int, width: int, user_id) -> str:
        """Say so when a series has used 80% of its numbers, and tell the admins once per series."""
        top = 10 ** width - 1
        if n < USAGE_WARNING * top:
            return ""
        series = f"{before}{'N' * width}{after}"
        msg = (f"The series {series} has used {n:,} of its {top:,} numbers ({n * 100 // top}%). Ask the data steward "
               "to plan a new category or a longer number before it runs out.")
        if not db.scalar("SELECT 1 FROM audit WHERE action = 'number_capacity' AND entity = 'series' "
                         "AND details LIKE ?", (f"{series}:%",)):
            with db.tx():
                db.audit(user_id, "number_capacity", "series", None, f"{series}: {n} of {top} used")
            from . import notify_outbox
            admins = [r["id"] for r in db.rows("SELECT id FROM users WHERE active = 1 AND role = 'admin'")]
            notify_outbox.enqueue(admins, f"Number series {series} is 80% used", msg, "#/admin/rules", "general",
                                  severity="warning", key=f"number_capacity:{key}")
        return msg

    @staticmethod
    def capacity() -> list[dict]:
        """How full each running series is, fullest first."""
        out = []
        for r in db.rows("SELECT prefix, next_value, width FROM numbering"):
            top = 10 ** r["width"] - 1
            used = max(r["next_value"] - 1, 0)
            before, _, after = r["prefix"].partition("#")
            out.append({"series": f"{before}{'N' * r['width']}{after}", "used": used, "of": top,
                        "percent": round(used * 100 / top, 1), "warning": used >= USAGE_WARNING * top})
        return sorted(out, key=lambda x: -x["percent"])

    # -- voiding
    def void_unused(self, days: int = VOID_AFTER_DAYS, now: datetime | None = None) -> int:
        """Void numbers handed out at least ``days`` ago that nothing was made from: no item and no file carries
        them. They stay in the ledger, so they are never issued again. Numbers of series that never make items
        (projects) are left alone. Returns how many were voided."""
        from datetime import timedelta
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(timespec="seconds")
        keep = [sid for sid, sc in self.sequence_schemes().items() if not sc.creates_item]
        rows = db.rows("SELECT a.id, a.part_number, a.scheme FROM number_allocations a "
                       "WHERE a.item_id IS NULL AND a.voided_at IS NULL AND a.allocated_at < ? "
                       "AND NOT EXISTS (SELECT 1 FROM items i WHERE i.part_number = a.part_number COLLATE NOCASE) "
                       "AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.part_number = a.part_number "
                       "COLLATE NOCASE AND d.deleted = 0)", (cutoff,))
        rows = [r for r in rows if r["scheme"] not in keep]
        if not rows:
            return 0
        with db.tx():
            stamp = db.now()
            for r in rows:
                db.execute("UPDATE number_allocations SET voided_at = ? WHERE id = ? AND voided_at IS NULL",
                           (stamp, r["id"]))
            db.audit(None, "number_void", "number", None,
                     f"{len(rows)} unused number{'s' if len(rows) > 1 else ''} voided after {days} days: "
                     + ", ".join(r["part_number"] for r in rows[:20]) + (" ..." if len(rows) > 20 else ""))
        return len(rows)

    def next_in_series(self, prefix: str, width: int, user_id: int | None) -> str:
        """Same result as /api/numbering/next (the CAD add-ins), but recorded in the ledger."""
        prefix = _upper(prefix)
        with db.tx():
            pn, n, width, key = self._next(prefix, "", width, True)
            db.execute("INSERT INTO numbering(prefix, next_value, width) VALUES (?, ?, ?) "
                       "ON CONFLICT(prefix) DO UPDATE SET next_value = excluded.next_value", (key, n + 1, width))
            self._ledger(pn, "prefix", key, n, None, {"prefix": prefix}, None, user_id)
        self._usage_warning(key, prefix, "", n, width, user_id)
        return pn

    def ensure_coded_item(self, *, scheme: str, code: str, description: str, values: dict, user_id: int | None,
                          uom: str = "", material: str = "", make_buy: str = "make", item_type: str = "",
                          props: dict | None = None) -> dict:
        """Find before you create: return the item with this code, or create it. Safe with concurrent callers."""
        code = _upper(code)
        for attempt in range(self.MAX_TRIES):
            try:
                with db.tx():
                    existing = self.find_item(code)
                    if existing:
                        if not db.scalar("SELECT 1 FROM number_allocations WHERE part_number = ?",
                                         (_upper(existing["part_number"]),)):
                            self._ledger(existing["part_number"], scheme, None, None, code, values, existing["id"],
                                         user_id)
                        item, created = existing, False
                    else:
                        iid = self._create_item(code, description, user_id, uom=uom, material=material,
                                                make_buy=make_buy, item_type=item_type,
                                                props={"spec": {"scheme": scheme, "code": code, "values": values},
                                                       **(props or {})})
                        self._ledger(code, scheme, None, None, code, values, iid, user_id)
                        db.audit(user_id, "item_create", "item", iid, f"{code} from {scheme}")
                        item, created = db.one("SELECT * FROM items WHERE id = ?", (iid,)), True
                break
            except sqlite3.IntegrityError:
                if attempt == self.MAX_TRIES - 1:
                    raise
        return {"part_number": item["part_number"], "item": item, "existing": not created, "scheme": scheme,
                "message": (f"{item['part_number']} already exists, so Kosha used it instead of making a duplicate."
                            if not created else f"{item['part_number']} has been created and is ready for BOMs.")}

    # -- internals
    def _create_item(self, pn: str, description: str, user_id, uom: str, material: str, make_buy: str | None,
                     item_type: str, props: dict) -> int:
        base = "part"
        if item_type:
            it = self.config.item_type(item_type)
            if it:
                base = it.get("base", "part")
                props = {**props, "item_type": item_type}
            elif item_type in ("part", "assembly"):
                base = item_type
        iid = boms.ensure_item(pn, description, user_id, uom or "Nos", material or "", make_buy or None)
        db.update("items", iid, {"item_type": base, "props_json": json.dumps(props), "updated_at": db.now()})
        return iid

    @staticmethod
    def _ledger(pn, scheme, key, seq, spec_code, values, item_id, user_id) -> None:
        db.insert("number_allocations", {
            "part_number": _upper(pn), "scheme": scheme, "counter_key": key, "seq": seq,
            "spec_code": _upper(spec_code) if spec_code else None, "values_json": json.dumps(values or {}),
            "item_id": item_id, "allocated_by": user_id, "allocated_at": db.now()})

    @staticmethod
    def recent(limit: int = 50) -> list[dict]:
        return db.rows("SELECT a.*, i.description FROM number_allocations a LEFT JOIN items i ON i.id = a.item_id "
                       "ORDER BY a.id DESC LIMIT ?", (limit,))


def _item_brief(it: dict | None) -> dict | None:
    if not it:
        return None
    return {k: it[k] for k in ("id", "part_number", "description", "state", "revision")}
