# Previews of files and the file add-ins

Kosha shows a preview of most files in its viewer, without the program that made them. Nothing in a file is run:
archives are listed, not unpacked; e-mails are read as text, never shown as web pages; Office files are read
through their XML with document type declarations refused. Long files are cut, and the preview says so.

| Files | What the viewer shows |
| --- | --- |
| DXF, DWG (with the converter), PDF, images, STL, SolidWorks (embedded picture) | The drawing, page, picture or 3D view, as before |
| Text: `.txt`, `.md`, `.log`, `.json`, `.xml`, `.ini`, `.yaml`, CNC programs (`.nc`, `.tap`, `.gcode`) | The first 2,000 lines |
| Tables: `.csv`, `.tsv` | The first 300 rows as a table |
| Word `.docx` | The paragraphs and table rows |
| Excel `.xlsx`, `.xlsm` | The first sheet's first 300 rows, and the names of the other sheets |
| PowerPoint `.pptx` | The text of each slide |
| ZIP archives | Every file inside with its size; unsafe paths and password-protected entries are marked |
| E-mails `.eml` | From, To, Cc, Date and Subject, the text, and the attachments' names and sizes |
| STEP `.step`, `.stp` | Who made it, with which program, the schema and the part and assembly names |
| Videos `.mp4`, `.webm`, `.ogv` | The video, played in the browser (it can be wound forward and back) |

A preview is made when the file is checked in. For files checked in before this version, **Admin › CAD conversion › Regenerate previews**
makes them.

## Add-ins about files

Three add-in kinds extend this (see the [SDK README](../sdk/python/README.md#file-types-storage-and-importers)):

- **File type**: previews for more formats. Kosha asks the add-in only for files it cannot show itself, through
  the add-in guard, so a slow or failing add-in never stops a check-in.
- **Storage copies**: a second copy of every stored file on another disk, a NAS or an object store. Kosha copies
  a part every hour (**Admin › Backups › Copies in storage add-ins** shows how far it is and **Copy now** runs it).
  A stored file that went missing is brought back from a copy, and the monthly file check repairs a damaged file
  from a copy when the backup folder has none. A copy is used only when its SHA-256 fingerprint matches.
- **Importer**: files and part data from another system. **Admin › Import legacy data › From another system**
  runs it as a normal import, with quality queues, review, sign-off and Undo batch.

**Admin › Add-ins › By kind** lists every kind, the add-ins of each kind that are installed, where they run, and
how to start a new one with the SDK.
