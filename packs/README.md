# Industry packs and standard-parts data

Licence: CC BY 4.0 (see `LICENSES/CC-BY-4.0.txt`). Packs are data, not code: YAML for structure
and CSV for tables. They give a new Kosha installation sensible categories, spec-code templates,
dictionaries and a library of market components (fasteners, bearings ...), so a company does not
start from an empty system.

> **Format preview.** The server loads packs from version 1.0. The files here fix the format so
> contributors can start adding data now.

| Pack | Holds |
| --- | --- |
| `general-machinery` | Categories and spec-code templates for common machine parts (gears, shafts, flanges ...) |
| `standard-parts` | Market components by standard: fasteners, bearings, seals ... |

## Rules for data

- Every row names its source standard (for example `ISO 4017`) and, once checked against the
  standard, its edition and `verified: yes`. Rows typed from memory or secondary sources stay
  `verified: no` until a reviewer checks them.
- Only sizes and designations, which are facts. Never copy text, drawings or tables wholesale from
  paid standards, and never take data from catalogue services or libraries whose terms forbid
  redistribution or require a different licence.
