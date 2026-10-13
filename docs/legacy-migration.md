# Legacy migration

**Admin › Import legacy data** reads an old folder tree (a network share or a disk the server can reach) and checks
every file in once. Each run is a *batch*. Start with a dry run: it changes nothing and gives the Excel report.

## What a batch does

- Junk (`.bak`, `.dwl`, `~$` lock files, `Thumbs.db` …) is set aside and listed, never imported.
- **ZIP archives** are unpacked into a staging folder; what is inside is imported into a folder named after the
  archive. A damaged archive, one with a password, unsafe paths (`../`) or one that unpacks to far more than it
  should is reported as an error and nothing from it is imported. RAR and 7Z are not unpacked (no permissive
  library); they can be imported as files.
- Identical copies are stored once; the copy with the best name is kept and the paths of the others are remembered.
- Part number, description, material and revision come from the part master spreadsheet, the title block or the
  file name.

## Quality score and work queues

Every imported file gets a score from 0 to 100, and every point has a reason, shown under **Why**:

| | Points |
| --- | --- |
| Part number from the part master or title block (from the file name only: 20) | 30 |
| Part number agrees everywhere it was found | 10 |
| Description from the part master or title block (from the file name only: 12) | 20 |
| Material found | 15 |
| Revision mark read | 15 |
| Picture made | 10 |

**Auto** is 90 and above, **Review** 60 to 89, **Attention** below 60.

## Review grid

**Review** on a batch opens its grid: one row per file with its picture, old path, part number, description,
material, revision, score and reasons. Correct a cell and press **Confirm** (or **Save**). Select rows to confirm
them all at once, optionally setting the description or material of all of them. A corrected part number is linked
to its part; the old number stays searchable.

**Export this queue to Excel** gives the same rows. Correct them offline, write *yes* under Confirm, and **Import
corrections from Excel**. Every row is checked first; if one is wrong nothing is changed, and the problems are listed
by row.

## Old paths and numbers

The old path of every file, the paths of its identical copies and any old part number stay searchable for ever:
searching for `Copy of AG-BH-01` or an old number finds the file. The file's **History** tab lists them under
*Before Kosha*.

## Count check, sample and sign-off

The batch page shows *files found = imported + identical copies + already in Kosha + set aside + archives unpacked
+ errors*. A batch is signed off by an admin when:

1. the numbers add up,
2. **Read back stored files** found every stored file with its fingerprint (SHA-256), and
3. a random sample (**Draw a sample to check**: 2 %, at least 5, at most 50 files) was opened by an engineer and
   marked right or wrong, with at least 98 % right.

The button says what is still missing.

## Undo batch

Before sign-off, **Undo batch** takes back what the batch made, as long as nobody has used it since: its files go
to the recycle bin (they can be put back, and get their part again), the parts and folders it created go when
nothing else uses them, and its cross-references go. Files that were checked out, changed, revised, put on a change
order or used in a BOM stay, and are listed with the reason.

## API

`GET /api/import/jobs/{id}`, `GET /api/import/jobs/{id}/records?queue=&q=&unconfirmed=1`,
`PATCH /api/import/records/{id}`, `POST /api/import/jobs/{id}/records/confirm`,
`GET|POST /api/import/jobs/{id}/records.xlsx`, `GET /api/import/jobs/{id}/reconcile`,
`POST /api/import/jobs/{id}/verify`, `POST /api/import/jobs/{id}/sample`, `PUT /api/import/records/{id}/sample`,
`POST /api/import/jobs/{id}/sign-off`, `GET|POST /api/import/jobs/{id}/undo`.
