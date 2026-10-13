# Industry packs and standard-parts data

Licence: CC BY 4.0 (see `LICENSES/CC-BY-4.0.txt`). Packs are data, not code: YAML for structure
and CSV for tables. They give a new Kosha installation sensible categories, spec-code templates,
number schemes, dictionaries and a library of market components (fasteners, bearings ...), so a
company does not start from an empty system.

An admin installs, updates and removes packs under **Admin › Packs**. Kosha records what each pack
installed separately from what the company changed: updating a pack keeps every local change, and
removing a pack switches its data off but never deletes items or part numbers made from it.
The server reads packs from this folder, or from the folder in `KOSHA_PACKS_DIR`.

| Pack | Holds |
| --- | --- |
| `general-machinery` | Product families, the specification's categories and item types, materials, project and project-drawing number schemes, spec codes for spur gears, shafts and flanges, a title-block map |
| `engineered-components` | Categories for bought-to-spec components and a spec code for electric motors |
| `spares-and-service` | Categories and item types for spares, service kits and consumables; spec code for filter elements |
| `india-localisation` | Item fields for HSN code and GST rate; units with their GST unit quantity codes |
| `standard-parts` | Market components by standard: hex screws and bolts, nuts, washers, socket head cap screws, deep-groove ball bearings, parallel keys |

## Format

`<pack>/pack.yaml` has `id` (same as the folder name), `name`, `version` (`1.0.0`), `licence`,
`description` and any of these lists:

- `families`: `{code: PU, name: Pumps, examples: "Centrifugal pumps"}` (two capital letters, used in machine model and serial numbers).
- `categories`: `{code: GER, name: Gears, item_type: configured, make_buy: make, required: [material], erp_item_group: Gears, hsn: "8483"}` (code: 1 to 6 capitals or digits; `required` lists the fields a part needs before review).
- `item_types`: `{code: weldment, name: Weldment, base: assembly}` (`base` is part or assembly; `erp: false` keeps items of the type out of the ERP).
- `units`: `{code: Nos, name: Numbers, uqc: NOS}`; `fields`: extra item fields (`key`, `label`, `type` ...).
- `dictionaries`: named lists of `{code, name}` entries, for example `materials`. Write codes in quotes
  when they look like numbers (`"1.4401"`).
- `spec_codes`: files with spec-code templates. Every field must appear in `code`, so different
  values never give the same code. Field types: `int` (`min`, `max`, `choices`), `decimal` (`unit`,
  `min`, `max`, `places`, `choices`), `choice` (`dictionary` or `choices`) and `text` (`pattern`).
  `{field.name}` uses a dictionary entry's name in the description.
- `numbering`: number series such as `pattern: "PRJ-{yy}-{seq:3}"`; `creates_item: false` for
  numbers that are not parts (projects).
- `title_blocks`: drawing attribute names and the Kosha field each fills.
- `tables`: standard-parts tables, each a CSV file with one row per size, its `columns`, the
  `options` chosen when a part is used (length, property class ...) and templates for the
  `designation`, `part_number` and `description`.

Kosha checks everything when it reads a pack; errors name the file and the row.

## Rules for data

- Every row names its source standard (for example `ISO 4017`) and, once checked against the
  standard, its edition and `verified: yes`. Rows typed from memory or secondary sources stay
  `verified: no` until a reviewer checks them.
- Only sizes and designations, which are facts. Never copy text, drawings or tables wholesale from
  paid standards, and never take data from catalogue services or libraries whose terms forbid
  redistribution or require a different licence.
- Change the `version` when you change a pack, so installations see the update.
