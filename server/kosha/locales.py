# SPDX-License-Identifier: AGPL-3.0-or-later
"""Locales and languages (specification 5.1).

A **locale** sets how dates, times, numbers and money are written; the installation has one (Admin › Company), India
by default: 10-Oct-2026, 24-hour time in IST, ₹1,25,000 and ₹2.4 lakh in summaries. A **language** is the words on
screen; each person chooses theirs (Account menu › Language). Screen text lives in translation files under
``web/locales`` (``en.json`` is complete; other languages may cover part of it, and the rest stays in English), so a
language is added without changing code.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, db
from .web import bad

LOCALES = {
    "en-IN": {"name": "India", "date": "DD-Mon-YYYY", "time_zone": "Asia/Kolkata", "hour12": False,
              "number": "en-IN", "currency": "INR", "big_numbers": "lakh"},
    "en-GB": {"name": "United Kingdom", "date": "DD-Mon-YYYY", "time_zone": "Europe/London", "hour12": False,
              "number": "en-GB", "currency": "GBP", "big_numbers": ""},
    "en-US": {"name": "United States", "date": "Mon DD, YYYY", "time_zone": "America/New_York", "hour12": True,
              "number": "en-US", "currency": "USD", "big_numbers": ""},
    "de-DE": {"name": "Germany", "date": "DD.MM.YYYY", "time_zone": "Europe/Berlin", "hour12": False,
              "number": "de-DE", "currency": "EUR", "big_numbers": ""},
}
DEFAULT = "en-IN"
LOCALE_DIR = Path(config.STATIC_DIR) / "locales"


def languages() -> list[dict]:
    """The languages with a translation file, and how much of the screen text each covers."""
    try:
        base = json.loads((LOCALE_DIR / "en.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [{"code": "en", "name": "English", "covered": 100}]
    out = []
    for f in sorted(LOCALE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        keys = [k for k in data if not k.startswith("_")]
        out.append({"code": f.stem, "name": data.get("_name", f.stem),
                    "covered": round(100 * len(set(keys) & set(base)) / max(1, len([k for k in base if not k.startswith("_")])))})
    return sorted(out, key=lambda x: (x["code"] != "en", x["name"]))


def current() -> dict:
    code = db.get_setting("locale") or DEFAULT
    if code not in LOCALES:
        code = DEFAULT
    return {"code": code, **LOCALES[code]}


def check(code: str) -> str:
    if code not in LOCALES:
        raise bad("Choose one of the locales: " + ", ".join(f"{v['name']} ({k})" for k, v in LOCALES.items()) + ".")
    return code
