<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Kosha export format (version 1)

A company can always take all of its data out of Kosha. `python -m kosha.export <file.zip>` writes one ZIP file
with every database table and every stored file; `python -m kosha.export --verify <file.zip>` checks it.

```bash
cd /opt/kosha/server            # or the server folder of your checkout
KOSHA_DATA_DIR=/var/lib/kosha python -m kosha.export /srv/exports/kosha-2026-10-13.zip
python -m kosha.export --verify /srv/exports/kosha-2026-10-13.zip
```

The export can run while Kosha is running: all tables are read in one consistent snapshot, and stored files never
change once written. It never overwrites an existing file. Exit codes: `0` complete, `1` written but incomplete
(a stored file was missing or damaged on the server, see the messages) or, with `--verify`, damaged, `2` not
written.

**Keep exports as safe as the server.** They contain everything: user accounts with their password hashes, the
audit log, and add-in settings such as ERP keys.

## Layout

| Path in the ZIP | Content |
| --- | --- |
| `manifest.json` | What the export contains, with row counts and SHA-256 checksums (below). |
| `README.txt` | A short description of this layout. |
| `tables/<table>.jsonl` | One file per database table, one JSON object per row. |
| `files/<ab>/<sha256>` | Every stored file, named by the SHA-256 of its content; `<ab>` is the first two characters. |
| `files.jsonl` | The list of stored files: `{"sha256": "...", "size": 1234, "path": "files/ab/ab..."}`. |
| `index/versions.jsonl` | Every file version with its folder path and file name (below). |

The ZIP uses Deflate compression and ZIP64 where needed, so any current unzip tool opens it.

## Tables

Each line of `tables/<table>.jsonl` is one row: a JSON object whose keys are the column names. Text, numbers and
`null` are written as they are. Binary values are written as `{"$base64": "..."}`. Text is UTF-8. Times are ISO
8601 text in UTC, as Kosha stores them (for example `2026-10-13T09:30:00+00:00`). Lists and settings that Kosha keeps
as JSON text (columns ending in `_json`, and `settings.value_json`) stay JSON text inside the string.

The tables are those of the Kosha version named in the manifest; the manifest also lists the database changes
(`schema_migrations`) that had been applied, and for each table its columns and its `CREATE TABLE` statement. The
main ones:

| Table | Holds |
| --- | --- |
| `folders` | The folder tree (`parent_id` is `null` for the top folder). |
| `documents` | Files: folder, name, part number, revision, lifecycle state, `latest_version`, `deleted` (soft delete). |
| `versions` | Every version of every file: `document_id`, `version`, `revision`, `blob_sha` (the stored file), size, comment, who and when; `preview_sha` and `viewer_sha` name generated previews. |
| `items` | The part master: part number, description, material, unit, revision and state. |
| `boms`, `bom_lines` | Engineering BOMs per item revision, and their lines (child item, quantity, find number). |
| `change_orders`, `co_items`, `co_approvers`, `co_comments` | Change orders, the files they change, approvals and comments. |
| `lifecycle_events` | Every state change of every file. |
| `users` | Accounts (`password_hash` is a salted scrypt hash, never the password). |
| `audit` | The audit log. |
| `erp_jobs`, `import_jobs`, `cp_*` | ERP transfers, legacy imports, duplicate-finder results. |
| `settings` | Kosha's settings (`value_json` is JSON text). |
| `sessions`, `sign_in_failures` | Sign-in sessions (only a hash of each session id) and recent failed sign-ins. |

## Stored files

Every file in Kosha's file store is in `files/`, whatever refers to it: file versions, previews, 3D viewer
models, import reports. A file's name is the SHA-256 of its content, so a file can be checked on its own:
`sha256sum files/ab/ab12...` must print its name. `versions.blob_sha` names the content of each file version.

## Index of file versions

`index/versions.jsonl` is a convenience for people leaving Kosha: one line per file version, joining `versions`,
`documents` and `folders`:

```json
{"path": "Kosha/Mixers/MX-100-020 Agitator Shaft.dxf", "document_id": 7, "filename": "MX-100-020 Agitator Shaft.dxf",
 "part_number": "MX-100-020", "state": "released", "deleted": false, "version": 3, "revision": "B",
 "sha256": "ab12...", "size": 48213, "created_at": "2026-10-13T09:30:00+00:00", "comment": "Keyway added",
 "file": "files/ab/ab12..."}
```

To rebuild a folder tree of the latest versions, take for each `document_id` the line with the highest `version`
(skip `"deleted": true` if you wish) and copy `file` to `path`.

## Manifest

```json
{
  "format": "kosha-export",
  "format_version": 1,
  "created_at": "2026-10-13T09:30:00+00:00",
  "kosha_version": "0.5.0.dev0",
  "database": "sqlite",
  "schema_migrations": ["m20261013_0900_core_baseline", "..."],
  "tables": [{"name": "documents", "file": "tables/documents.jsonl", "rows": 120, "sha256": "...",
              "columns": [{"name": "id", "type": "INTEGER", "notnull": false, "pk": true}, "..."],
              "sql": "CREATE TABLE documents (...)"}],
  "index": {"file": "index/versions.jsonl", "rows": 310, "sha256": "..."},
  "files": {"list": "files.jsonl", "list_sha256": "...", "count": 402, "bytes": 81234567},
  "problems": []
}
```

- `sha256` of a table, the index or the file list is the SHA-256 of that member's bytes; `rows` is its number of
  lines.
- `problems` lists anything the export could not include in full, such as a file the database refers to that was
  missing on the server. An export with problems is not complete; `--verify` reports them.

`--verify` checks the manifest's format and version, every table and the index against their checksums and row
counts, every stored file against its fingerprint and size, and that every file version in `versions` is among the
exported files.

## Compatibility

`format_version` changes only when a reader written for the old version would misread the new one. Adding tables,
columns or manifest keys does not change it, so readers should ignore what they do not know.
