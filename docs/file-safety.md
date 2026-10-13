# File safety

How Kosha keeps files whole on the way in, on the way out, and after someone deletes them.

## Uploads that resume

Files of 32 MB and more go up in 8 MB pieces. When the network drops, the browser waits, asks Kosha how much has
arrived and continues from there. Before the first piece it reads the file once and sends its SHA-256 fingerprint;
Kosha checks the whole file against it before anything is stored, so a damaged transfer is never kept as a good file
("arrived damaged ... upload the file again"). Then the virus scan, the store and the check-in run as for any upload.

Programs use the same calls (see [api-rules.md](api-rules.md)):

| Call | What it does |
|---|---|
| `POST /api/v1/uploads` `{filename, size, sha256}` | Starts an upload; answers with its `id` and `piece_size` |
| `PUT /api/v1/uploads/{id}?offset=N` | One piece as raw bytes (`Content-Type: application/octet-stream`, at most 16 MB) |
| `GET /api/v1/uploads/{id}` | How many bytes have arrived (`received`): continue from there |
| `POST /api/v1/uploads/{id}/finish` | Checks size and fingerprint, then adds the file to `folder_id` (and `path` below it) or checks it in over `document_id` |
| `DELETE /api/v1/uploads/{id}` | Gives up; the pieces are removed |

A piece sent again after a break is accepted; one sent past the end of what arrived is refused with `409
wrong_offset` and the `received` count. Unfinished uploads are removed after seven days.

Downloads (`GET /api/v1/documents/{id}/file`) accept `Range: bytes=...`, so a big assembly can resume too.

## Blocked types

Programs and scripts (EXE, DLL, MSI, BAT, CMD, PS1, VBS, JS, SCR, JAR, LNK, REG and similar) are refused on every way
in: uploads, check-ins, deviation attachments and the inputs library. Macro-enabled Office files (XLSM, DOCM) are
allowed because calculation sheets need them; the server never runs them. With `KOSHA_CLAMD` set, every file is
scanned before it is stored.

## Recycle bin

Deleting a file, or a folder with what is in it, sends the files to the recycle bin (Explorer › **Recycle bin**).
**Put back** returns a file to its folder, recreating the folder when it was deleted too. The person who deleted
it, its creator or an approver can put it back for `recycle_bin_days` (30 by default, set on the bin page); after
that an admin can. Nothing leaves the bin by itself. Released files cannot be deleted at all, only made obsolete,
and a folder holding one is not deleted.

## Purge

To remove a file's content for good (for example a customer drawing returned under a contract), an admin ticks it in
the bin, gives the reason and asks; **a second admin confirms**. The stored content goes when nothing else uses it;
the record, its history and the audit entries stay. Backups keep the file until they expire.

## Clean-up

Every six hours the server removes what never finished: temporary files of broken uploads (after a day), unfinished
resumable uploads (after seven days) and stored files no record refers to (after seven days). Those last ones are
moved to `orphans/` in the data folder and deleted 30 days later, so even a mistake can be undone. A file counts as
used when any record names it; nothing a user was told is safe is touched. Admins can run it now with
`POST /api/v1/maintenance/cleanup`.
