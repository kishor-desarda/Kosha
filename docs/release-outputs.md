# Release outputs

What Kosha makes when a file is released, so nobody has to export or stamp by hand (specification 7.3 and 8.3).

## Made at every release

- **Stamped PDF.** For a PDF, or a drawing or Office file whose PDF was made at check-in (by a CAD add-in or the
  converter), Kosha adds a green stamp at the top right of every page: *Released — Rev B — 10-Oct-2026 — approver*.
- **Release sheet.** One page that records the part number, description, revision, release date, approver, change
  order and comment, the part's material, make or buy and weight, and every file released with it with its version
  and SHA-256 fingerprint.

Both are stored as files linked to the revision and version they came from, so a PDF can never be mistaken for
another revision. They are listed on the file's page under **Release outputs**. Making them never blocks or undoes a
release: if one cannot be made (the PDF is damaged or protected, or pdfium is not installed) the tab says so, and an
approver can press **Make again**.

## When an output is opened or downloaded

- *Uncontrolled when printed — 13-Oct-2026* at the top left of every page, with the day of the download.
- The watermark line with the person's name and the company wording, when watermarks are on (see `security.md`).
- For an older revision, a red see-through band across the middle of every page: *Superseded — use Rev C*. The
  same band is added when an older version of a released PDF is downloaded from the Versions tab.

Every download of an output is in the audit log.
