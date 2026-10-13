# File exchange connector for Kosha

Licence: Apache-2.0. For any ERP that Kosha has no connector for: Kosha writes every released item and BOM as
files into an exchange folder, and the ERP's own import (a scheduled job, an import tool, a script) reads them.
Optionally the ERP writes files back: stock and cost, acknowledgements, and exports of its items and BOMs for
Kosha's reconciliation check. No network access and no API are needed on either side.

## How it behaves

- **One transfer, one file pair.** Each item and each BOM Kosha sends becomes `to-erp/<sequence>_item_<part>_<hash>.csv`
  and `.json` (or only one of them, as chosen in the settings). Items always come before the BOMs that use them,
  so importing in sequence order is enough.
- **Atomic.** Each file is written to a hidden temporary file in the same folder (`.kosha-*.tmp`), flushed to disk
  (fsync), then renamed. The ERP never sees half a file. If the server stops in the middle, the temporary file is
  left behind and removed by a later send (after a minute); the transfer is written again in full.
- **Idempotent.** Kosha remembers what it last wrote for each item and BOM (in its own data folder, not in the
  exchange folder), so the same content is never written twice, even after the ERP has taken the files away.
  File names follow from the content and the sequence number, so a send interrupted after the rename is finished,
  not written a second time.
- **Written again on purpose** in two cases only: the ERP's acknowledgement file says it rejected the last file for
  that item or BOM, or the ERP's export (newer than Kosha's last file) shows that the ERP holds something else, for
  example after a user pressed *Send again* on a reconciliation difference. The new file has a new sequence number.
- **Dry run** is handled by Kosha: while it is on, nothing is written.
- **Errors say what happened and that nothing was lost**, for example when the folder does not exist (a share that
  is not connected) or cannot be written.

## Set up

1. Choose a folder both Kosha and the ERP's import can reach, for example `/srv/erp-exchange` on the Kosha server, or
   a network share connected there. Kosha creates the `to-erp` subfolder.
2. Set up the ERP's import to read `to-erp/*.csv` or `to-erp/*.json` in name order, to **ignore files that start
   with a dot or end in `.tmp`**, and to move or delete each file after importing it. Import each `sequence`
   number once (the safest key: a file written again on purpose has a new one).
3. Optional: let the ERP write a stock file, an acknowledgement file and item and BOM exports (layouts below), for
   example into a `from-erp` subfolder, and name them in the settings, relative to the exchange folder.
4. In Kosha, choose **File exchange** as the ERP system under **ERP sync › Connection settings**, fill in the
   folder, press **Test connection**, then untick **Dry run** and tick **Connect**. Map Kosha's units to the ERP's
   unit names under *Unit mapping*; the mapped name is what the files contain.

| Setting | Meaning |
| --- | --- |
| Exchange folder | A full path on the Kosha server, e.g. `/srv/erp-exchange` or `D:\erp-exchange` |
| Files to write for each transfer | CSV and JSON, CSV only, or JSON only |
| CSV separator | Comma, semicolon (for spreadsheet programs set to a decimal comma) or tab. Numbers always use a decimal point |
| Stock file from the ERP | Optional, e.g. `from-erp/stock.csv` |
| Acknowledgement file from the ERP | Optional, e.g. `from-erp/ack.csv` |
| Item export / BOM export from the ERP | Optional, for reconciliation, e.g. `from-erp/items.csv` and `from-erp/boms.csv` |

## Files Kosha writes (schema `kosha.erp-exchange/1`)

Files are UTF-8, CSV with a header row and CRLF line ends. The columns and keys below do not change within schema
version 1; new optional columns may be added at the end, so read columns by name.

**Item, CSV** (`000001_item_DE-300-001_3f2a9c0d1e.csv`), one row:

| Column | Content |
| --- | --- |
| `record` | always `item` |
| `code` | part number, exactly as in Kosha |
| `description` | description |
| `revision` | released revision |
| `uom` | unit, after Kosha's unit mapping |
| `material` | material (may be empty) |
| `weight_kg` | weight in kg (may be empty) |
| `item_group` | item group (may be empty) |
| `make_or_buy` | `make`, `buy` or empty |
| `content_hash` | fingerprint of the content above; equal content gives an equal hash |
| `sequence` | number of this transfer; grows by one with every transfer Kosha writes |
| `written_at` | time written, UTC, ISO 8601 (`2026-10-13T08:15:00Z`) |

**BOM, CSV** (`000004_bom_DE-300-000_9b1c...csv`), one row per line, in line order:
`record` (always `bom_line`), `parent_code`, `parent_description`, `bom_revision`, `line_no` (1, 2, ...),
`component_code`, `qty` (decimal point, e.g. `2.5`), `uom`, `content_hash`, `sequence`, `written_at`.
A BOM always describes the complete list of lines for the parent: replace the ERP's BOM, do not add to it.

**JSON** (same name, `.json`):

```json
{
  "schema": "kosha.erp-exchange/1",
  "kind": "bom",
  "sequence": 4,
  "content_hash": "9b1c4e...",
  "written_at": "2026-10-13T08:15:02Z",
  "bom": {
    "code": "DE-300-000", "description": "CONVEYOR ASSY", "revision": "A",
    "lines": [{"line_no": 1, "code": "DE-300-001", "qty": 6.0, "uom": "Nos"},
              {"line_no": 2, "code": "DE-300-002", "qty": 2.0, "uom": "Nos"}]
  }
}
```

For an item, `kind` is `item` and the record is under `item`, with the same keys as the item CSV columns `code`
to `make_or_buy` (`weight_kg` is a number or `null`).

## Files the ERP can write back (all optional, CSV)

The separator (comma, semicolon, tab or `|`) is detected; column names are not case-sensitive; a UTF-8 byte order
mark is fine; numbers may use a decimal point or a decimal comma. Write these files atomically too (to a temporary
name, then rename), so Kosha never reads half a file.

| File | Columns | Used for |
| --- | --- | --- |
| Stock | `code`, `stock`, `cost`, `warehouse` (one row per item, or per item and warehouse; stock is added up) | Stock and cost shown on items. An item Kosha sent that is not listed counts as zero stock |
| Acknowledgement | `file` (name of the transfer file, with or without extension), `status` (`ok`/`imported`, or `error`/`rejected`), `message` | A rejected transfer is written again on the next send; *Test connection* shows the ERP's message |
| Item export | `code`, and any of `description`, `uom`, `revision`, `material`, `active` (yes/no), `from_kosha` (yes/no) | Reconciliation. A column the export does not have is not compared; without `from_kosha`, Kosha does not look for items that exist only in the ERP |
| BOM export | `parent_code`, `component_code`, `qty`, and optionally `uom`, `revision`, `reference` | Reconciliation of the BOM the ERP uses for each parent |

## Tests

```bash
pytest -q addins/file-exchange/tests
```

The tests use temporary folders and `tests/fake_erp_folder.py`, a simulated ERP that imports the files (JSON or
CSV) and writes the files above back. They include real crashes (a separate process that stops between writing
the temporary file and renaming it, and between renaming and recording) and parallel sends.
