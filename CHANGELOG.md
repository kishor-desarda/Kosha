# Changelog

All notable changes to Kosha are listed here, newest first. Versions follow [semantic versioning](https://semver.org/):
a release candidate (`rc`) is feature-complete and waits for pilot feedback before the final release.
The release workflow publishes the section for each tag as that release's notes.

## [Unreleased]

### Added

- **Manufacturing BOMs and process plans:** how the workshop builds each assembly, made from the engineering BOM:
  operations with work centres, set-up and run times and instructions; each part at the operation that uses it;
  sub-assemblies built with their parent; parts added for manufacturing. Checks against the engineering BOM down
  to every part, release after the engineering BOM, carry-over to the next revision, an Excel export for the
  workshop, the `mbom.released` event, and an ERP setting to send manufacturing BOMs with their operations
  (operations in ERPNext when switched on).

## [1.0.0rc1]

The first release candidate for 1.0: every area of the specification is built and covered by tests, including an
acceptance suite that walks through the specification's acceptance scenarios end to end.

### Added

- **Part numbers:** number formats from the specification, bulk reservation, voiding with a reason, and near
  matches shown before a new number is taken.
- **Items:** product families, spare-part flags, OEM cross-references and customer part numbers.
- **Access:** roles that stack, access rules by folder, category and customer, vendor accounts, and a regular
  access review for admins.
- **Lifecycle:** checks before review and release, "spares only" and superseded revisions.
- **Changes:** change requests, interchangeability, effectivity, approval routes, stand-ins and reminders.
- **API:** a versioned path, request IDs, one error format, `If-Match`, `Idempotency-Key`, cursor paging and
  limits; API keys for programs, signed webhooks and live updates on open screens.
- **Files:** resumable uploads, byte-range downloads, blocked program files, a recycle bin and a two-admin purge;
  previews for text, tables, archives, e-mails, Word, Excel, PowerPoint, STEP and video.
- **Security:** two-step sign-in, new-place alerts, a bulk-download alarm, an audit chain, personal-data
  export and erasure, watermarked PDF downloads, share links for outside people and encrypted secrets.
- **Health:** a monthly file fingerprint check with repair, a nightly consistency check and alerts to admins;
  drafts kept in the browser.
- **Release outputs:** a stamped PDF and a release sheet at each release, an "uncontrolled when printed" line
  and a superseded band.
- **BOMs:** latest released, in work, as of a date and as-built views, machine builds and a cost roll-up with
  what-if.
- **Transmittals:** vendor packages with a transmittal sheet and a superseded warning, Pack and Go, and
  projects from ERP sales orders.
- **ERP:** drawings attached to ERP items, HSN, OEM and status sent out, orders and customers read in,
  automatic retries, a nightly check and a field-mapping screen.
- **Workbench:** Ctrl+K, favourites, saved views, pinned pages, Undo, accent colour and text size.
- **Language:** Hinglish search, Indian number and date formats, translation files with Hindi, and F1 help.
- **Shop floor and learning:** a shop view with QR labels, a first-login tour and a practice folder.
- **Legacy migration:** import batches with quality queues, a review grid, an Excel round-trip, ZIP unpacking,
  search by old number, a count check, a sign-off and Undo batch.
- **AI (optional):** a model per job with a fallback, retries, a cost log and a monthly budget, local-only data,
  a drawing check, duplicate explanations, title-block reading and voice input.
- **Add-ins:** file-type, storage-copy and importer add-ins, and an add-in catalogue grouped by kind.
- **Releases:** a release workflow that checks readiness, runs the full CI, scans dependencies for known
  vulnerabilities, builds the packages and the server image with a software bill of materials (SBOM) and signed
  build provenance, and publishes a GitHub release. User and admin guides.

### Changed

- The server version is now `1.0.0rc1`. Add-ins that declare `kosha: ">=0.5,<2.0"` keep working.

## [0.5.0]

The preview: revision control, engineering BOMs, change orders, ERP connectors (ERPNext, Odoo, Dolibarr and file
exchange), the duplicate finder, legacy import, viewers, PostgreSQL, directory sign-in, the inputs library,
standard parts, industry packs, themes, optional AI assistance, the Office add-in, Kosha Desktop and the CAD
add-ins in preview.
